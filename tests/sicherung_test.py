"""Sichern und Einspielen - der Ernstfall.

Geprueft wird die Behauptung, auf die es ankommt: ein Abbild laesst sich auf
einem frisch aufgesetzten Server mit einem NEUEN AGORA_SECRET_KEY einspielen,
und die Modell-Zugaenge sind danach immer noch zu oeffnen. Das ist der Weg
nach einem Einbruch - neuer Server, neuer Schluessel, altes Abbild.

    python tests/sicherung_test.py
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = pathlib.Path(tempfile.gettempdir()) / "agora_sicherung.db"
if DB.exists():
    DB.unlink()

from cryptography.fernet import Fernet  # noqa: E402

ALTER_SCHLUESSEL = Fernet.generate_key().decode()
NEUER_SCHLUESSEL = Fernet.generate_key().decode()

os.environ["AGORA_SECRET_KEY"] = ALTER_SCHLUESSEL
os.environ["AGORA_ADMIN_TOKEN"] = "sicherung-admin-token"
os.environ["AGORA_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB.as_posix()}"
os.environ["AGORA_WORKER_ENABLED"] = "true"
os.environ["AGORA_WORKER_INTERVAL"] = "0.2"

import httpx  # noqa: E402

from app import crypto, llm, main, sicherung, tresor  # noqa: E402
from app.config import get_settings  # noqa: E402

HEAD = {"Authorization": "Bearer sicherung-admin-token"}
PIN = "Geheime-PIN-2026"
ABBILD_PW = "ein ganzer Satz als Passwort"

# So eigenwillig, dass ein Zufallstreffer ausgeschlossen ist.
PERSONA = "Du bist Hypatia und antwortest stets mit einer Gegenfrage."

# Was die Agenten zu sehen bekommen, wird hier mitgeschrieben - daran laesst
# sich pruefen, ob die Persona nach dem Einspielen noch im Prompt steht.
PROMPTS: list[str] = []


async def falsches_modell(agent, messages, dk=None):
    PROMPTS.append(str(messages[0].get("content") or ""))
    yield "delta", f"Weiter geht es, sagt {agent.name}.", None
    yield "usage", "", None


async def falscher_abschluss(agent, messages, max_tokens=None, dk=None):
    return agent.name


llm.stream = falsches_modell
llm.complete = falscher_abschluss


def schluessel_wechseln(neuer: str) -> None:
    """Den Serverschluessel austauschen, als waere es ein anderer Server."""
    os.environ["AGORA_SECRET_KEY"] = neuer
    get_settings.cache_clear()
    crypto._fernet = None


async def main_test() -> int:
    app = main.app
    transport = httpx.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            # --- Bestand aufbauen ----------------------------------------
            r = await c.post("/api/me/pin", headers=HEAD, json={"neue_pin": PIN})
            assert r.status_code == 200, r.text

            r = await c.post("/api/credentials", headers=HEAD, json={
                "label": "Anthropic", "provider": "anthropic",
                "api_key": "sk-ant-geheim-123"})
            assert r.status_code == 201, r.text

            r = await c.post("/api/agents", headers=HEAD, json={
                "name": "Kritikerin", "role": "prueft", "model": "anthropic/dummy",
                "persona": PERSONA, "credential_id": r.json()["id"]})
            assert r.status_code == 201, r.text
            agent = r.json()

            r = await c.post("/api/forums", headers=HEAD, json={"name": "Physik"})
            assert r.status_code == 201, r.text

            r = await c.post("/api/threads", headers=HEAD, json={
                "title": "Etwas Wichtiges", "goal": "Soll ein Abbild ueberleben.",
                "agent_ids": [agent["id"]], "mode": "roundrobin",
                "max_rounds": 1, "pace_seconds": 0, "start_now": False})
            assert r.status_code == 201, r.text
            thema = r.json()

            r = await c.post(f"/api/threads/{thema['id']}/posts", headers=HEAD,
                             json={"content": "Ein Beitrag, der nicht verloren gehen darf."})
            assert r.status_code in (200, 201), r.text
            print("[ok] Bestand angelegt: Person mit PIN, Zugang, Agent, Forum, Thema, Beitrag")

            # --- Sichern --------------------------------------------------
            r = await c.post("/api/admin/sicherung/erstellen", headers=HEAD,
                             json={"passwort": ""})
            assert r.status_code == 200, r.text
            abbild = r.content
            assert r.headers["content-type"].startswith("application/gzip"), r.headers
            assert "agora-sicherung-" in r.headers.get("content-disposition", "")
            kopf, _ = sicherung.lesen(abbild)
            assert kopf["zaehler"]["users"] == 1, kopf
            assert kopf["zaehler"]["credentials"] == 1, kopf
            assert kopf["zaehler"]["posts"] >= 1, kopf
            assert kopf["am_serverschluessel"] == 0, "Zugang haengt noch am Serverschluessel"
            print(f"[ok] Abbild erstellt, {len(abbild)} Byte, Inhalt: {kopf['zaehler']}")

            # Der fluechtige Zustand geht bewusst nicht mit.
            _, tabellen = sicherung.lesen(abbild)
            assert "dk_unlocked" not in tabellen["users"][0], tabellen["users"][0].keys()
            assert "locked_by" not in tabellen["threads"][0], tabellen["threads"][0].keys()
            print("[ok] Freischaltung und Worker-Lease sind nicht im Abbild")

            # --- Nur hineinschauen veraendert nichts ----------------------
            r = await c.post("/api/admin/sicherung/pruefen", headers=HEAD,
                             files={"datei": ("abbild.gz", abbild, "application/gzip")})
            assert r.status_code == 200, r.text
            assert r.json()["gleicher_server"] is True, r.json()
            assert (await c.get("/api/agents", headers=HEAD)).json(), "Bestand angetastet"

            # --- Kein Einspielen ohne Bestaetigung ------------------------
            r = await c.post("/api/admin/sicherung/einspielen", headers=HEAD,
                             files={"datei": ("abbild.gz", abbild, "application/gzip")})
            assert r.status_code == 400, r.text
            print("[ok] Pruefen veraendert nichts, Einspielen verlangt eine Bestaetigung")

            # --- Passwort auf dem Abbild ----------------------------------
            r = await c.post("/api/admin/sicherung/erstellen", headers=HEAD,
                             json={"passwort": ABBILD_PW})
            assert r.status_code == 200, r.text
            geschuetzt = r.content
            assert r.headers["content-disposition"].endswith('.agora"'), r.headers
            assert sicherung.ist_verschluesselt(geschuetzt), geschuetzt[:40]
            # Die Beitraege duerfen nicht mehr im Klartext darin stehen.
            assert b"nicht verloren gehen darf" not in geschuetzt
            print(f"[ok] Abbild mit Passwort, {len(geschuetzt)} Byte, nichts lesbar darin")

            r = await c.post("/api/admin/sicherung/erstellen", headers=HEAD,
                             json={"passwort": "zu kurz"})
            assert r.status_code == 400, r.text

            dat = {"datei": ("abbild.agora", geschuetzt, "application/octet-stream")}
            # Ohne Passwort: keine Stoerung, eine Rueckfrage.
            r = await c.post("/api/admin/sicherung/pruefen", headers=HEAD, files=dat)
            assert r.status_code == 200 and r.json() == {"passwort_noetig": True}, r.text
            # Mit falschem: klare Absage.
            r = await c.post("/api/admin/sicherung/pruefen", headers=HEAD, files=dat,
                             data={"passwort": "falsch aber lang genug"})
            assert r.status_code == 400 and "asswort" in r.text, r.text
            # Mit richtigem: derselbe Inhalt wie ohne Passwort.
            r = await c.post("/api/admin/sicherung/pruefen", headers=HEAD, files=dat,
                             data={"passwort": ABBILD_PW})
            assert r.status_code == 200, r.text
            assert r.json()["verschluesselt"] is True, r.json()
            assert r.json()["zaehler"] == kopf["zaehler"], r.json()
            # Und einspielen geht ohne Passwort auch dann nicht.
            r = await c.post("/api/admin/sicherung/einspielen", headers=HEAD, files=dat,
                             data={"bestaetigt": "true"})
            assert r.status_code == 400, r.text
            print("[ok] Ohne Passwort Rueckfrage, mit falschem Absage, mit richtigem lesbar")

            # --- Kein Einspielen von Unsinn -------------------------------
            for name, inhalt in (
                ("leer.gz", b""),
                ("kein-gzip.gz", b"das ist einfach Text"),
            ):
                r = await c.post("/api/admin/sicherung/pruefen", headers=HEAD,
                                 files={"datei": (name, inhalt, "application/gzip")})
                assert r.status_code == 400, f"{name}: {r.text}"
            print("[ok] Beschaedigte Dateien werden abgewiesen")

            # --- Nur Admins -----------------------------------------------
            r = await c.post("/api/users", headers=HEAD,
                             json={"name": "gast", "is_admin": False})
            assert r.status_code == 201, r.text
            gast_token = r.json()["token"]
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as gast:
                a = await gast.post("/api/login",
                                    json={"token": gast_token, "pin": "Gast-PIN-2026"})
                assert a.status_code == 200, a.text
                # Die Anmeldung setzt das Cookie - damit reicht der blosse Aufruf.
                a = await gast.post("/api/admin/sicherung/erstellen",
                                    json={"passwort": ""})
                assert a.status_code == 403, a.text
            print("[ok] Nur Admins duerfen sichern")

    # --- Der Ernstfall: neuer Server, neuer Schluessel --------------------
    # Erst den Verbindungspool schliessen: unter Windows laesst sich eine
    # Datei nicht loeschen, solange noch jemand ein Handle darauf haelt.
    # Unter Linux ginge es auch ohne - der Test soll aber ueberall laufen.
    from app.db import engine as _motor

    await _motor.dispose()
    DB.unlink()
    schluessel_wechseln(NEUER_SCHLUESSEL)
    assert get_settings().secret_key == NEUER_SCHLUESSEL

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            leer = (await c.get("/api/agents", headers=HEAD)).json()
            assert leer == [], "Der neue Server sollte leer sein"
            print("[ok] Frischer Server mit neuem AGORA_SECRET_KEY, Bestand leer")

            r = await c.post("/api/admin/sicherung/pruefen", headers=HEAD,
                             files={"datei": ("abbild.gz", abbild, "application/gzip")})
            assert r.status_code == 200, r.text
            assert r.json()["gleicher_server"] is False, "Schluesselwechsel nicht erkannt"
            print("[ok] Das Abbild erkennt, dass der Server ein anderer ist")

            # Eingespielt wird das GESCHUETZTE Abbild - der Ernstfall bringt
            # eine Datei mit, die unterwegs war.
            r = await c.post("/api/admin/sicherung/einspielen", headers=HEAD,
                             data={"bestaetigt": "true", "passwort": ABBILD_PW},
                             files={"datei": ("abbild.agora", geschuetzt,
                                              "application/octet-stream")})
            assert r.status_code == 200, r.text
            print(f"[ok] Eingespielt: {r.json()['geschrieben']}")

            # Das Admin-Token aus der Umgebung gilt weiter - sonst waere
            # nach dem Einspielen niemand mehr drin.
            me = await c.get("/api/me", headers=HEAD)
            assert me.status_code == 200, "Admin ausgesperrt!"

            agenten = (await c.get("/api/agents", headers=HEAD)).json()
            assert [a["name"] for a in agenten] == ["Kritikerin"], agenten
            foren = (await c.get("/api/forums", headers=HEAD)).json()
            assert [f["name"] for f in foren] == ["Physik"], foren
            themen = (await c.get("/api/threads", headers=HEAD)).json()
            assert themen and themen[0]["title"] == "Etwas Wichtiges", themen
            beitraege = (await c.get(f"/api/threads/{themen[0]['id']}/posts",
                                     headers=HEAD)).json()
            assert any("nicht verloren gehen darf" in (b["content"] or "")
                       for b in beitraege), beitraege
            print("[ok] Foren, Themen, Beitraege und Agenten sind wieder da")

            # --- Der eigentliche Beweis ------------------------------------
            # Die Freischaltung ist fort, also muss die PIN wieder her.
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as sitzung:
                a = await sitzung.post("/api/login",
                                       json={"token": "sicherung-admin-token", "pin": PIN})
                assert a.status_code == 200, a.text
                assert a.json()["unlocked_until"], "PIN hat nicht freigeschaltet"

            # Und der Zugang laesst sich mit dem Datenschluessel wieder oeffnen,
            # obwohl der Serverschluessel ein voellig anderer ist.
            from sqlalchemy import select

            from app.db import SessionLocal
            from app.models import Credential, User

            async with SessionLocal() as sitzung:
                nutzer = (await sitzung.execute(
                    select(User).where(User.name == "admin"))).scalar_one()
                zugang = (await sitzung.execute(
                    select(Credential).where(Credential.owner_id == nutzer.id))).scalar_one()
                dk = tresor.auspacken(PIN, nutzer.dk_salt, nutzer.dk_wrapped)
                klartext = tresor.zugang_entschluesseln(dk, zugang.api_key_enc)

            assert klartext == "sk-ant-geheim-123", klartext
            print("[ok] MODELL-ZUGANG LESBAR trotz neuem Serverschluessel")

            # --- Weiterarbeiten mit denselben Personas --------------------
            # Ein Abbild ist nur dann etwas wert, wenn man danach dort
            # weitermacht, wo man aufgehoert hat.
            agent = (await c.get("/api/agents", headers=HEAD)).json()[0]
            assert agent["persona"] == PERSONA, agent["persona"]
            print("[ok] Die Persona steht Wort fuer Wort wieder da")

            # Erst zur Ruhe kommen lassen: das eingespielte Thema war aktiv
            # (ein menschlicher Beitrag hatte es geweckt) und laeuft nach dem
            # Einspielen von selbst an. Mitten in seinen Abschluss hinein zu
            # verlaengern misst nichts.
            thema = (await c.get("/api/threads", headers=HEAD)).json()[0]
            for _ in range(300):
                zustand = (await c.get(f"/api/threads/{thema['id']}",
                                       headers=HEAD)).json()
                if zustand["status"] in ("done", "error", "paused"):
                    break
                await asyncio.sleep(0.2)
            assert zustand["status"] == "done", zustand
            print("[ok] Das eingespielte Thema lief an und kam zum Abschluss")

            vorher = len((await c.get(f"/api/threads/{thema['id']}/posts",
                                      headers=HEAD)).json())
            PROMPTS.clear()

            # Wiederbeleben: "Weiter" stockt bei aufgebrauchtem Budget selbst
            # auf - ein beendetes Thema laesst sich damit fortsetzen.
            r = await c.post(f"/api/threads/{thema['id']}/control", headers=HEAD,
                             json={"action": "extend", "rounds": 2})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "active", r.json()

            # Auf einen AGENTEN-Beitrag warten, nicht auf irgendeinen: das
            # Forum schreibt auch Systemmeldungen, und auf die zu stossen
            # hiesse abzubrechen, bevor ueberhaupt ein Modell dran war.
            neue = []
            for _ in range(300):
                await asyncio.sleep(0.2)
                jetzt = (await c.get(f"/api/threads/{thema['id']}/posts",
                                     headers=HEAD)).json()
                neue = jetzt[vorher:]
                if any(b["author_type"] == "agent" for b in neue):
                    break
            angekommen = [(b["author_type"], (b["content"] or "")[:60]) for b in neue]
            assert any(b["author_type"] == "agent" for b in neue), angekommen
            assert PROMPTS, f"Kein Modellaufruf. Stattdessen kam: {angekommen}"
            assert PERSONA in PROMPTS[0], PROMPTS[0][:300]
            print("[ok] Thema wiederbelebt, der Agent spricht mit seiner Persona weiter")

    print("\nALLE TESTS BESTANDEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_test()))
