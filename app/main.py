from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import secrets
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import (
    dokumente,
    llm,
    meldungen,
    modelle,
    orchestrator,
    pruefarten,
    schemas,
    sicherheit,
    sicherung,
    tresor,
    werkzeuge,
)
from .config import get_settings
from .crypto import check_ready, decrypt as server_entschluesseln
from .db import SessionLocal, get_session, init_db
from .events import bus
from .models import (
    AccessRequest,
    Agent,
    Credential,
    Forum,
    Gelesen,
    Mitglied,
    Participant,
    Post,
    Thread,
    Uebersetzung,
    User,
    Wissen,
    as_utc,
    utcnow,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agora")

# Sichtbar unter /healthz - damit man ohne Anmeldung pruefen kann, welcher
# Stand tatsaechlich laeuft.
APP_VERSION = "0.32.0"
COOKIE_NAME = "agora_token"
# So lange gilt ein Einmal-Token. Kurz gehalten: es geht durch fremde Haende
# (Mail, Chat) und soll nicht tagelang herumliegen.
EINMAL_STUNDEN = 48
# Nach so vielen falschen PINs ist fuer SPERRE_MINUTEN Schluss. Eine PIN hat
# wenig Entropie; ohne Bremse waere sie in Minuten durchprobiert.
MAX_FEHLVERSUCHE = 5
SPERRE_MINUTEN = 15
# Notbremse gegen Spam auf dem offenen Antragsformular.
MAX_OFFENE_ANTRAEGE = 50

STATIC_DIR = Path(__file__).parent / "static"
HEARTBEAT_SECONDS = 15.0

_worker_stop = asyncio.Event()
_worker_task: asyncio.Task | None = None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Fehlkonfigurierte Geheimnisse sollen den Start verhindern, nicht erst
    # Wochen spaeter den ersten Modell-Zugang.
    check_ready()
    await init_db()
    await _bootstrap_admin()
    await bus.start()

    global _worker_task
    # Zuruecksetzen, sonst beendet sich ein neuer Worker sofort wieder: das
    # Signal bleibt vom letzten Herunterfahren gesetzt. Im Betrieb laeuft
    # genau ein Lebenszyklus je Prozess, im Test koennen es mehrere sein.
    _worker_stop.clear()
    if settings.worker_enabled:
        _worker_task = asyncio.create_task(orchestrator.worker_loop(_worker_stop))
    else:
        log.info("Worker in diesem Prozess deaktiviert (AGORA_WORKER_ENABLED=false)")

    try:
        yield
    finally:
        _worker_stop.set()
        if _worker_task is not None:
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(_worker_task, timeout=10)
        await bus.stop()


app = FastAPI(title="Agora - Agentenforum", version="0.1.0", lifespan=lifespan)


@app.exception_handler(HTTPException)
async def fehler_uebersetzen(request: Request, exc: HTTPException):
    """Meldungen in der Sprache der Oberflaeche ausliefern.

    Der Quelltext bleibt deutsch; uebersetzt wird genau hier, an einer Stelle.
    Die Oberflaeche schickt ihre Sprache in X-Agora-Sprache mit.
    """
    sprache = request.headers.get("x-agora-sprache", "de").lower()[:2]
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": meldungen.uebersetze(exc.detail, sprache)},
        headers=getattr(exc, "headers", None),
    )


async def _bootstrap_admin() -> None:
    settings = get_settings()
    if not settings.admin_token:
        log.warning("AGORA_ADMIN_TOKEN ist leer - es wird kein Admin angelegt.")
        return
    async with SessionLocal() as session:
        existing = (
            await session.execute(select(User).where(User.name == "admin"))
        ).scalar_one_or_none()
        token_hash = hash_token(settings.admin_token)
        if existing is None:
            session.add(User(name="admin", token_hash=token_hash, is_admin=True))
            log.info("Admin-User angelegt.")
        elif existing.token_hash != token_hash:
            existing.token_hash = token_hash
            log.info("Admin-Token aus der Umgebung uebernommen.")
        await session.commit()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def _user_out(user: User) -> schemas.UserOut:
    return schemas.UserOut(
        id=user.id,
        name=user.name,
        is_admin=user.is_admin,
        has_pin=bool(user.pin_hash),
        unlocked_until=user.unlocked_until,
        teilnahme_stunden=user.teilnahme_stunden or 0,
    )


async def user_by_token(session: AsyncSession, token: str) -> User | None:
    if not token:
        return None
    return (
        await session.execute(select(User).where(User.token_hash == hash_token(token.strip())))
    ).scalar_one_or_none()


