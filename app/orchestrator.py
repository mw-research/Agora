"""Autonomer Diskussionsmotor.

Ein Thread laeuft ohne menschliches Zutun weiter, bis eine von drei Bremsen
greift: das Rundenbudget ist aufgebraucht, ein Agent sagt die Stop-Phrase,
oder ein Fehler tritt auf. Menschen koennen jederzeit dazwischenschreiben,
pausieren oder Runden nachlegen - muessen aber nicht.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import llm, pruefarten, tresor, werkzeuge
from .config import get_settings
from .db import SessionLocal
from .events import bus
from .models import Agent, Participant, Post, Thread, User, as_utc, to_utc_iso, utcnow

log = logging.getLogger(__name__)

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"
LEASE_SECONDS = 300
DELTA_FLUSH_CHARS = 60
DELTA_FLUSH_SECONDS = 0.4


# ---------------------------------------------------------------------------
# Worker-Schleife
# ---------------------------------------------------------------------------
async def worker_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    log.info("Worker %s gestartet", WORKER_ID)
    while not stop_event.is_set():
        try:
            worked = await _tick()
        except Exception:
            log.exception("Worker-Tick fehlgeschlagen")
            worked = False
        if not worked:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=settings.worker_interval)
    log.info("Worker %s beendet", WORKER_ID)


async def _tick() -> bool:
    async with SessionLocal() as session:
        thread = await _claim_thread(session)
        if thread is None:
            return False
        thread_id = thread.id
        anspruch = thread.locked_by
    # Der Zug selbst laeuft in einer eigenen Session: er kann Minuten dauern,
    # solange soll keine Transaktion offen stehen.
    async with SessionLocal() as session:
        try:
            await run_turn(session, thread_id, anspruch)
        except Exception:
            log.exception("Zug in Thread %s fehlgeschlagen", thread_id)
            await _fail_thread(session, thread_id, "Interner Fehler im Worker", anspruch)
    return True


async def _claim_thread(session: AsyncSession) -> Thread | None:
    now = utcnow()
    stmt = (
        select(Thread)
        .where(
            Thread.status == "active",
            Thread.next_run_at <= now,
            (Thread.locked_until.is_(None)) | (Thread.locked_until < now),
        )
        .order_by(Thread.next_run_at)
        .limit(1)
    )
    if get_settings().is_postgres:
        stmt = stmt.with_for_update(skip_locked=True)

    thread = (await session.execute(stmt)).scalar_one_or_none()
    if thread is None:
        await session.rollback()
        return None

    # Ein eigener Anspruch je Zug, nicht nur die Worker-Kennung: daran
    # erkennt der Worker am Ende, ob sein Anspruch noch derselbe ist - oder
    # ob inzwischen jemand eingegriffen hat.
    thread.locked_until = now + timedelta(seconds=LEASE_SECONDS)
    thread.locked_by = f"{uuid4().hex[:12]}@{WORKER_ID}"[:64]
    await session.commit()
    return thread


async def _anspruch_geltend(
    session: AsyncSession, thread_id: str, anspruch: str | None, **werte
) -> bool:
    """Schreibt den Schlusszustand - aber nur, wenn der Anspruch noch gilt.

    Ein Zug kann Minuten dauern, und in dieser Zeit koennen Menschen
    eingreifen: Weiter, +5 Runden, Beenden. Die Steuerung entwertet dabei den
    Anspruch des laufenden Workers. Sein Abschluss darf den Eingriff dann
    nicht ueberschreiben - sonst draeckt man "Weiter" und nichts passiert.

    Was der Zug bereits geschrieben hat (Beitraege, Rundenzaehler), bleibt in
    jedem Fall stehen. Nur ueber Status und naechsten Lauf entscheidet der,
    der zuletzt eingegriffen hat.
    """
    bedingung = [Thread.id == thread_id]
    if anspruch is not None:
        bedingung.append(Thread.locked_by == anspruch)

    ergebnis = await session.execute(
        update(Thread)
        .where(*bedingung)
        .values(locked_until=None, locked_by=None, updated_at=utcnow(), **werte)
    )
    if ergebnis.rowcount:
        await session.commit()
        return True

    # Anspruch entwertet. Den Zustand in Ruhe lassen, aber die Lease
    # freigeben - sonst bliebe das Thema bis zu ihrem Ablauf gesperrt. Hat
    # inzwischen ein anderer Worker zugegriffen, steht dort sein Anspruch
    # und die Bedingung greift nicht.
    await session.execute(
        update(Thread)
        .where(Thread.id == thread_id, Thread.locked_by.is_(None))
        .values(locked_until=None)
    )
    await session.commit()
    log.info("Thread %s: Eingriff waehrend des Zuges - der Zustand bleibt", thread_id)
    return False


async def _datenschluessel(session: AsyncSession, agent: Agent) -> bytes | None:
    """Den Datenschluessel des Zugangs-Besitzers holen.

    Wirft GesperrtFehler, wenn die Freischaltung fehlt oder abgelaufen ist -
    dann kann niemand die Modelle dieser Person benutzen, auch nicht in einem
    fremden Thread.
    """
    zugang = agent.credential
    if zugang is None or zugang.enc_scheme != "user":
        return None
    besitzer = await session.get(User, zugang.owner_id)
    if besitzer is None:
        raise tresor.GesperrtFehler("Der Besitzer des Zugangs gibt es nicht mehr.")
    return tresor.abholen(besitzer.dk_unlocked, besitzer.unlocked_until)


async def _teilnahme_offen(session: AsyncSession, thread: Thread, agent: Agent) -> bool:
    """Darf dieser Agent im Thread noch mitreden?

    Der Besitzer legt fest, wie lange seine Agenten nach seinem letzten
    eigenen Beitrag weiterdiskutieren duerfen. Ohne diese Grenze koennte
    jemand anders einen Thread endlos auf fremde Kosten laufen lassen.
    """
    besitzer = await session.get(User, agent.owner_id)
    stunden = (besitzer.teilnahme_stunden if besitzer else 0) or 0
    if stunden <= 0:
        return True

    beginn = (
        await session.execute(
            select(Post.created_at)
            .where(
                Post.thread_id == thread.id,
                Post.user_id == agent.owner_id,
                Post.author_type == "human",
            )
            .order_by(Post.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if beginn is None:
        # Noch kein eigener Beitrag: dann zaehlt ab dem Beitritt.
        beginn = (
            await session.execute(
                select(Participant.created_at).where(
                    Participant.thread_id == thread.id, Participant.agent_id == agent.id
                )
            )
        ).scalar_one_or_none()
    if beginn is None:
        return True
    return as_utc(beginn) + timedelta(hours=stunden) > utcnow()


# ---------------------------------------------------------------------------
# Ein Zug
# ---------------------------------------------------------------------------
async def run_turn(
    session: AsyncSession, thread_id: str, anspruch: str | None = None
) -> None:
    settings = get_settings()
    thread = await session.get(Thread, thread_id)
    if thread is None or thread.status != "active":
        return

    participants = await list_participants(session, thread_id)
    if not participants:
        await _finish_thread(
            session, thread, "paused", "Keine Agenten im Thread.", anspruch
        )
        return

    if thread.rounds_done >= min(thread.max_rounds, settings.max_rounds_hard):
        await _end_of_budget(session, thread, participants, anspruch)
        return

    # Wer darf gerade ueberhaupt reden? Das Fenster setzt der Besitzer der
    # Agenten, nicht der, der den Thread angestossen hat.
    erlaubt = [a for a in participants if await _teilnahme_offen(session, thread, a)]
    if not erlaubt:
        await _finish_thread(
            session,
            thread,
            "paused",
            "Teilnahmefenster abgelaufen - wer weiterdiskutieren will, schreibt einen Beitrag.",
            anspruch,
        )
        return

    speaker = await _choose_speaker(session, thread, erlaubt)
    try:
        dk = await _datenschluessel(session, speaker)
    except tresor.GesperrtFehler as exc:
        besitzer = await session.get(User, speaker.owner_id)
        await _finish_thread(
            session,
            thread,
            "paused",
            f"Zugang gesperrt: {besitzer.name if besitzer else 'Der Besitzer'} muss "
            f"die Modell-Zugaenge freischalten ({exc})",
            anspruch,
        )
        return

    posts = await recent_posts(session, thread_id, settings.max_context_posts)
    messages = _build_messages(thread, participants, speaker, posts)

    post = Post(
        thread_id=thread.id,
        author_type="agent",
        author_name=speaker.name,
        agent_id=speaker.id,
        content="",
        status="streaming",
        round_no=thread.rounds_done + 1,
        model=speaker.model,
    )
    session.add(post)
    await session.commit()
    await bus.publish(
        {
            "type": "post.created",
            "thread_id": thread.id,
            "post_id": post.id,
            "author_type": "agent",
            "author_name": speaker.name,
            "agent_id": speaker.id,
            "round_no": post.round_no,
            "model": speaker.model,
            "created_at": to_utc_iso(post.created_at),
        }
    )

    chunks: list[str] = []
    buffer: list[str] = []
    last_flush = time.monotonic()
    usage: dict | None = None

    async def flush() -> None:
        nonlocal last_flush
        if not buffer:
            return
        text = "".join(buffer)
        buffer.clear()
        last_flush = time.monotonic()
        await bus.publish(
            {"type": "post.delta", "thread_id": thread.id, "post_id": post.id, "text": text}
        )

    try:
        async for kind, text, chunk_usage in llm.stream(speaker, messages, dk=dk):
            if kind == "usage":
                usage = chunk_usage
                continue
            chunks.append(text)
            buffer.append(text)
            long_enough = sum(len(part) for part in buffer) >= DELTA_FLUSH_CHARS
            slow_enough = time.monotonic() - last_flush >= DELTA_FLUSH_SECONDS
            if long_enough or slow_enough:
                await flush()
        await flush()
    except llm.LLMError as exc:
        post.content = "".join(chunks)
        post.status = "error"
        await session.commit()
        await bus.publish(
            {
                "type": "post.done",
                "thread_id": thread.id,
                "post_id": post.id,
                "status": "error",
                "content": post.content,
            }
        )
        await _fail_thread(
            session,
            thread.id,
            f"Modellaufruf von '{speaker.name}' fehlgeschlagen: {exc}",
            anspruch,
        )
        return

    post.content = "".join(chunks).strip()
    post.status = "complete"
    post.usage = usage
    thread.rounds_done += 1
    thread.last_agent_id = speaker.id
    thread.updated_at = utcnow()
    await session.commit()

    await bus.publish(
        {
            "type": "post.done",
            "thread_id": thread.id,
            "post_id": post.id,
            "status": "complete",
            "content": post.content,
            "usage": usage,
        }
    )

    # --- Hat der Beitrag etwas zu rechnen? ---------------------------------
    if thread.werkzeuge and settings.werkzeuge:
        if await _rechnen_lassen(session, thread, speaker, post, anspruch):
            return

    # --- Weiterlaufen oder aufhoeren? --------------------------------------
    aktuelle = await recent_posts(session, thread_id, settings.max_context_posts)
    if _alle_einig(aktuelle, erlaubt, thread.stop_phrase):
        await _finish_thread(
            session, thread, "done", "Alle Teilnehmer sind sich einig.", anspruch
        )
        return

    if thread.rounds_done >= min(thread.max_rounds, settings.max_rounds_hard):
        await _end_of_budget(session, thread, erlaubt, anspruch)
        return

    await session.commit()
    await _anspruch_geltend(
        session,
        thread.id,
        anspruch,
        next_run_at=utcnow() + timedelta(seconds=max(0, thread.pace_seconds)),
    )
    await session.refresh(thread)
    await publish_thread(thread)


async def _rechnen_lassen(
    session: AsyncSession,
    thread: Thread,
    speaker: Agent,
    post: Post,
    anspruch: str | None = None,
) -> bool:
    """Fuehrt die rechnen-Bloecke eines Beitrags aus.

    Liefert True, wenn gerechnet wurde und derselbe Agent gleich noch einmal
    dran ist, um das Ergebnis einzuordnen.
    """
    settings = get_settings()
    bloecke = werkzeuge.bloecke_finden(post.content)
    if not bloecke:
        return False

    # Wie oft hat dieser Agent schon in Folge gerechnet? Ohne Grenze koennte
    # er sich in einer Rechnung verlieren und niemand kaeme mehr zu Wort.
    letzte = await recent_posts(session, thread.id, 2 * settings.werkzeug_runden + 4)
    schon = 0
    for frueher in reversed(letzte[:-1]):
        if frueher.author_type == "tool":
            schon += 1
        elif frueher.author_type == "agent" and frueher.agent_id == speaker.id:
            continue
        else:
            break
    if schon >= settings.werkzeug_runden:
        await add_post(
            session,
            thread.id,
            author_type="system",
            author_name="Forum",
            content=(
                f"{speaker.name} hat {schon} mal in Folge gerechnet - "
                "jetzt ist wieder jemand anders dran."
            ),
            round_no=thread.rounds_done,
        )
        return False

    code = "\n".join(bloecke)
    log.info("Thread %s: %s rechnet (%d Zeichen)", thread.id, speaker.name, len(code))
    ausgabe, geglueckt = await werkzeuge.ausfuehren(
        code,
        settings.werkzeug_sekunden,
        settings.werkzeug_speicher_mb,
        settings.werkzeug_zeichen,
        settings.rechner_url,
    )

    # Als Block ablegen: so steht die Ausgabe in Festbreite im Forum und ist
    # auch fuer die Modelle sichtbar von Prosa getrennt. Enthaelt sie selbst
    # schon Blockzeichen, bleibt sie unangetastet.
    gezeigt = ausgabe if "```" in ausgabe else f"```text\n{ausgabe}\n```"

    await add_post(
        session,
        thread.id,
        author_type="tool",
        author_name="Rechner",
        agent_id=speaker.id,
        content=gezeigt,
        status="complete" if geglueckt else "error",
        round_no=thread.rounds_done,
    )

    # Derselbe Agent kommt gleich wieder dran: eine Ausgabe ohne Einordnung
    # nuetzt der Diskussion nichts.
    thread.naechster_agent_id = speaker.id
    await session.commit()
    await _anspruch_geltend(
        session,
        thread.id,
        anspruch,
        next_run_at=utcnow() + timedelta(seconds=max(0, thread.pace_seconds)),
    )
    await session.refresh(thread)
    await publish_thread(thread)
    return True


def _sagt_stopp(inhalt: str, phrase: str) -> bool:
    """Nur am Schluss des Beitrags zaehlen.

    Sonst beendet auch ein Modell die Diskussion, das die Phrase bloss
    erwaehnt ("... dann sage ich EINVERSTANDEN") statt sie zu setzen.
    """
    if not phrase:
        return False
    return phrase.upper() in inhalt.strip()[-120:].upper()


def _alle_einig(posts: list[Post], participants: list[Agent], phrase: str) -> bool:
    """Beendet wird erst, wenn ALLE im selben Durchgang zustimmen.

    Ein einzelner Agent kann die Diskussion damit nicht abwuergen - und
    genau das ist der Sinn: es soll eine Einigung sein, keine Ansage.

    Alles, was nicht von einem Agenten kommt - ein menschlicher Zwischenruf,
    ein beigesteuertes Dokument, ein neu hinzugezogener Teilnehmer - setzt die
    Zustimmung zurueck: was davor galt, bezog sich auf einen anderen Stand.
    """
    if not phrase or len(participants) < 2:
        return False

    letzte_stoerung = -1
    for index, post in enumerate(posts):
        # Eine eigene Rechnung ist keine neue Information von aussen.
        if post.author_type not in ("agent", "tool"):
            letzte_stoerung = index

    zuletzt: dict[str, str] = {}
    for post in posts[letzte_stoerung + 1 :]:
        if post.author_type == "agent" and post.agent_id:
            zuletzt[post.agent_id] = post.content

    return all(_sagt_stopp(zuletzt.get(agent.id, ""), phrase) for agent in participants)


# ---------------------------------------------------------------------------
# Sprecherwahl
# ---------------------------------------------------------------------------
async def _choose_speaker(session: AsyncSession, thread: Thread, participants: list[Agent]) -> Agent:
    # Ist jemand vorgemerkt - etwa weil er gerade gerechnet hat und das
    # Ergebnis einordnen soll - geht das vor.
    if thread.naechster_agent_id:
        vorgemerkt = next(
            (a for a in participants if a.id == thread.naechster_agent_id), None
        )
        thread.naechster_agent_id = None
        if vorgemerkt is not None:
            return vorgemerkt

    fallback = _round_robin(thread, participants)
    if thread.mode != "selector" or len(participants) < 2:
        return fallback

    moderator = None
    if thread.moderator_agent_id:
        moderator = await session.get(Agent, thread.moderator_agent_id)
    if moderator is None:
        moderator = participants[0]

    roster = "\n".join(f"- {a.name}: {a.role or 'keine Rollenbeschreibung'}" for a in participants)
    posts = await recent_posts(session, thread.id, 10)
    transcript = "\n\n".join(f"[{p.author_name}]: {p.content}" for p in posts) or "(noch leer)"

    prompt = (
        f"Du moderierst eine Fachdiskussion.\n\n"
        f"THEMA: {thread.title}\nZIEL: {thread.goal}\n\n"
        f"TEILNEHMER:\n{roster}\n\n"
        f"BISHERIGER VERLAUF:\n{transcript}\n\n"
        "Wer sollte als naechstes sprechen, damit die Diskussion am meisten "
        "vorankommt? Waehle nicht denselben Sprecher wie zuletzt, ausser es ist "
        "zwingend noetig.\n"
        "Antworte ausschliesslich mit dem Namen, ohne Begruendung, ohne Satzzeichen."
    )
    try:
        moderator_dk = await _datenschluessel(session, moderator)
        answer = await llm.complete(
            moderator, [{"role": "user", "content": prompt}], max_tokens=24, dk=moderator_dk
        )
    except (llm.LLMError, tresor.GesperrtFehler) as exc:
        log.warning("Sprecherwahl fehlgeschlagen, nutze Round-Robin: %s", exc)
        return fallback

    normalized = answer.strip().strip(" .,:;*\"'").lower()
    chosen = None
    for agent in participants:
        if agent.name.lower() == normalized:
            chosen = agent
            break
    if chosen is None:
        for agent in participants:
            if agent.name.lower() in normalized:
                chosen = agent
                break
    if chosen is None:
        log.info("Moderator lieferte '%s' - kein Treffer, nutze Round-Robin", answer[:60])
        return fallback

    # Harte Sperre gegen den Monolog: ein Moderator, der sich verhakt und
    # immer denselben Namen liefert, wuerde die Diskussion sonst zu einem
    # Selbstgespraech machen. Die Prompt-Bitte allein reicht dafuer nicht.
    if chosen.id == thread.last_agent_id:
        log.info("Moderator waehlte '%s' erneut - erzwinge Wechsel", chosen.name)
        return fallback
    return chosen


def _round_robin(thread: Thread, participants: list[Agent]) -> Agent:
    if thread.last_agent_id:
        for index, agent in enumerate(participants):
            if agent.id == thread.last_agent_id:
                return participants[(index + 1) % len(participants)]
    return participants[0]


# ---------------------------------------------------------------------------
# Prompt-Aufbau
# ---------------------------------------------------------------------------
def _build_messages(
    thread: Thread, participants: list[Agent], speaker: Agent, posts: list[Post]
) -> list[dict]:
    others = ", ".join(a.name for a in participants if a.id != speaker.id) or "niemand"
    # Liegt Material vor, aendert das den Auftrag: dann ist zu pruefen, nicht
    # frei zu diskutieren.
    hat_dokument = any(p.author_type == "document" and p.content for p in posts)
    system = (
        f"{speaker.persona}\n\n"
        "--- Forum-Kontext ---\n"
        f"Du bist '{speaker.name}' in einer schriftlichen Fachdiskussion.\n"
        f"Weitere Teilnehmer: {others}. Menschen koennen jederzeit mitschreiben; "
        "ihre Beitraege sind mit (Mensch) markiert und haben Vorrang.\n"
        f"THEMA: {thread.title}\n"
        f"ZIEL: {thread.goal or 'kein explizites Ziel gesetzt'}\n\n"
        "Regeln:\n"
        "- Schreibe genau einen Diskussionsbeitrag, hoechstens ca. 200 Woerter.\n"
        "- Wiederhole nicht, was schon gesagt wurde. Bringe etwas Neues oder "
        "widersprich konkret und begruendet.\n"
        "- Sprich andere Teilnehmer namentlich an, wenn du dich auf sie beziehst.\n"
        f"- Stelle deinem Beitrag KEIN '{speaker.name}:' voran - der Name steht schon dran.\n"
        "- Geht es um Programmierung, schreibe Code in einen Block mit "
        "Sprachangabe, etwa ```python. Solcher Code wird NICHT ausgefuehrt - "
        "er ist zum Lesen, Besprechen und Verbessern da. Beziehe dich auf "
        "konkrete Zeilen, statt allgemein zu bleiben.\n"
        + (
            "- Du kannst rechnen, statt zu schaetzen. Schreibe dazu einen Block:\n"
            "  ```rechnen\n"
            "  ... Python ...\n"
            "  ```\n"
            "  Er wird ausgefuehrt, die Ausgabe erscheint als naechster Beitrag, "
            "und danach bist du gleich wieder dran, um sie einzuordnen. Sichtbar "
            "ist nur, was du mit print() ausgibst.\n"
            "  Zur Verfuegung stehen: " + ", ".join(werkzeuge.verfuegbare_pakete()) + ".\n"
            "  Kein Netzwerk, kein Dateizugriff. Nutze das, wann immer eine Zahl, "
            "eine Umformung oder ein Gegenbeispiel zu pruefen ist - behaupte "
            "nichts, was du nachrechnen kannst.\n"
            if thread.werkzeuge
            else ""
        )
        + (
            "- Es liegt Material bei. Referiere es nicht, sondern arbeite "
            "daran.\n"
            if hat_dokument
            else ""
        )
        # Der eigentliche Auftrag haengt daran, was fuer ein Material es ist.
        + pruefarten.waehlen(thread.art).prompt(bool(thread.werkzeuge))
        + (
            "- Haeltst du das Ziel fuer erreicht, setze als allerletztes Wort "
            f"{thread.stop_phrase}. Das ist deine Zustimmung, kein Schlusswort: "
            "die Diskussion endet erst, wenn ALLE Teilnehmer im selben Durchgang "
            "zustimmen. Ist noch etwas offen oder siehst du einen Widerspruch, "
            "schreibe dieses Wort NICHT, sondern benenne, was fehlt."
            if thread.stop_phrase
            else "- Es gibt keine Abkuerzung zum Schluss: diskutiere, bis die "
            "vorgesehenen Runden aufgebraucht sind."
        )
    ).strip()

    messages: list[dict] = [{"role": "system", "content": system}]
    # Immer mit einer user-Nachricht beginnen: Anthropic verlangt das, und der
    # Zielsatz gehoert ohnehin an den Anfang.
    messages.append(
        {"role": "user", "content": f"Diskussionsauftrag: {thread.title}\n{thread.goal}".strip()}
    )

    for post in posts:
        if not post.content:
            continue
        if post.author_type == "agent" and post.agent_id == speaker.id:
            messages.append({"role": "assistant", "content": post.content})
        elif post.author_type == "tool":
            messages.append(
                {"role": "user", "content": f"--- Ausgabe der Rechnung ---\n{post.content}"}
            )
        elif post.author_type == "document":
            # Deutlich abgesetzt, damit ein langes Dokument nicht als
            # Gespraechsbeitrag missverstanden wird.
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"--- Beigesteuertes Dokument: {post.author_name} ---\n"
                        f"{post.content}\n"
                        "--- Ende des Dokuments ---"
                    ),
                }
            )
        else:
            marker = " (Mensch)" if post.author_type == "human" else ""
            messages.append(
                {"role": "user", "content": f"[{post.author_name}{marker}]: {post.content}"}
            )

    messages.append({"role": "user", "content": "Dein Beitrag:"})
    return messages


# ---------------------------------------------------------------------------
# Abschluss / Fehler
# ---------------------------------------------------------------------------
async def _end_of_budget(
    session: AsyncSession,
    thread: Thread,
    participants: list[Agent],
    anspruch: str | None = None,
) -> None:
    # Steht am Ende schon eine Synthese, kommt keine zweite dazu. Sonst
    # sammeln sich sie an, wenn ein Thema mehrfach an derselben Grenze
    # ankommt - und kosten jedes Mal einen Modellaufruf.
    letzte = await recent_posts(session, thread.id, 1)
    schon_zusammengefasst = bool(letzte) and "(Synthese)" in (letzte[-1].author_name or "")

    if thread.synthesize_on_end and not schon_zusammengefasst:
        moderator = None
        if thread.moderator_agent_id:
            moderator = await session.get(Agent, thread.moderator_agent_id)
        moderator = moderator or participants[0]
        posts = await recent_posts(session, thread.id, get_settings().max_context_posts)
        transcript = "\n\n".join(f"[{p.author_name}]: {p.content}" for p in posts)
        prompt = (
            f"Die Diskussion ist beendet.\n\n"
            f"THEMA: {thread.title}\nZIEL: {thread.goal}\n\n"
            f"VERLAUF:\n{transcript}\n\n"
            "Schreibe eine Abschluss-Synthese: Konsens, offene Streitpunkte, "
            "konkrete naechste Schritte. Maximal 300 Woerter, als Stichpunkte."
        )
        try:
            moderator_dk = await _datenschluessel(session, moderator)
            summary = await llm.complete(
                moderator, [{"role": "user", "content": prompt}], dk=moderator_dk
            )
            await add_post(
                session,
                thread.id,
                author_type="agent",
                author_name=f"{moderator.name} (Synthese)",
                content=summary,
                agent_id=moderator.id,
                round_no=thread.rounds_done,
                model=moderator.model,
            )
        except (llm.LLMError, tresor.GesperrtFehler) as exc:
            log.warning("Synthese fehlgeschlagen: %s", exc)

    # Welche der beiden Grenzen gegriffen hat, macht fuer das Weitermachen
    # einen Unterschied: die eigene laesst sich anheben, die harte nicht.
    grenze = get_settings().max_rounds_hard
    if thread.rounds_done >= grenze:
        schluss = f"Harte Grenze von {grenze} Runden erreicht."
    else:
        schluss = "Rundenbudget aufgebraucht."
    await _finish_thread(session, thread, "done", schluss, anspruch)


async def _finish_thread(
    session: AsyncSession,
    thread: Thread,
    status: str,
    detail: str,
    anspruch: str | None = None,
) -> None:
    # Erst festschreiben, was der Zug ohnehin erarbeitet hat (Rundenzaehler,
    # letzter Sprecher), dann erst ueber den Schlusszustand entscheiden.
    await session.commit()
    await _anspruch_geltend(
        session, thread.id, anspruch, status=status, status_detail=detail
    )
    await session.refresh(thread)
    await publish_thread(thread)


async def _fail_thread(
    session: AsyncSession, thread_id: str, detail: str, anspruch: str | None = None
) -> None:
    # Auch ein Fehlschlag darf einen Eingriff nicht ueberschreiben: wer
    # "Beenden" gedrueckt hat, soll nicht stattdessen "error" vorfinden.
    await session.rollback()
    await _anspruch_geltend(
        session, thread_id, anspruch, status="error", status_detail=detail
    )
    thread = await session.get(Thread, thread_id)
    if thread is not None:
        await publish_thread(thread)


async def publish_thread(thread: Thread) -> None:
    await bus.publish(
        {
            "type": "thread.update",
            "thread_id": thread.id,
            "status": thread.status,
            "status_detail": thread.status_detail,
            "rounds_done": thread.rounds_done,
            "max_rounds": thread.max_rounds,
        }
    )


# ---------------------------------------------------------------------------
# Hilfsfunktionen (auch von der API genutzt)
# ---------------------------------------------------------------------------
async def list_participants(session: AsyncSession, thread_id: str) -> list[Agent]:
    rows = (
        (
            await session.execute(
                select(Participant)
                .where(Participant.thread_id == thread_id)
                .order_by(Participant.position, Participant.id)
            )
        )
        .scalars()
        .all()
    )
    return [row.agent for row in rows if row.agent is not None]


async def recent_posts(session: AsyncSession, thread_id: str, limit: int) -> list[Post]:
    rows = (
        (
            await session.execute(
                select(Post)
                .where(Post.thread_id == thread_id, Post.status != "streaming")
                .order_by(Post.created_at.desc(), Post.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


async def add_post(session: AsyncSession, thread_id: str, **kwargs) -> Post:
    kwargs.setdefault("status", "complete")
    post = Post(thread_id=thread_id, **kwargs)
    session.add(post)
    await session.commit()
    await bus.publish(
        {
            "type": "post.created",
            "thread_id": thread_id,
            "post_id": post.id,
            "author_type": post.author_type,
            "author_name": post.author_name,
            "agent_id": post.agent_id,
            "round_no": post.round_no,
            "model": post.model,
            "created_at": to_utc_iso(post.created_at),
        }
    )
    await bus.publish(
        {
            "type": "post.done",
            "thread_id": thread_id,
            "post_id": post.id,
            "status": "complete",
            "content": post.content,
        }
    )
    return post