async def current_user(
    agora_token: str = Cookie(default="", alias=COOKIE_NAME),
    authorization: str = Header(default=""),
    x_agora_token: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> User:
    # Drei Wege, absteigend nach Verlaesslichkeit. Cookies reicht jede
    # vorgelagerte Authentifizierungs-Middleware durch; den
    # Authorization-Header beanspruchen manche fuer sich und ueberschreiben ihn.
    token = agora_token or x_agora_token
    if not token and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
    if not token.strip():
        raise HTTPException(401, "Kein Token uebermittelt (Cookie, X-Agora-Token oder Bearer)")
    user = await user_by_token(session, token)
    if user is None:
        raise HTTPException(401, "Unbekanntes Token")
    if user.one_time:
        # Ein Einmal-Token taugt ausschliesslich zum Anmelden. Sonst koennte
        # es jemand dauerhaft benutzen, ohne es je einzuloesen - und genau das
        # soll es nicht geben.
        raise HTTPException(401, "Einmal-Token: bitte zuerst ueber die Anmeldung einloesen.")
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Nur fuer Admins")
    return user


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------
@app.post("/api/login", response_model=schemas.LoginOut)
async def login(
    payload: schemas.LoginIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """Token gegen ein Cookie tauschen.

    Der Umweg lohnt sich, weil Cookies unveraendert durch vorgelagerte
    Authentifizierungs-Schichten kommen - Header nicht zwingend.
    """
    user = await user_by_token(session, payload.token)
    if user is None:
        raise HTTPException(401, "Unbekanntes Token")

    if user.locked_until is not None and as_utc(user.locked_until) > utcnow():
        raise HTTPException(
            429, f"Zu viele Fehlversuche. Naechster Versuch in {SPERRE_MINUTEN} Minuten."
        )

    token = payload.token.strip()
    neues_token: str | None = None

    if user.one_time:
        ablauf = user.one_time_expires_at
        if ablauf is not None and as_utc(ablauf) < utcnow():
            raise HTTPException(
                401, "Das Einmal-Token ist abgelaufen - bitte einen neuen Zugang anfragen."
            )

        if user.pin_hash:
            # Der Admin hat nur das Token erneuert; die PIN bleibt und muss
            # stimmen. Genau daran scheitert eine Uebernahme.
            if not sicherheit.stimmt(payload.pin, user.pin_hash):
                await _fehlversuch(session, user)
                raise HTTPException(401, "PIN stimmt nicht.")
        else:
            # Erste Anmeldung: die Person vergibt ihre PIN selbst, und dabei
            # entsteht ihr Datenschluessel.
            try:
                user.pin_hash = sicherheit.verschluesseln(payload.pin)
            except sicherheit.PinFehler as exc:
                raise HTTPException(400, str(exc)) from exc
            user.dk_salt, user.dk_wrapped = tresor.neuer_datenschluessel(payload.pin)

        # Einloesen: ab hier kennt nur die Person selbst ihr Token. Wer das
        # Einmal-Token ausgegeben hat, kommt damit nicht mehr hinein.
        neues_token = secrets.token_urlsafe(32)
        user.token_hash = hash_token(neues_token)
        user.one_time = False
        user.one_time_expires_at = None
        token = neues_token
        log.info("Einmal-Token von %s eingeloest", user.name)

    elif user.pin_hash:
        if not sicherheit.stimmt(payload.pin, user.pin_hash):
            await _fehlversuch(session, user)
            raise HTTPException(401, "PIN stimmt nicht.")

    user.failed_logins = 0
    user.locked_until = None

    # Freischalten: der Datenschluessel wird geoeffnet und fuer eine begrenzte
    # Zeit hinterlegt, damit der Worker ihn in seinem eigenen Prozess benutzen
    # kann. Danach ist er wieder fort.
    if user.dk_wrapped:
        try:
            dk = tresor.auspacken(payload.pin, user.dk_salt, user.dk_wrapped)
        except tresor.GesperrtFehler as exc:
            raise HTTPException(401, str(exc)) from exc
        stunden = get_settings().freischaltung_stunden
        user.dk_unlocked, user.unlocked_until = tresor.hinterlegen(dk, stunden)
        await _zugaenge_umstellen(session, user, dk)
        await session.commit()
        await _gesperrte_threads_wecken(session, user)
    else:
        await session.commit()

    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=60 * 60 * 24 * 30,
        path="/",
    )
    return schemas.LoginOut(
        **_user_out(user).model_dump(), new_token=neues_token
    )


GESPERRT_HINWEIS = "Zugang gesperrt"


async def _zugaenge_umstellen(session: AsyncSession, user: User, dk: bytes) -> None:
    """Alte, mit dem Serverschluessel verschluesselte Zugaenge umziehen.

    Einmalig beim ersten Freischalten nach der Umstellung. Danach haengen sie
    am Datenschluessel der Person und sind ohne deren PIN nicht mehr lesbar.
    """
    alte = (
        (
            await session.execute(
                select(Credential).where(
                    Credential.owner_id == user.id,
                    (Credential.enc_scheme.is_(None)) | (Credential.enc_scheme != "user"),
                )
            )
        )
        .scalars()
        .all()
    )
    for zugang in alte:
        if zugang.api_key_enc:
            zugang.api_key_enc = tresor.zugang_verschluesseln(
                dk, server_entschluesseln(zugang.api_key_enc)
            )
        if zugang.extra_json_enc:
            zugang.extra_json_enc = tresor.zugang_verschluesseln(
                dk, server_entschluesseln(zugang.extra_json_enc)
            )
        zugang.enc_scheme = "user"
    if alte:
        log.info("%d Zugaenge von %s auf den Datenschluessel umgestellt", len(alte), user.name)


async def _gesperrte_threads_wecken(session: AsyncSession, user: User) -> None:
    """Threads, die nur wegen der Sperre pausieren, wieder anstossen."""
    eigene = select(Agent.id).where(Agent.owner_id == user.id)
    betroffen = (
        (
            await session.execute(
                select(Thread)
                .join(Participant, Participant.thread_id == Thread.id)
                .where(
                    Thread.status == "paused",
                    Thread.status_detail.startswith(GESPERRT_HINWEIS),
                    Participant.agent_id.in_(eigene),
                )
            )
        )
        .scalars()
        .unique()
        .all()
    )
    for thread in betroffen:
        thread.status = "active"
        thread.status_detail = f"{user.name} hat die Modell-Zugaenge freigeschaltet."
        thread.next_run_at = utcnow()
        thread.locked_until = None
        thread.locked_by = None
    if betroffen:
        await session.commit()
        for thread in betroffen:
            await orchestrator.publish_thread(thread)


@app.post("/api/me/lock", response_model=schemas.UserOut)
async def lock_now(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    """Modell-Zugaenge sofort wieder sperren."""
    user.dk_unlocked = None
    user.unlocked_until = None
    await session.commit()
    return _user_out(user)


@app.patch("/api/me", response_model=schemas.UserOut)
async def update_me(
    payload: schemas.MeUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    user.teilnahme_stunden = payload.teilnahme_stunden
    await session.commit()
    return _user_out(user)


async def _fehlversuch(session: AsyncSession, user: User) -> None:
    user.failed_logins = (user.failed_logins or 0) + 1
    if user.failed_logins >= MAX_FEHLVERSUCHE:
        user.locked_until = utcnow() + timedelta(minutes=SPERRE_MINUTEN)
        user.failed_logins = 0
        log.warning("Konto %s nach zu vielen Fehlversuchen gesperrt", user.name)
    await session.commit()


@app.post("/api/me/pin", response_model=schemas.UserOut)
async def change_pin(
    payload: schemas.PinChange,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """PIN setzen oder aendern - nur mit der bisherigen."""
    if user.pin_hash and not sicherheit.stimmt(payload.alte_pin, user.pin_hash):
        await _fehlversuch(session, user)
        raise HTTPException(401, "Die bisherige PIN stimmt nicht.")
    try:
        neuer_hash = sicherheit.verschluesseln(payload.neue_pin)
    except sicherheit.PinFehler as exc:
        raise HTTPException(400, str(exc)) from exc

    # Der Datenschluessel bleibt derselbe, er wird nur neu verpackt - sonst
    # waeren alle Modell-Zugaenge nach einem PIN-Wechsel verloren.
    if user.dk_wrapped:
        try:
            user.dk_salt, user.dk_wrapped = tresor.umpacken(
                payload.alte_pin, payload.neue_pin, user.dk_salt, user.dk_wrapped
            )
        except tresor.GesperrtFehler as exc:
            raise HTTPException(401, str(exc)) from exc
    else:
        user.dk_salt, user.dk_wrapped = tresor.neuer_datenschluessel(payload.neue_pin)

    user.pin_hash = neuer_hash
    user.failed_logins = 0
    user.locked_until = None

    # Gleich freischalten: die PIN liegt hier ohnehin vor, und sonst muesste
    # man sich nach dem Setzen erst neu anmelden, um etwas anlegen zu koennen.
    dk = tresor.auspacken(payload.neue_pin, user.dk_salt, user.dk_wrapped)
    user.dk_unlocked, user.unlocked_until = tresor.hinterlegen(
        dk, get_settings().freischaltung_stunden
    )
    await _zugaenge_umstellen(session, user, dk)
    await session.commit()
    return _user_out(user)


@app.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/me", response_model=schemas.UserOut)
async def get_me(user: User = Depends(current_user)):
    return _user_out(user)


@app.get("/api/users", response_model=list[schemas.UserOut])
async def list_users(
    _: User = Depends(admin_user), session: AsyncSession = Depends(get_session)
):
    return (await session.execute(select(User).order_by(User.name))).scalars().all()


@app.get("/api/personen", response_model=list[schemas.MitgliedOut])
async def list_personen(
    _: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    """Nur Kennung und Name - fuer die Einladung in ein geschlossenes Forum.

    /api/users bleibt Admins vorbehalten, weil dort der Zustand der Konten
    haengt: Rechte, PIN gesetzt, freigeschaltet bis wann. Hier steht nichts
    davon. Ohne diese Liste koennte niemand ausser einem Admin jemanden in
    seinen eigenen Raum holen - und ein Admin auch nicht, denn er sieht den
    Raum ja gerade nicht. Der Raum bliebe fuer immer einsam.

    Namen sind im Forum ohnehin kein Geheimnis: sie stehen an jedem Beitrag.
    """
    zeilen = (
        await session.execute(select(User.id, User.name).order_by(User.name))
    ).all()
    return [schemas.MitgliedOut(user_id=i, name=n) for i, n in zeilen]


@app.post("/api/users", response_model=schemas.UserCreated, status_code=201)
async def create_user(
    payload: schemas.UserCreate,
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_session),
):
    exists = (
        await session.execute(select(User).where(User.name == payload.name))
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(409, "Name schon vergeben")
    return await _nutzer_anlegen(session, payload.name, payload.is_admin)


async def _nutzer_anlegen(
    session: AsyncSession, name: str, is_admin: bool
) -> schemas.UserCreated:
    """Legt eine Person mit Einmal-Token an.

    Das ausgegebene Token taugt nur fuer die erste Anmeldung; dabei tauscht
    die Anwendung es gegen eines, das niemand sonst kennt. Deshalb kommt auch
    ein Admin spaeter nicht an die hinterlegten Modell-Zugaenge.
    """
    token = secrets.token_urlsafe(32)
    ablauf = utcnow() + timedelta(hours=EINMAL_STUNDEN)
    user = User(
        name=name,
        token_hash=hash_token(token),
        is_admin=is_admin,
        one_time=True,
        one_time_expires_at=ablauf,
    )
    session.add(user)
    await session.commit()
    return schemas.UserCreated(
        id=user.id, name=user.name, is_admin=user.is_admin, token=token, expires_at=ablauf
    )


@app.patch("/api/users/{user_id}", response_model=schemas.UserOut)
async def set_user_rechte(
    user_id: str,
    payload: schemas.UserRechte,
    admin: User = Depends(admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Verwaltungsrechte geben oder nehmen.

    Der eigentliche Zweck: das Installationskonto entmachten. Es laesst sich
    nicht loeschen, weil der naechste Start es aus AGORA_ADMIN_TOKEN wieder
    anlegt - aber sobald es ein eigenes Admin-Konto gibt, braucht es seine
    Rechte nicht mehr und wird zum blossen Notschluessel.

    Zwei Sperren: niemand nimmt sich selbst die Rechte (das waere die Tuer
    hinter sich zuziehen), und der letzte Admin bleibt Admin.
    """
    person = await session.get(User, user_id)
    if person is None:
        raise HTTPException(404, "Person nicht gefunden")
    if person.id == admin.id:
        raise HTTPException(400, "Die eigenen Rechte kann man nicht aendern.")
    if person.is_admin and not payload.is_admin:
        uebrig = (
            await session.execute(
                select(func.count())
                .select_from(User)
                .where(User.is_admin.is_(True), User.id != person.id)
            )
        ).scalar_one()
        if uebrig == 0:
            raise HTTPException(400, "Das Forum braucht mindestens einen Admin.")

    person.is_admin = payload.is_admin
    await session.commit()
    log.warning(
        "Admin %s hat %s die Verwaltungsrechte %s",
        admin.name, person.name, "gegeben" if payload.is_admin else "genommen",
    )
    return person


@app.delete("/api/users/{user_id}", status_code=200)
async def delete_user(
    user_id: str,
    admin: User = Depends(admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Eine Person entfernen - ihre Themen bleiben.

    Der Punkt, um den sich alles dreht: threads.creator_id haengt mit
    ON DELETE CASCADE an der Person. Wer sie einfach loescht, loescht
    damit JEDE von ihr eroeffnete Diskussion samt aller Beitraege darin -
    auch der Beitraege anderer Leute. Deshalb werden die Themen vorher
    uebergeben.

    Was mitgeht, ist persoenlich und ohne die Person ohnehin wertlos: ihre
    Modell-Zugaenge (nur mit ihrer PIN zu oeffnen) und ihre Agenten. Die
    Beitraege dieser Agenten bleiben stehen - posts.agent_id ist bewusst
    kein Fremdschluessel, und der Name steht am Beitrag.
    """
    person = await session.get(User, user_id)
    if person is None:
        raise HTTPException(404, "Person nicht gefunden")
    if person.id == admin.id:
        raise HTTPException(400, "Sich selbst kann man nicht loeschen.")
    if person.name == "admin":
        raise HTTPException(
            400,
            "Das Installationskonto laesst sich nicht loeschen - der naechste "
            "Start legt es aus AGORA_ADMIN_TOKEN ohnehin wieder an. Nimm ihm "
            "stattdessen die Verwaltungsrechte, sobald du ein eigenes "
            "Admin-Konto hast.",
        )

    # Themen uebergeben, statt sie mitzureissen - aber NICHT die aus
    # geschlossenen Foren. Sonst waere das Loeschen einer Person der Weg,
    # als Admin an einen privaten Raum zu kommen: erst den Besitzer
    # entfernen, dann seine Themen erben. Ein geschlossener Raum bleibt
    # deshalb bei denen, die ohnehin darin sind; ist niemand mehr uebrig,
    # geht er ganz.
    geschlossen_mit = {
        f_id
        for f_id, in (
            await session.execute(
                select(Forum.id).where(Forum.sichtbar == "geschlossen")
            )
        ).all()
    }
    # Auch was unter einem geschlossenen Forum haengt, ist geschlossen.
    for oben in list(geschlossen_mit):
        geschlossen_mit.update(await _forum_mit_unterforen(session, oben))

    uebergeben = (
        await session.execute(
            update(Thread)
            .where(
                Thread.creator_id == person.id,
                Thread.forum_id.is_(None) | Thread.forum_id.not_in(geschlossen_mit or [""]),
            )
            .values(creator_id=admin.id)
        )
    ).rowcount

    # Geschlossene Raeume, in denen die Person war: an ein anderes Mitglied
    # oder weg.
    raeume = (
        await session.execute(
            select(Mitglied.forum_id).where(Mitglied.user_id == person.id)
        )
    ).scalars().all()
    verwaist = 0
    for raum in raeume:
        erbe = (
            await session.execute(
                select(Mitglied.user_id)
                .where(Mitglied.forum_id == raum, Mitglied.user_id != person.id)
                .order_by(Mitglied.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        aeste = await _forum_mit_unterforen(session, raum)
        if erbe is None:
            # Niemand mehr drin. Ein Forum, das keiner mehr oeffnen kann,
            # ist keine Sicherung, sondern unerreichbarer Ballast - und der
            # Admin soll es gerade nicht aufmachen duerfen. Also weg.
            await session.execute(delete(Thread).where(Thread.forum_id.in_(aeste)))
            forum = await session.get(Forum, raum)
            if forum is not None:
                await session.delete(forum)
            verwaist += 1
        else:
            await session.execute(
                update(Thread)
                .where(Thread.creator_id == person.id, Thread.forum_id.in_(aeste))
                .values(creator_id=erbe)
            )

    agenten = (
        await session.execute(select(Agent).where(Agent.owner_id == person.id))
    ).scalars().all()
    zugaenge = (
        await session.execute(select(Credential).where(Credential.owner_id == person.id))
    ).scalars().all()

    await session.delete(person)
    await session.commit()

    log.warning(
        "Admin %s hat %s geloescht (%d Themen uebernommen, %d Agenten, "
        "%d Zugaenge, %d verwaiste geschlossene Foren entfernt)",
        admin.name, person.name, uebergeben, len(agenten), len(zugaenge), verwaist,
    )
    return {
        "geloescht": person.name,
        "themen_uebernommen": uebergeben,
        "agenten_entfernt": len(agenten),
        "zugaenge_entfernt": len(zugaenge),
        "raeume_entfernt": verwaist,
    }


@app.post("/api/users/{user_id}/reset", response_model=schemas.UserCreated)
async def reset_user_token(
    user_id: str,
    payload: schemas.ResetIn,
    admin: User = Depends(admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Neues Einmal-Token ausgeben, wenn jemand seines verloren hat.

    Die PIN bleibt dabei bestehen - ein Admin kommt mit dem neuen Token also
    nicht hinein. Wer auch die PIN vergessen hat, braucht
    pin_zuruecksetzen=true; dann faellt die PIN weg, und damit dieser Weg
    keine Uebernahme wird, faellt zugleich jeder Modell-Zugang der Person.
    """
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Nicht gefunden")

    if payload.pin_zuruecksetzen:
        zugaenge = (
            (await session.execute(select(Credential).where(Credential.owner_id == user.id)))
            .scalars()
            .all()
        )
        for zugang in zugaenge:
            await session.execute(
                update(Agent)
                .where(Agent.credential_id == zugang.id)
                .values(credential_id=None)
            )
            await session.delete(zugang)
        user.pin_hash = None
        user.dk_salt = None
        user.dk_wrapped = None
        user.dk_unlocked = None
        user.unlocked_until = None
        log.warning(
            "Admin %s hat PIN von %s zurueckgesetzt, %d Modell-Zugaenge geloescht",
            admin.name,
            user.name,
            len(zugaenge),
        )

    token = secrets.token_urlsafe(32)
    ablauf = utcnow() + timedelta(hours=EINMAL_STUNDEN)
    user.token_hash = hash_token(token)
    user.one_time = True
    user.one_time_expires_at = ablauf
    await session.commit()
    log.warning("Admin %s hat das Token von %s zurueckgesetzt", admin.name, user.name)
    return schemas.UserCreated(
        id=user.id, name=user.name, is_admin=user.is_admin, token=token, expires_at=ablauf
    )


# ---------------------------------------------------------------------------
# Zugangsantraege
# ---------------------------------------------------------------------------
@app.post("/api/requests", response_model=schemas.RequestOut, status_code=201)
async def create_request(
    payload: schemas.RequestCreate,
    session: AsyncSession = Depends(get_session),
):
    """Ohne Anmeldung erreichbar: hier traegt sich ein, wer einen Zugang moechte."""
    if not get_settings().antraege_offen:
        raise HTTPException(
            403, "Selbstregistrierung ist abgeschaltet - wende dich an die Betreiber."
        )
    name = payload.name.strip()
    schon_da = (
        await session.execute(select(User).where(User.name == name))
    ).scalar_one_or_none()
    if schon_da is not None:
        raise HTTPException(409, "Diesen Namen gibt es schon - frag nach einem neuen Token.")

    offen = (
        await session.execute(
            select(AccessRequest).where(
                AccessRequest.name == name, AccessRequest.status == "offen"
            )
        )
    ).scalar_one_or_none()
    if offen is not None:
        raise HTTPException(409, "Fuer diesen Namen laeuft schon ein Antrag.")

    anzahl = len(
        (
            await session.execute(
                select(AccessRequest.id).where(AccessRequest.status == "offen")
            )
        ).all()
    )
    if anzahl >= MAX_OFFENE_ANTRAEGE:
        raise HTTPException(429, "Zu viele offene Antraege - bitte spaeter noch einmal.")

    antrag = AccessRequest(
        name=name, contact=payload.contact.strip() or None, note=payload.note.strip()
    )
    session.add(antrag)
    await session.commit()
    log.info("Neuer Zugangsantrag von %s", name)
    return antrag


@app.get("/api/requests", response_model=list[schemas.RequestOut])
async def list_requests(
    _: User = Depends(admin_user), session: AsyncSession = Depends(get_session)
):
    return (
        (await session.execute(select(AccessRequest).order_by(AccessRequest.created_at.desc())))
        .scalars()
        .all()
    )


@app.post("/api/requests/{request_id}/approve", response_model=schemas.UserCreated)
async def approve_request(
    request_id: str,
    admin: User = Depends(admin_user),
    session: AsyncSession = Depends(get_session),
):
    antrag = await session.get(AccessRequest, request_id)
    if antrag is None:
        raise HTTPException(404, "Antrag nicht gefunden")
    if antrag.status != "offen":
        raise HTTPException(409, f"Der Antrag ist bereits {antrag.status}.")
    schon_da = (
        await session.execute(select(User).where(User.name == antrag.name))
    ).scalar_one_or_none()
    if schon_da is not None:
        raise HTTPException(409, "Diesen Namen gibt es inzwischen schon.")

    angelegt = await _nutzer_anlegen(session, antrag.name, is_admin=False)
    antrag.status = "freigegeben"
    antrag.decided_by = admin.name
    antrag.decided_at = utcnow()
    antrag.user_id = angelegt.id
    await session.commit()
    return angelegt


@app.post("/api/requests/{request_id}/reject", response_model=schemas.RequestOut)
async def reject_request(
    request_id: str,
    admin: User = Depends(admin_user),
    session: AsyncSession = Depends(get_session),
):
    antrag = await session.get(AccessRequest, request_id)
    if antrag is None:
        raise HTTPException(404, "Antrag nicht gefunden")
    if antrag.status != "offen":
        raise HTTPException(409, f"Der Antrag ist bereits {antrag.status}.")
    antrag.status = "abgelehnt"
    antrag.decided_by = admin.name
    antrag.decided_at = utcnow()
    await session.commit()
    return antrag


# ---------------------------------------------------------------------------
# Sicherung
# ---------------------------------------------------------------------------
@app.post("/api/admin/sicherung/erstellen")
async def sicherung_erstellen(
    payload: schemas.SicherungAnfrage,
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Das ganze Forum als eine Datei.

    Sie geht an den Browser und nicht in einen Pfad auf dem Server: wohin
    gesichert wird, entscheidet die Person am Bildschirm, und der Server soll
    keine Pfade von aussen entgegennehmen.

    POST statt GET wegen des Passworts - in einer URL stuende es im
    Zugriffsprotokoll jedes Proxys davor.
    """
    abbild, kopf = await sicherung.erstellen(session)

    geschuetzt = bool(payload.passwort.strip())
    if geschuetzt:
        try:
            abbild = sicherung.verschluesseln(abbild, payload.passwort)
        except sicherung.SicherungFehler as exc:
            raise HTTPException(400, str(exc)) from exc

    log.info("Sicherung erstellt (%s): %s",
             "mit Passwort" if geschuetzt else "ohne Passwort", kopf["zaehler"])
    return Response(
        content=abbild,
        media_type="application/octet-stream" if geschuetzt else "application/gzip",
        headers={
            "Content-Disposition":
                f'attachment; filename="{sicherung.dateiname(geschuetzt)}"',
        },
    )


def _oeffnen(daten: bytes, passwort: str):
    """Gemeinsamer Weg fuer Pruefen und Einspielen."""
    try:
        return sicherung.lesen(daten, passwort or None)
    except sicherung.PasswortFehlt:
        return None
    except sicherung.PasswortFalsch as exc:
        raise HTTPException(400, str(exc)) from exc
    except sicherung.SicherungFehler as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/admin/sicherung/pruefen")
async def sicherung_pruefen(
    datei: UploadFile = File(...),
    passwort: str = Form(default=""),
    _: User = Depends(admin_user),
):
    """Hineinschauen, ohne etwas zu veraendern.

    Einspielen loescht den gesamten Bestand. Vorher soll man sehen koennen,
    was man da einspielt. Liegt ein Passwort darauf und fehlt es, ist das
    keine Stoerung, sondern eine Rueckfrage - die Oberflaeche fragt danach.
    """
    geoeffnet = _oeffnen(await datei.read(), passwort)
    if geoeffnet is None:
        return {"passwort_noetig": True}
    kopf, _tabellen = geoeffnet
    return kopf


@app.post("/api/admin/sicherung/einspielen")
async def sicherung_einspielen(
    datei: UploadFile = File(...),
    passwort: str = Form(default=""),
    bestaetigt: bool = Form(default=False),
    admin: User = Depends(admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Den gesamten Bestand durch das Abbild ersetzen.

    Alles Bisherige ist danach fort - deshalb die ausdrueckliche Bestaetigung.
    Hinterher wird der Admin aus der Umgebung neu gesetzt: sonst gilt nur noch
    das Token aus dem Abbild, und auf einem frisch aufgesetzten Server waere
    niemand mehr drin.
    """
    if not bestaetigt:
        raise HTTPException(400, "Einspielen loescht den Bestand - bitte bestaetigen.")

    geoeffnet = _oeffnen(await datei.read(), passwort)
    if geoeffnet is None:
        raise HTTPException(400, "Dieses Abbild ist mit einem Passwort geschuetzt.")
    kopf, tabellen = geoeffnet

    log.warning("Admin %s spielt ein Abbild ein (%s)", admin.name, kopf.get("erstellt_am"))
    geschrieben = await sicherung.einspielen(session, tabellen)
    await session.commit()

    await _bootstrap_admin()
    return {
        "kopf": kopf,
        "geschrieben": geschrieben,
        # Nach dem Einspielen ist jede Freischaltung fort: alle melden sich
        # neu an und geben ihre PIN ein.
        "hinweis": "Eingespielt. Alle muessen sich neu anmelden.",
    }


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
def _credential_out(cred: Credential) -> schemas.CredentialOut:
    return schemas.CredentialOut(
        id=cred.id,
        label=cred.label,
        provider=cred.provider,
        api_base=cred.api_base,
        has_key=bool(cred.api_key_enc),
        auth_style=cred.auth_style or "api_key",
        header_name=cred.header_name,
        has_extra=bool(cred.extra_json_enc),
    )


@app.get("/api/credentials", response_model=list[schemas.CredentialOut])
async def list_credentials(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    rows = (
        (
            await session.execute(
                select(Credential).where(Credential.owner_id == user.id).order_by(Credential.label)
            )
        )
        .scalars()
        .all()
    )
    return [_credential_out(row) for row in rows]


@app.post("/api/credentials", response_model=schemas.CredentialOut, status_code=201)
async def create_credential(
    payload: schemas.CredentialCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    if payload.auth_style not in ("api_key", "bearer", "header", "none"):
        raise HTTPException(400, "auth_style muss api_key, bearer, header oder none sein")
    if payload.extra_json:
        try:
            geparst = json.loads(payload.extra_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, f"Zusatzparameter sind kein gueltiges JSON: {exc}") from exc
        if not isinstance(geparst, dict):
            raise HTTPException(400, "Zusatzparameter muessen ein JSON-Objekt sein")

    try:
        dk = tresor.abholen(user.dk_unlocked, user.unlocked_until)
    except tresor.GesperrtFehler as exc:
        raise HTTPException(
            409, f"{exc} Melde dich mit deiner PIN an, dann kannst du Zugaenge anlegen."
        ) from exc

    cred = Credential(
        owner_id=user.id,
        label=payload.label,
        provider=payload.provider.strip().strip("/"),
        api_key_enc=tresor.zugang_verschluesseln(dk, payload.api_key) if payload.api_key else None,
        api_base=payload.api_base or None,
        auth_style=payload.auth_style,
        header_name=payload.header_name or None,
        extra_json_enc=(
            tresor.zugang_verschluesseln(dk, payload.extra_json) if payload.extra_json else None
        ),
        enc_scheme="user",
    )
    session.add(cred)
    await session.commit()
    return _credential_out(cred)


@app.delete("/api/credentials/{credential_id}", status_code=204)
async def delete_credential(
    credential_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    cred = await session.get(Credential, credential_id)
    if cred is None or cred.owner_id != user.id:
        raise HTTPException(404, "Nicht gefunden")
    await session.delete(cred)
    await session.commit()


@app.get("/api/credentials/{credential_id}/modelle")
async def modelle_auflisten(
    credential_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Welche Modelle bietet dieser Endpunkt an?

    Eine Bequemlichkeit beim Anlegen eines Agenten - wer den Namen kennt,
    schreibt ihn weiter von Hand. Deshalb kommt ein Fehlschlag als lesbarer
    Satz zurueck und nicht als Absturz.
    """
    cred = await session.get(Credential, credential_id)
    if cred is None or cred.owner_id != user.id:
        raise HTTPException(404, "Zugang nicht gefunden")

    try:
        dk = tresor.abholen(user.dk_unlocked, user.unlocked_until)
    except tresor.GesperrtFehler as exc:
        raise HTTPException(
            409, f"{exc} Melde dich mit deiner PIN an, dann sehe ich im Endpunkt nach."
        ) from exc

    # Dieselbe Entschluesselung wie beim Modellaufruf - nicht nachgebaut,
    # sonst laufen die beiden Wege irgendwann auseinander.
    try:
        schluessel = llm._geheimnis(cred.api_key_enc, cred.enc_scheme, dk)
    except (llm.LLMError, RuntimeError) as exc:
        raise HTTPException(409, f"Schluessel unlesbar: {exc}") from exc

    try:
        namen = await modelle.auflisten(cred, schluessel)
    except modelle.ModellFehler as exc:
        raise HTTPException(502, str(exc)) from exc

    log.info("Modell-Liste fuer '%s': %d Eintraege", cred.label, len(namen))
    return {"modelle": namen}



# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------
@app.get("/api/agents", response_model=list[schemas.AgentOut])
async def list_agents(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    gefunden = (
        (
            await session.execute(
                select(Agent)
                .where((Agent.owner_id == user.id) | (Agent.is_public.is_(True)))
                .order_by(Agent.name)
            )
        )
        .scalars()
        .all()
    )
    return await _mit_aktenvermerk(session, list(gefunden))


@app.post("/api/agents", response_model=schemas.AgentOut, status_code=201)
async def create_agent(
    payload: schemas.AgentCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    if payload.credential_id:
        cred = await session.get(Credential, payload.credential_id)
        if cred is None or cred.owner_id != user.id:
            raise HTTPException(400, "Credential gehoert dir nicht")
    agent = Agent(owner_id=user.id, **payload.model_dump())
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


@app.patch("/api/agents/{agent_id}", response_model=schemas.AgentOut)
async def update_agent(
    agent_id: str,
    payload: schemas.AgentUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.owner_id != user.id:
        raise HTTPException(404, "Nicht gefunden")
    werte = payload.model_dump(exclude_unset=True)
    # Beim Anlegen wird das geprueft, beim Aendern muss es genauso gelten -
    # sonst liesse sich ein fremder Zugang unterschieben und auf fremde
    # Kosten nutzen.
    if werte.get("credential_id"):
        cred = await session.get(Credential, werte["credential_id"])
        if cred is None or cred.owner_id != user.id:
            raise HTTPException(400, "Credential gehoert dir nicht")
    for key, value in werte.items():
        setattr(agent, key, value)
    await session.commit()
    await session.refresh(agent)
    return agent


async def _mit_aktenvermerk(
    session: AsyncSession, agenten: list[Agent]
) -> list[schemas.AgentOut]:
    """Agenten ausliefern und dazuschreiben, wie viele Unterlagen sie tragen.

    Nur die Anzahl. Wer mitliest, soll sehen koennen, dass ein Teilnehmer aus
    Material argumentiert, das sonst niemand hat - sonst wirkt sein
    Widerspruch grundlos und die Diskussion ist von aussen nicht zu
    verstehen. Was in den Unterlagen steht, geht deshalb trotzdem niemanden
    an ausser dem Besitzer.
    """
    if not agenten:
        return []
    zahlen = dict(
        (
            await session.execute(
                select(Wissen.agent_id, func.count())
                .where(Wissen.agent_id.in_([a.id for a in agenten]))
                .group_by(Wissen.agent_id)
            )
        ).all()
    )
    hinaus = []
    for agent in agenten:
        eintrag = schemas.AgentOut.model_validate(agent)
        eintrag.wissen_dateien = zahlen.get(agent.id, 0)
        hinaus.append(eintrag)
    return hinaus


async def _eigener_agent(session: AsyncSession, user: User, agent_id: str) -> Agent:
    """Den Agenten holen - aber nur den eigenen.

    Die Handakte gehoert dem Besitzer, sonst niemandem. Auch keinem Admin:
    das Material liegt dort, damit dieser eine Agent eine andere Sicht hat,
    nicht damit es jemand einsammelt.
    """
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.owner_id != user.id:
        raise HTTPException(404, "Agent nicht gefunden")
    return agent


@app.get("/api/agents/{agent_id}/wissen", response_model=list[schemas.WissenOut])
async def list_wissen(
    agent_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await _eigener_agent(session, user, agent_id)
    return (
        (
            await session.execute(
                select(Wissen).where(Wissen.agent_id == agent_id).order_by(Wissen.created_at)
            )
        )
        .scalars()
        .all()
    )


@app.post("/api/agents/{agent_id}/wissen", response_model=schemas.WissenOut, status_code=201)
async def add_wissen(
    agent_id: str,
    datei: UploadFile = File(...),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Dem Agenten Unterlagen mitgeben, die nur er kennt.

    Wie bei den Dokumenten an einem Thema wird die Datei nicht aufbewahrt:
    der Text wird herausgeholt, die Bytes verworfen.

    Der Deckel ist kein Schoenheitsfehler. Das Material liegt in JEDEM
    Aufruf dieses Agenten, genau wie seine Persona - wer hier 40000 Zeichen
    ablegt, zahlt sie in jeder einzelnen Runde noch einmal.
    """
    await _eigener_agent(session, user, agent_id)

    rohdaten = await datei.read()
    name = (datei.filename or "unterlage").strip()
    try:
        text, gekuerzt = dokumente.text_gewinnen(name, rohdaten)
    except dokumente.DokumentFehler as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        del rohdaten

    if not text.strip():
        raise HTTPException(400, "Aus der Datei kam kein Text heraus.")

    grenze = get_settings().wissen_zeichen
    belegt = (
        await session.execute(
            select(func.coalesce(func.sum(Wissen.zeichen), 0)).where(
                Wissen.agent_id == agent_id
            )
        )
    ).scalar_one()
    frei = grenze - belegt
    if len(text) > frei:
        raise HTTPException(
            400,
            f"Das passt nicht mehr: {len(text)} Zeichen, frei sind noch {max(frei, 0)} "
            f"von {grenze}. Das Material liegt in jedem Aufruf dieses Agenten - "
            "nimm etwas heraus oder kuerze die Unterlage.",
        )

    stueck = Wissen(agent_id=agent_id, name=name, text=text, zeichen=len(text))
    session.add(stueck)
    await session.commit()
    await session.refresh(stueck)
    log.info(
        "%s legt '%s' (%d Zeichen%s) in die Handakte von Agent %s",
        user.name, name, len(text), ", gekuerzt" if gekuerzt else "", agent_id,
    )
    return stueck


@app.delete("/api/agents/{agent_id}/wissen/{wissen_id}", status_code=204)
async def remove_wissen(
    agent_id: str,
    wissen_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await _eigener_agent(session, user, agent_id)
    stueck = await session.get(Wissen, wissen_id)
    if stueck is None or stueck.agent_id != agent_id:
        raise HTTPException(404, "Nicht gefunden")
    await session.delete(stueck)
    await session.commit()
    return Response(status_code=204)


@app.delete("/api/agents/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.owner_id != user.id:
        raise HTTPException(404, "Nicht gefunden")
    await session.delete(agent)
    await session.commit()


# ---------------------------------------------------------------------------
# Foren
# ---------------------------------------------------------------------------
async def sichtbare_foren(session: AsyncSession, user: User) -> set[str]:
    """Welche Foren diese Person sehen darf.

    Die Regel in einem Satz: ein Forum ist sichtbar, wenn auf dem Weg zur
    Wurzel kein geschlossenes Forum liegt, in dem die Person nicht Mitglied
    ist.

    Die Vererbung nach unten ist der Punkt. Ohne sie waere jedes Unterforum
    eines geschlossenen Forums ein Loch in der Wand - man legt einfach eins
    darunter an und der Inhalt ist wieder offen.

    Admins bekommen hier bewusst KEINE Ausnahme. Ein geschlossenes Forum ist
    sonst nur ein Forum, das ausser sechs Leuten niemand sieht. Loeschen
    duerfen sie es trotzdem, ohne hineinzusehen - siehe delete_forum.
    """
    foren = (
        await session.execute(select(Forum.id, Forum.parent_id, Forum.sichtbar))
    ).all()
    if not foren:
        return set()

    meine = set(
        (
            await session.execute(
                select(Mitglied.forum_id).where(Mitglied.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )

    zustand = {f_id: (eltern, art) for f_id, eltern, art in foren}
    erlaubt: dict[str, bool] = {}

    def darf(forum_id: str, gesehen: set[str]) -> bool:
        if forum_id in erlaubt:
            return erlaubt[forum_id]
        # Ringe kann die API nicht erzeugen, ein eingespieltes Abbild schon.
        # Im Zweifel zu: lieber etwas nicht zeigen als etwas zu viel.
        if forum_id in gesehen:
            return False
        eltern, art = zustand[forum_id]
        antwort = not (art == "geschlossen" and forum_id not in meine)
        if antwort and eltern and eltern in zustand:
            antwort = darf(eltern, gesehen | {forum_id})
        erlaubt[forum_id] = antwort
        return antwort

    return {f_id for f_id, _, _ in foren if darf(f_id, set())}


async def darf_thema_sehen(session: AsyncSession, user: User, thread: Thread) -> bool:
    """Ein Thema folgt seinem Forum. Ohne Forum ist es fuer alle da."""
    if thread.forum_id is None:
        return True
    return thread.forum_id in await sichtbare_foren(session, user)


async def thema_oder_403(session: AsyncSession, user: User, thread_id: str) -> Thread:
    """Das Thema holen - oder abweisen, als gaebe es es nicht.

    Bewusst 404 und nicht 403: ein 403 verriete, dass es das Thema gibt.
    Bei einem geschlossenen Forum ist schon die Existenz eine Auskunft.
    """
    thread = await session.get(Thread, thread_id)
    if thread is None or not await darf_thema_sehen(session, user, thread):
        raise HTTPException(404, "Thread nicht gefunden")
    return thread


async def _waere_zyklus(session: AsyncSession, forum_id: str, eltern_id: str | None) -> bool:
    """Darf forum_id unter eltern_id haengen?

    Ohne diese Pruefung laesst sich ein Forum in sein eigenes Unterforum
    schieben - der Baum haette dann einen Ring und die Oberflaeche wuerde
    beim Aufbauen endlos laufen.
    """
    lauf = eltern_id
    while lauf:
        if lauf == forum_id:
            return True
        eltern = await session.get(Forum, lauf)
        lauf = eltern.parent_id if eltern else None
    return False


async def _forum_mit_unterforen(session: AsyncSession, forum_id: str) -> list[str]:
    """forum_id samt allen darunter liegenden Foren."""
    kanten = (await session.execute(select(Forum.id, Forum.parent_id))).all()
    kinder: dict[str | None, list[str]] = {}
    for kind, eltern in kanten:
        kinder.setdefault(eltern, []).append(kind)

    gesammelt = [forum_id]
    offen = [forum_id]
    while offen:
        aktuell = offen.pop()
        for kind in kinder.get(aktuell, []):
            gesammelt.append(kind)
            offen.append(kind)
    return gesammelt


@app.get("/api/forums", response_model=list[schemas.ForumOut])
async def list_forums(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    erlaubt = await sichtbare_foren(session, user)
    alle = (
        (await session.execute(select(Forum).order_by(Forum.position, Forum.name)))
        .scalars()
        .all()
    )
    return [f for f in alle if f.id in erlaubt]


@app.post("/api/forums", response_model=schemas.ForumOut, status_code=201)
async def create_forum(
    payload: schemas.ForumCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    if payload.parent_id and payload.parent_id not in await sichtbare_foren(session, user):
        raise HTTPException(400, "Uebergeordnetes Forum gibt es nicht")
    if payload.sichtbar not in ("offen", "geschlossen"):
        raise HTTPException(400, "Sichtbarkeit ist entweder offen oder geschlossen")
    forum = Forum(creator_id=user.id, **payload.model_dump())
    session.add(forum)
    await session.flush()
    if forum.sichtbar == "geschlossen":
        # Wer es anlegt, ist drin - sonst waere es im selben Atemzug fuer
        # niemanden mehr zu oeffnen.
        session.add(Mitglied(forum_id=forum.id, user_id=user.id))
    await session.commit()
    await session.refresh(forum)
    return forum


@app.patch("/api/forums/{forum_id}", response_model=schemas.ForumOut)
async def update_forum(
    forum_id: str,
    payload: schemas.ForumUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    erlaubt = await sichtbare_foren(session, user)
    forum = await session.get(Forum, forum_id)
    if forum is None or forum_id not in erlaubt:
        raise HTTPException(404, "Nicht gefunden")
    werte = payload.model_dump(exclude_unset=True)
    if "sichtbar" in werte:
        if werte["sichtbar"] not in ("offen", "geschlossen"):
            raise HTTPException(400, "Sichtbarkeit ist entweder offen oder geschlossen")
        if forum.creator_id != user.id and not user.is_admin:
            raise HTTPException(403, "Nur der Ersteller aendert die Sichtbarkeit")
    if "parent_id" in werte:
        if werte["parent_id"] == forum_id:
            raise HTTPException(400, "Ein Forum kann nicht sich selbst enthalten")
        if werte["parent_id"] and werte["parent_id"] not in erlaubt:
            raise HTTPException(400, "Uebergeordnetes Forum gibt es nicht")
        if await _waere_zyklus(session, forum_id, werte["parent_id"]):
            raise HTTPException(400, "Das waere ein Ring: Ziel liegt unterhalb dieses Forums")
    for schluessel, wert in werte.items():
        setattr(forum, schluessel, wert)
    if forum.sichtbar == "geschlossen":
        vorhanden = await session.get(
            Mitglied, {"forum_id": forum.id, "user_id": user.id}
        )
        if vorhanden is None:
            session.add(Mitglied(forum_id=forum.id, user_id=user.id))
    await session.commit()
    await session.refresh(forum)
    return forum


@app.get("/api/forums/{forum_id}/mitglieder", response_model=list[schemas.MitgliedOut])
async def list_mitglieder(
    forum_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Wer in diesem Raum ist. Sehen darf das nur, wer selbst darin ist."""
    if forum_id not in await sichtbare_foren(session, user):
        raise HTTPException(404, "Nicht gefunden")
    zeilen = (
        await session.execute(
            select(Mitglied.user_id, User.name)
            .join(User, User.id == Mitglied.user_id)
            .where(Mitglied.forum_id == forum_id)
            .order_by(User.name)
        )
    ).all()
    return [schemas.MitgliedOut(user_id=u, name=n) for u, n in zeilen]


@app.post("/api/forums/{forum_id}/mitglieder", status_code=201)
async def mitglied_aufnehmen(
    forum_id: str,
    payload: schemas.MitgliedIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Jemanden hereinholen.

    Wer drin ist, darf hereinholen - nicht nur der Ersteller. Ein Raum, in
    dem nur eine Person Leute aufnehmen kann, steht still, sobald sie weg
    ist, und das Loeschen der Person wuerde ihn mitnehmen.
    """
    forum = await session.get(Forum, forum_id)
    if forum is None or forum_id not in await sichtbare_foren(session, user):
        raise HTTPException(404, "Nicht gefunden")
    if forum.sichtbar != "geschlossen":
        raise HTTPException(400, "Ein offenes Forum hat keine Mitgliederliste.")
    if await session.get(User, payload.user_id) is None:
        raise HTTPException(404, "Person nicht gefunden")
    vorhanden = await session.get(
        Mitglied, {"forum_id": forum_id, "user_id": payload.user_id}
    )
    if vorhanden is None:
        session.add(Mitglied(forum_id=forum_id, user_id=payload.user_id))
        await session.commit()
    return {"aufgenommen": payload.user_id}


@app.delete("/api/forums/{forum_id}/mitglieder/{user_id}", status_code=204)
async def mitglied_entfernen(
    forum_id: str,
    user_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Jemanden hinauswerfen - oder selbst gehen.

    Die letzte Person kann nicht gehen: ein geschlossenes Forum ohne
    Mitglieder koennte niemand mehr oeffnen, auch kein Admin. Es waere da
    und unerreichbar. Wer wirklich weg will, loescht es.
    """
    forum = await session.get(Forum, forum_id)
    if forum is None or forum_id not in await sichtbare_foren(session, user):
        raise HTTPException(404, "Nicht gefunden")
    stand = await session.get(Mitglied, {"forum_id": forum_id, "user_id": user_id})
    if stand is None:
        raise HTTPException(404, "Gehoert nicht dazu")
    uebrig = (
        await session.execute(
            select(func.count())
            .select_from(Mitglied)
            .where(Mitglied.forum_id == forum_id, Mitglied.user_id != user_id)
        )
    ).scalar_one()
    if uebrig == 0:
        raise HTTPException(
            400,
            "Das waere das letzte Mitglied - danach kaeme niemand mehr hinein. "
            "Hol erst jemanden dazu oder loesche das Forum.",
        )
    await session.delete(stand)
    await session.commit()
    return Response(status_code=204)


@app.delete("/api/forums/{forum_id}", status_code=204)
async def delete_forum(
    forum_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    forum = await session.get(Forum, forum_id)
    if forum is None:
        raise HTTPException(404, "Nicht gefunden")

    sichtbar = forum_id in await sichtbare_foren(session, user)
    if not sichtbar and not user.is_admin:
        raise HTTPException(404, "Nicht gefunden")
    if sichtbar and forum.creator_id != user.id and not user.is_admin:
        raise HTTPException(403, "Nur der Ersteller oder ein Admin darf loeschen")

    if not sichtbar:
        # Loeschen duerfen, ohne lesen zu duerfen. Ein Admin kann einen
        # geschlossenen Raum nicht leerraeumen, weil er nicht hineinsieht -
        # also faellt hier die Auflage weg, dass er leer sein muss. Was
        # darin liegt, geht mit; ON DELETE CASCADE traegt Unterforen,
        # Mitglieder und ueber threads.forum_id die Themen.
        log.warning(
            "Admin %s loescht das geschlossene Forum %s ungesehen samt Inhalt",
            user.name, forum_id,
        )
        aeste = await _forum_mit_unterforen(session, forum_id)
        await session.execute(delete(Thread).where(Thread.forum_id.in_(aeste)))
        await session.delete(forum)
        await session.commit()
        return Response(status_code=204)

    kinder = (
        await session.execute(select(Forum.id).where(Forum.parent_id == forum_id).limit(1))
    ).first()
    themen = (
        await session.execute(select(Thread.id).where(Thread.forum_id == forum_id).limit(1))
    ).first()
    if kinder or themen:
        raise HTTPException(
            409, "Forum ist nicht leer - erst Unterforen und Themen verschieben oder loeschen"
        )
    await session.delete(forum)
    await session.commit()


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------
@app.get("/api/threads", response_model=list[schemas.ThreadOut])
async def list_threads(
    forum_id: str | None = None,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    erlaubt = await sichtbare_foren(session, user)
    stmt = select(Thread).order_by(Thread.updated_at.desc())
    if forum_id == "-":
        # Themen ohne Einordnung
        stmt = stmt.where(Thread.forum_id.is_(None))
    elif forum_id:
        # Ein Oberforum zeigt auch, was in seinen Unterforen liegt - sonst
        # waere es eine leere Huelle, obwohl darunter diskutiert wird.
        aeste = await _forum_mit_unterforen(session, forum_id)
        stmt = stmt.where(Thread.forum_id.in_([f for f in aeste if f in erlaubt]))
    else:
        # Ohne Filter: alles Offene plus die Themen ohne Forum.
        stmt = stmt.where(
            Thread.forum_id.is_(None) | Thread.forum_id.in_(erlaubt or [""])
        )
    return (await session.execute(stmt)).scalars().all()


@app.post("/api/threads", response_model=schemas.ThreadDetail, status_code=201)
async def create_thread(
    payload: schemas.ThreadCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    if payload.mode not in ("selector", "roundrobin"):
        raise HTTPException(400, "mode muss 'selector' oder 'roundrobin' sein")

    # In ein Forum, das man nicht sieht, legt man auch nichts hinein.
    if payload.forum_id is not None:
        if payload.forum_id not in await sichtbare_foren(session, user):
            raise HTTPException(400, "Forum gibt es nicht")

    agents: list[Agent] = []
    for agent_id in dict.fromkeys(payload.agent_ids):
        agent = await session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(400, f"Agent {agent_id} nicht verfuegbar")
        # Nur eigene. Ein fremder Agent laeuft auf dem Modell-Zugang seines
        # Besitzers, also auf dessen Rechnung - wer mitdiskutieren will,
        # holt seinen eigenen dazu. Oeffentlich heisst sichtbar, nicht
        # benutzbar.
        if agent.owner_id != user.id:
            raise HTTPException(
                403,
                f"'{agent.name}' gehoert jemand anderem. Eigene Agenten kannst du "
                "hinzufuegen; fremde muss ihr Besitzer selbst dazuholen.",
            )
        agents.append(agent)

    names = [a.name.lower() for a in agents]
    if len(set(names)) != len(names):
        raise HTTPException(400, "Teilnehmernamen muessen im Thread eindeutig sein")

    moderator_id = payload.moderator_agent_id or agents[0].id
    if moderator_id not in {a.id for a in agents}:
        raise HTTPException(400, "Moderator muss Teilnehmer des Threads sein")

    if payload.forum_id and await session.get(Forum, payload.forum_id) is None:
        raise HTTPException(400, "Forum gibt es nicht")

    if payload.art not in pruefarten.ARTEN:
        raise HTTPException(400, "Unbekannte Art der Diskussion")

    thread = Thread(
        title=payload.title,
        goal=payload.goal,
        creator_id=user.id,
        forum_id=payload.forum_id,
        status="active" if payload.start_now else "paused",
        mode=payload.mode,
        moderator_agent_id=moderator_id,
        max_rounds=payload.max_rounds,
        pace_seconds=payload.pace_seconds,
        stop_phrase=payload.stop_phrase,
        synthesize_on_end=payload.synthesize_on_end,
        # Auch wenn jemand es anfragt: erlaubt ist es nur, wenn der Server es
        # zulaesst.
        werkzeuge=payload.werkzeuge and get_settings().werkzeuge,
        art=payload.art,
        next_run_at=utcnow(),
    )
    session.add(thread)
    await session.flush()
    for position, agent in enumerate(agents):
        session.add(Participant(thread_id=thread.id, agent_id=agent.id, position=position))
    await session.commit()

    # Damit das neue Thema sofort in den Listen der anderen auftaucht. Ohne
    # das erschiene es erst, wenn der Worker es das erste Mal anfasst - bei
    # einem angehaltenen Thema also nie.
    await orchestrator.publish_thread(thread)

    return schemas.ThreadDetail(
        **schemas.ThreadOut.model_validate(thread).model_dump(),
        participants=await _mit_aktenvermerk(session, agents),
    )


@app.get("/api/threads/{thread_id}", response_model=schemas.ThreadDetail)
async def get_thread(
    thread_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    thread = await thema_oder_403(session, user, thread_id)
    participants = await orchestrator.list_participants(session, thread_id)
    return schemas.ThreadDetail(
        **schemas.ThreadOut.model_validate(thread).model_dump(),
        participants=await _mit_aktenvermerk(session, participants),
    )


@app.get("/api/threads/{thread_id}/posts", response_model=list[schemas.PostOut])
async def list_posts(
    thread_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await thema_oder_403(session, user, thread_id)
    return (
        (
            await session.execute(
                select(Post)
                .where(Post.thread_id == thread_id)
                .order_by(Post.created_at, Post.id)
            )
        )
        .scalars()
        .all()
    )


@app.patch("/api/threads/{thread_id}", response_model=schemas.ThreadOut)
async def verschiebe_thread(
    thread_id: str,
    payload: schemas.ThreadUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Ein Thema umhaengen oder umbenennen.

    Gedacht fuer den Fall, dass ein Thema im falschen Forum gelandet ist.
    Die Kennung des Themas bleibt dabei dieselbe - deshalb braucht es hier
    weder Umleitung noch alte URL, anders als in einem Forum, das seine
    Adressen aus dem Forumsnamen baut.
    """
    thread = await thema_oder_403(session, user, thread_id)
    if thread.creator_id != user.id and not user.is_admin:
        raise HTTPException(403, "Nur wer das Thema eroeffnet hat, darf es verschieben.")

    werte = payload.model_dump(exclude_unset=True)
    if "forum_id" in werte and werte["forum_id"] is not None:
        # In ein Forum, das man nicht sieht, schiebt man auch nichts hinein -
        # sonst waere das Verschieben ein Weg, Inhalt aus der Sicht anderer
        # verschwinden zu lassen oder in einen fremden Raum zu legen.
        if werte["forum_id"] not in await sichtbare_foren(session, user):
            raise HTTPException(400, "Forum gibt es nicht")
    for schluessel, wert in werte.items():
        setattr(thread, schluessel, wert)
    await session.commit()
    await session.refresh(thread)

    # Die Seitenleiste zeigt Themen nach Forum sortiert - ohne diese Meldung
    # sieht sie die Verschiebung erst beim naechsten Laden.
    await orchestrator.publish_thread(thread)
    log.info("%s hat Thema %s verschoben", user.name, thread.id)
    return thread


@app.post("/api/threads/{thread_id}/gelesen", status_code=204)
async def thema_gelesen(
    thread_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Merken, dass diese Person hier auf dem Laufenden ist.

    Die Oberflaeche ruft das beim Oeffnen eines Themas und danach bei jedem
    neuen Beitrag, den sie tatsaechlich anzeigt. Fehlschlaege sind egal - im
    schlimmsten Fall leuchtet ein Punkt einmal zu lang.
    """
    await thema_oder_403(session, user, thread_id)
    stand = await session.get(Gelesen, {"user_id": user.id, "thread_id": thread_id})
    if stand is None:
        session.add(Gelesen(user_id=user.id, thread_id=thread_id, gelesen_bis=utcnow()))
    else:
        stand.gelesen_bis = utcnow()
    await session.commit()
    return Response(status_code=204)


@app.get("/api/ungelesen", response_model=schemas.UngelesenOut)
async def ungelesen(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Wo liegt etwas, das diese Person noch nicht gesehen hat?

    Ein Thema gilt als ungelesen, wenn sein juengster Beitrag nach dem
    gemerkten Zeitpunkt liegt - oder wenn es dafuer noch gar keine Zeile
    gibt und ueberhaupt ein Beitrag darin steht. Eigene Beitraege zaehlen
    nicht: wer selbst geschrieben hat, weiss es.

    Der Punkt wandert bis zur Wurzel hinauf, sonst bliebe er an einem
    zugeklappten Ast unsichtbar.
    """
    # Auch hier die Sichtbarkeit: der Punkt verraet zwar keinen Inhalt, aber
    # die Existenz und die Regsamkeit eines geschlossenen Forums.
    erlaubt = await sichtbare_foren(session, user)
    letzter = (
        select(Post.thread_id, func.max(Post.created_at).label("zuletzt"))
        .where(Post.user_id.is_distinct_from(user.id))
        .group_by(Post.thread_id)
        .subquery()
    )
    zeilen = (
        await session.execute(
            select(Thread.id, Thread.forum_id, letzter.c.zuletzt, Gelesen.gelesen_bis)
            .join(letzter, letzter.c.thread_id == Thread.id)
            .outerjoin(
                Gelesen,
                (Gelesen.thread_id == Thread.id) & (Gelesen.user_id == user.id),
            )
            .where(Thread.forum_id.is_(None) | Thread.forum_id.in_(erlaubt or [""]))
        )
    ).all()

    themen: list[str] = []
    foren: set[str] = set()
    ohne_forum = False
    for thread_id, forum_id, zuletzt, gelesen_bis in zeilen:
        if gelesen_bis is not None and as_utc(zuletzt) <= as_utc(gelesen_bis):
            continue
        themen.append(thread_id)
        if forum_id is None:
            ohne_forum = True
        else:
            foren.add(forum_id)

    # Nach oben durchreichen, damit der Punkt auch am Oberforum steht.
    if foren:
        eltern = dict(
            (await session.execute(select(Forum.id, Forum.parent_id))).all()
        )
        vollstaendig = set(foren)
        for start in foren:
            lauf = eltern.get(start)
            # Die Schleife ist gegen Ringe abgesichert; die API verhindert
            # sie zwar, aber ein Abbild von aussen koennte welche enthalten.
            gesehen = {start}
            while lauf and lauf not in gesehen:
                vollstaendig.add(lauf)
                gesehen.add(lauf)
                lauf = eltern.get(lauf)
        foren = vollstaendig

    return schemas.UngelesenOut(
        threads=themen, foren=sorted(foren), ohne_forum=ohne_forum
    )


@app.post("/api/threads/{thread_id}/posts", response_model=schemas.PostOut, status_code=201)
async def create_post(
    thread_id: str,
    payload: schemas.PostCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    thread = await thema_oder_403(session, user, thread_id)

    post = await orchestrator.add_post(
        session,
        thread_id,
        author_type="human",
        author_name=user.name,
        user_id=user.id,
        content=payload.content.strip(),
        round_no=thread.rounds_done,
    )

    if payload.resume:
        # Ein menschlicher Zwischenruf weckt den Thread und legt bei
        # aufgebrauchtem Budget ein paar Runden nach.
        if thread.rounds_done >= thread.max_rounds:
            thread.max_rounds = thread.rounds_done + 3
        thread.status = "active"
        thread.status_detail = f"Fortgesetzt durch {user.name}."
        thread.locked_until = None
        thread.locked_by = None
        thread.next_run_at = utcnow()
        thread.updated_at = utcnow()
        await session.commit()
        await orchestrator.publish_thread(thread)

    return post


@app.post(
    "/api/threads/{thread_id}/participants",
    response_model=schemas.ThreadDetail,
    status_code=201,
)
async def add_participant(
    thread_id: str,
    payload: schemas.ParticipantAdd,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Einen Agenten nachtraeglich dazuholen.

    Gedacht fuer den Fall, dass in einer laufenden Diskussion eine Position
    fehlt. Der Neue erfaehrt aus dem Verlauf, worum es geht, und muss der
    Einigung am Ende ebenfalls zustimmen.
    """
    thread = await thema_oder_403(session, user, thread_id)

    agent = await session.get(Agent, payload.agent_id)
    if agent is None:
        raise HTTPException(400, "Agent nicht verfuegbar")
    # Siehe oben: fremde Agenten kosten fremdes Geld.
    if agent.owner_id != user.id:
        raise HTTPException(
            403,
            f"'{agent.name}' gehoert jemand anderem. Eigene Agenten kannst du "
            "hinzufuegen; fremde muss ihr Besitzer selbst dazuholen.",
        )

    bisher = await orchestrator.list_participants(session, thread_id)
    if any(vorhanden.id == agent.id for vorhanden in bisher):
        raise HTTPException(409, f"'{agent.name}' ist schon dabei")
    if any(vorhanden.name.lower() == agent.name.lower() for vorhanden in bisher):
        raise HTTPException(400, f"Es gibt hier schon einen Teilnehmer namens '{agent.name}'")

    session.add(Participant(thread_id=thread_id, agent_id=agent.id, position=len(bisher)))
    await session.commit()

    # Als Beitrag festhalten, damit die anderen im Verlauf sehen, dass und
    # warum jemand dazugekommen ist.
    await orchestrator.add_post(
        session,
        thread_id,
        author_type="system",
        author_name="Forum",
        content=(
            f"{agent.name} wurde zur Diskussion hinzugezogen"
            + (f" - {agent.role}." if agent.role else ".")
        ),
        round_no=thread.rounds_done,
    )

    # Eine neue Position soll auch zu Wort kommen: notfalls Runden nachlegen.
    if thread.rounds_done >= thread.max_rounds:
        thread.max_rounds = thread.rounds_done + len(bisher) + 1
    thread.status = "active"
    thread.status_detail = f"{agent.name} von {user.name} hinzugezogen."
    thread.locked_until = None
    thread.locked_by = None
    thread.next_run_at = utcnow()
    thread.updated_at = utcnow()
    await session.commit()
    await orchestrator.publish_thread(thread)

    teilnehmer = await orchestrator.list_participants(session, thread_id)
    return schemas.ThreadDetail(
        **schemas.ThreadOut.model_validate(thread).model_dump(),
        participants=await _mit_aktenvermerk(session, teilnehmer),
    )


@app.delete("/api/threads/{thread_id}/participants/{agent_id}", status_code=204)
async def remove_participant(
    thread_id: str,
    agent_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    thread = await thema_oder_403(session, user, thread_id)

    bisher = await orchestrator.list_participants(session, thread_id)
    if len(bisher) <= 1:
        raise HTTPException(409, "Der letzte Teilnehmer kann nicht entfernt werden")

    zeile = (
        await session.execute(
            select(Participant).where(
                Participant.thread_id == thread_id, Participant.agent_id == agent_id
            )
        )
    ).scalar_one_or_none()
    if zeile is None:
        raise HTTPException(404, "Teilnehmer nicht gefunden")

    name = next((a.name for a in bisher if a.id == agent_id), agent_id)
    await session.delete(zeile)
    # Der Moderator muss Teilnehmer bleiben, sonst waehlt niemand den Sprecher.
    if thread.moderator_agent_id == agent_id:
        thread.moderator_agent_id = next(a.id for a in bisher if a.id != agent_id)
    await session.commit()

    await orchestrator.add_post(
        session,
        thread_id,
        author_type="system",
        author_name="Forum",
        content=f"{name} nimmt nicht mehr an der Diskussion teil.",
        round_no=thread.rounds_done,
    )
    await orchestrator.publish_thread(thread)


@app.post("/api/threads/{thread_id}/documents", response_model=schemas.PostOut, status_code=201)
async def add_document(
    thread_id: str,
    datei: UploadFile = File(...),
    resume: bool = Form(default=True),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Ein Schriftstueck beisteuern, ueber das die Agenten reden sollen.

    Die Datei wird nirgends abgelegt: sie kommt herein, der Text geht als
    Beitrag in den Thread, die Bytes werden verworfen. Anders ginge es auch
    nicht - der Worker laeuft in einem anderen Prozess und sieht nur, was im
    Verlauf steht.
    """
    thread = await thema_oder_403(session, user, thread_id)

    rohdaten = await datei.read()
    name = (datei.filename or "dokument").strip()
    try:
        text, gekuerzt = dokumente.text_gewinnen(name, rohdaten)
    except dokumente.DokumentFehler as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        del rohdaten

    if gekuerzt:
        text += (
            "\n\n[... hier abgeschnitten: das Dokument ist laenger als "
            f"{dokumente.MAX_ZEICHEN} Zeichen und wuerde sonst in jedem "
            "Modellaufruf mitgeschickt.]"
        )

    post = await orchestrator.add_post(
        session,
        thread_id,
        author_type="document",
        author_name=name,
        user_id=user.id,
        content=text,
        round_no=thread.rounds_done,
    )

    if resume:
        if thread.rounds_done >= thread.max_rounds:
            thread.max_rounds = thread.rounds_done + 3
        thread.status = "active"
        thread.status_detail = f"'{name}' von {user.name} beigesteuert."
        thread.locked_until = None
        thread.locked_by = None
        thread.next_run_at = utcnow()
        thread.updated_at = utcnow()
        await session.commit()
        await orchestrator.publish_thread(thread)

    return post


@app.delete("/api/threads/{thread_id}/documents/{post_id}", status_code=204)
async def remove_document(
    thread_id: str,
    post_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    post = await session.get(Post, post_id)
    if post is None or post.thread_id != thread_id or post.author_type != "document":
        raise HTTPException(404, "Dokument nicht gefunden")
    if post.user_id != user.id and not user.is_admin:
        raise HTTPException(403, "Nur wer es beigesteuert hat oder ein Admin darf es entfernen")
    await session.delete(post)
    await session.commit()
    await bus.publish({"type": "post.removed", "thread_id": thread_id, "post_id": post_id})


# Wohin sich Beitraege uebersetzen lassen. Eine weitere Sprache ist ein
# Eintrag hier und einer in app/static/i18n.js (SPRACHNAMEN) - sonst nichts.
# Der Name geht so, wie er hier steht, in den Auftrag ans Modell.
ZIELSPRACHEN = {
    "de": "Deutsch",
    "en": "English",
    "ru": "Russisch (russkij)",
}

# So viel Text geht hoechstens in einen Uebersetzungsaufruf. Laengeres wird
# gekuerzt - sonst kostet ein einziger Klick auf ein langes Dokument mehr als
# die ganze Diskussion.
UEBERSETZUNG_MAX = 8000


async def _uebersetzer(session: AsyncSession, user: User) -> tuple[Agent, bytes | None]:
    """Einen Agenten des Lesers finden, mit dem uebersetzt werden kann.

    Bewusst der Zugang dessen, der liest: sonst zahlt fremde Neugier auf die
    Rechnung des Verfassers.
    """
    eigene = (
        (
            await session.execute(
                select(Agent)
                .where(Agent.owner_id == user.id, Agent.credential_id.is_not(None))
                .order_by(Agent.name)
            )
        )
        .scalars()
        .all()
    )
    if not eigene:
        raise HTTPException(
            409,
            "Zum Uebersetzen brauchst du einen eigenen Agenten mit Modell-Zugang.",
        )
    agent = eigene[0]
    try:
        zugang = agent.credential
        if zugang is not None and zugang.enc_scheme == "user":
            return agent, tresor.abholen(user.dk_unlocked, user.unlocked_until)
    except tresor.GesperrtFehler as exc:
        raise HTTPException(409, str(exc)) from exc
    return agent, None


def _token_budget(quelle: str) -> int:
    """Wie viele Token die Uebersetzung hoechstens brauchen darf.

    Hier lag ein Fehler, der lange unsichtbar war: das Budget stand auf
    min(4000, len(quelle) + 500) - und len() zaehlt ZEICHEN, max_tokens
    zaehlt TOKEN. Solange Quelle und Ziel lateinisch sind, geht die Rechnung
    durch Zufall auf, weil ein Token dort etwa vier Zeichen deckt.

    Kyrillisch nicht. Dort kommen auf ein Token eher anderthalb Zeichen, ein
    Beitrag von 8000 Zeichen braucht also gut 5000 Token - und riss die feste
    Obergrenze von 4000. Das Modell hoerte mitten im Satz auf.

    Deshalb wird jetzt mit dem ungeguenstigsten Verhaeltnis gerechnet, nicht
    mit dem bequemsten. Ein zu grosses Budget kostet nichts: bezahlt werden
    die Token, die tatsaechlich herauskommen.
    """
    # Ein Token deckt im schlechtesten Fall etwa 1,3 Zeichen (Kyrillisch,
    # Griechisch, CJK). Plus Luft fuer Formatierung, die stehen bleibt.
    return min(16000, int(len(quelle) / 1.3) + 500)


@app.post("/api/posts/{post_id}/translate", response_model=schemas.TranslateOut)
async def translate_post(
    post_id: str,
    payload: schemas.TranslateIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Einen Beitrag uebersetzen - auf Knopfdruck, nicht von selbst.

    Automatisch zu uebersetzen hiesse, fuer jeden Beitrag zu zahlen, den nie
    jemand liest. So haengen die Kosten daran, was tatsaechlich gelesen wird.
    """
    ziel = payload.ziel.strip().lower()[:2]
    if ziel not in ZIELSPRACHEN:
        erlaubt = ", ".join(ZIELSPRACHEN)
        raise HTTPException(400, f"Zielsprache muss eine von diesen sein: {erlaubt}")

    post = await session.get(Post, post_id)
    if post is None:
        raise HTTPException(404, "Beitrag nicht gefunden")
    # Uebersetzen heisst lesen - also dieselbe Huerde wie beim Thema.
    await thema_oder_403(session, user, post.thread_id)
    if post.author_type == "tool":
        raise HTTPException(400, "Rechenergebnisse werden nicht uebersetzt.")
    if not (post.content or "").strip():
        raise HTTPException(400, "Der Beitrag ist leer.")

    vorhanden = (
        await session.execute(
            select(Uebersetzung).where(
                Uebersetzung.post_id == post_id, Uebersetzung.sprache == ziel
            )
        )
    ).scalar_one_or_none()
    if vorhanden is not None:
        return schemas.TranslateOut(sprache=ziel, text=vorhanden.text, gecacht=True)

    agent, dk = await _uebersetzer(session, user)
    quelle = post.content.strip()
    gekuerzt = len(quelle) > UEBERSETZUNG_MAX
    if gekuerzt:
        quelle = quelle[:UEBERSETZUNG_MAX]

    sprachname = ZIELSPRACHEN[ziel]
    auftrag = (
        f"Uebersetze den folgenden Forumsbeitrag nach {sprachname}.\n"
        "Gib ausschliesslich die Uebersetzung aus, ohne Vorrede und ohne "
        "Anfuehrungszeichen. Absaetze, Aufzaehlungen und Code-Bloecke bleiben "
        "unveraendert stehen; Code wird nicht uebersetzt. Ist der Text bereits "
        f"in {sprachname}, gib ihn unveraendert zurueck.\n\n"
        f"--- Beitrag ---\n{quelle}"
    )
    try:
        text, grund = await llm.vervollstaendige(
            agent,
            [{"role": "user", "content": auftrag}],
            max_tokens=_token_budget(quelle),
            dk=dk,
        )
    except llm.LLMError as exc:
        raise HTTPException(502, f"Uebersetzung fehlgeschlagen: {exc}") from exc

    if grund == "length":
        # Das Budget hat trotzdem nicht gereicht. Lieber sagen als so tun,
        # als waere das der ganze Text.
        text += "\n\n[... die Uebersetzung bricht hier ab: das Modell hat sein "
        text += "Token-Budget erreicht.]"
    if gekuerzt:
        text += "\n\n[... gekuerzt: nur die ersten "
        text += f"{UEBERSETZUNG_MAX} Zeichen wurden uebersetzt.]"

    session.add(
        Uebersetzung(post_id=post_id, sprache=ziel, text=text, veranlasst_von=user.id)
    )
    await session.commit()
    log.info("Beitrag %s nach %s uebersetzt (von %s)", post_id, ziel, user.name)
    return schemas.TranslateOut(sprache=ziel, text=text, gecacht=False)


@app.post("/api/threads/{thread_id}/control", response_model=schemas.ThreadOut)
async def control_thread(
    thread_id: str,
    payload: schemas.ThreadControl,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    thread = await thema_oder_403(session, user, thread_id)

    action = payload.action
    if action == "pause":
        thread.status = "paused"
        thread.status_detail = f"Pausiert durch {user.name}."
    elif action == "stop":
        thread.status = "done"
        thread.status_detail = f"Beendet durch {user.name}."
    elif action in ("resume", "extend"):
        # Ueber die harte Grenze hinaus bringt Weitermachen nichts: der Worker
        # sieht sie beim naechsten Zug wieder, beendet sofort und schreibt
        # noch eine Synthese. Man drueckt dann immer wieder und bekommt
        # jedes Mal nur den Moderator. Lieber klar sagen, dass Schluss ist.
        grenze = get_settings().max_rounds_hard
        if thread.rounds_done >= grenze:
            raise HTTPException(
                409,
                f"Die harte Grenze von {grenze} Runden ist erreicht - weiter "
                "geht es hier nicht. Ein Admin kann AGORA_MAX_ROUNDS_HARD "
                "anheben; sonst hilft nur ein neues Thema, das an diesem "
                "anknuepft.",
            )
        if action == "extend":
            thread.max_rounds += payload.rounds
        elif thread.rounds_done >= thread.max_rounds:
            thread.max_rounds = thread.rounds_done + payload.rounds
        thread.status = "active"
        thread.status_detail = f"Fortgesetzt durch {user.name}."
        thread.next_run_at = utcnow()
    else:
        raise HTTPException(400, "action muss pause | resume | stop | extend sein")

    # Laeuft gerade ein Zug, darf die Lease NICHT weg: sonst griffe ein
    # zweiter Worker zu, waehrend der erste noch beim Modell wartet. Es
    # reicht, seinen Anspruch zu entwerten - er sieht das am Ende, laesst
    # diesen Zustand hier in Ruhe und gibt die Lease selbst frei.
    laeuft = thread.locked_until is not None and as_utc(thread.locked_until) > utcnow()
    thread.locked_by = None
    if not laeuft:
        thread.locked_until = None
    thread.updated_at = utcnow()
    await session.commit()
    await orchestrator.publish_thread(thread)
    return thread


@app.delete("/api/threads/{thread_id}", status_code=204)
async def delete_thread(
    thread_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    thread = await thema_oder_403(session, user, thread_id)
    if thread.creator_id != user.id and not user.is_admin:
        raise HTTPException(403, "Nur der Ersteller oder ein Admin darf loeschen")
    await session.delete(thread)
    await session.commit()


# ---------------------------------------------------------------------------
# Live-Stream (SSE)
# ---------------------------------------------------------------------------
def nur_themenstand(event: dict) -> bool:
    """Was in den Uebersichtsstrom gehoert: Statusaenderungen, sonst nichts.

    Mit Namen statt als lambda, damit der Test die Bedingung pruefen kann,
    ohne einen unendlichen Strom aufmachen zu muessen.
    """
    return event.get("type") == "thread.update"


def _ereignisstrom(request: Request, passt) -> StreamingResponse:
    """Server-Sent Events aus dem Bus, gefiltert durch `passt`.

    Einmal geschrieben statt zweimal: der Unterschied zwischen dem Strom
    eines Themas und dem der Uebersicht ist genau diese eine Bedingung.
    """

    async def generator() -> AsyncIterator[str]:
        async with bus.subscribe() as queue:
            yield ": verbunden\n\n"
            while True:
                if await request.is_disconnected():
                    return
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    # Ohne Lebenszeichen schliessen Proxys die Verbindung.
                    yield ": ping\n\n"
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if not passt(event):
                    continue
                yield f"event: {event.get('type', 'message')}\ndata: {payload}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.get("/api/threads/{thread_id}/stream")
async def stream_thread(
    thread_id: str,
    request: Request,
    token: str = "",
    agora_token: str = Cookie(default="", alias=COOKIE_NAME),
):
    # EventSource kann keine Header setzen. Das Cookie schickt der Browser von
    # allein mit; der Query-Parameter bleibt als Rueckfallebene.
    async with SessionLocal() as session:
        user = await user_by_token(session, token or agora_token)
        if user is None:
            raise HTTPException(401, "Unbekanntes Token")
        # Ohne diese Zeile waere der Raum zu und der Livestream daraus offen.
        await thema_oder_403(session, user, thread_id)

    return _ereignisstrom(request, lambda e: e.get("thread_id") == thread_id)


@app.get("/api/stream")
async def stream_uebersicht(
    request: Request,
    token: str = "",
    agora_token: str = Cookie(default="", alias=COOKIE_NAME),
):
    """Statusaenderungen ALLER Themen - fuer die Liste an der Seite.

    Ohne diesen Strom erfaehrt die Liste nur etwas ueber das Thema, das
    gerade offen ist; ist gar keins offen, gar nichts. Dann sieht man erst
    nach einem Neuladen, dass anderswo weiterdiskutiert wurde.

    Bewusst nur thread.update und keine Beitragsdeltas: die Liste zeigt
    Titel und Zustand, und jedem Browser jeden Buchstaben jedes Themas zu
    schicken waere Verschwendung.
    """
    async with SessionLocal() as session:
        user = await user_by_token(session, token or agora_token)
        if user is None:
            raise HTTPException(401, "Unbekanntes Token")
        # Welche Themen diese Person sehen darf, steht beim Verbinden fest.
        # Ein Thema, das waehrenddessen in ein geschlossenes Forum wandert,
        # faellt erst beim naechsten Verbinden heraus - es traegt ohnehin nur
        # Titel und Zustand, keinen Inhalt.
        erlaubt = await sichtbare_foren(session, user)
        meine = set(
            (
                await session.execute(
                    select(Thread.id).where(
                        Thread.forum_id.is_(None) | Thread.forum_id.in_(erlaubt or [""])
                    )
                )
            )
            .scalars()
            .all()
        )

    def sichtbarer_stand(ereignis: dict) -> bool:
        return nur_themenstand(ereignis) and ereignis.get("thread_id") in meine

    return _ereignisstrom(request, sichtbarer_stand)


@app.get("/healthz")
async def healthz():
    settings = get_settings()
    return {
        "status": "ok",
        "worker": settings.worker_enabled,
        "version": APP_VERSION,
        # Die Anmeldeseite blendet das Antragsformular danach aus.
        "antraege_offen": settings.antraege_offen,
        # Der Themen-Dialog blendet das Haekchen zum Rechnen danach aus.
        "werkzeuge": settings.werkzeuge,
        "werkzeug_pakete": werkzeuge.verfuegbare_pakete() if settings.werkzeuge else [],
    }


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
