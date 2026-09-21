"""End-to-End-Rauchtest ohne echte Modellaufrufe."""
import asyncio
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = pathlib.Path(tempfile.gettempdir()) / "agora_smoke.db"
if DB.exists():
    DB.unlink()

# Frisch erzeugen statt fest eintragen: ein Schluessel im Repository wird
# frueher oder spaeter irgendwo produktiv kopiert.
from cryptography.fernet import Fernet  # noqa: E402

os.environ["AGORA_SECRET_KEY"] = Fernet.generate_key().decode()
os.environ["AGORA_ADMIN_TOKEN"] = "smoke-admin-token"
os.environ["AGORA_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB.as_posix()}"
os.environ["AGORA_WORKER_ENABLED"] = "true"
os.environ["AGORA_WORKER_INTERVAL"] = "0.2"
os.environ["AGORA_WERKZEUGE"] = "true"

# Eine Datenbank im Stand von vorher anlegen: den drei neuen Spalten muss
# der Start sie selbst hinzufuegen, sonst laeuft jedes Update in
# "no such column" - und zwar erst beim ersten Zugriff.
import sqlite3  # noqa: E402

con = sqlite3.connect(DB)
con.execute(
    "CREATE TABLE credentials (id VARCHAR(32) NOT NULL PRIMARY KEY, "
    "owner_id VARCHAR(32) NOT NULL, label VARCHAR(80), provider VARCHAR(40), "
    "api_key_enc TEXT, api_base VARCHAR(300), created_at DATETIME)"
)
con.commit()
con.close()

import httpx  # noqa: E402

from app import llm, main  # noqa: E402

CALLS = {"stream": 0, "complete": 0}


async def fake_stream(agent, messages, dk=None):
    CALLS["stream"] += 1
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user", "Anthropic verlangt user als erste Nachricht"
    assert messages[-1]["role"] == "user"
    text = f"Antwort Nr. {CALLS['stream']} von {agent.name}. " * 3
    for index in range(0, len(text), 25):
        yield "delta", text[index : index + 25], None
        await asyncio.sleep(0.005)
    yield "usage", "", {"prompt_tokens": 10, "completion_tokens": 5}


async def fake_complete(agent, messages, max_tokens=None, dk=None):
    CALLS["complete"] += 1
    return "Kritikerin"


llm.stream = fake_stream
llm.complete = fake_complete

def pdf_mit_text(text: str) -> bytes:
    """Ein minimales, gueltiges PDF - ohne Fremdbibliothek zum Erzeugen.

    Die Querverweistabelle muss byte-genau stimmen, sonst muss der Leser
    raten und der Test prueft am Ende die Ratekunst von pypdf statt unseren
    Weg. Deshalb werden die Stellen gerechnet.
    """
    inhalt = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("latin-1")
    objekte = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(inhalt)).encode() + b" >>\nstream\n" + inhalt + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    aus = bytearray(b"%PDF-1.4\n")
    stellen = []
    for nummer, koerper in enumerate(objekte, start=1):
        stellen.append(len(aus))
        aus += f"{nummer} 0 obj\n".encode() + koerper + b"\nendobj\n"
    xref = len(aus)
    aus += f"xref\n0 {len(objekte) + 1}\n".encode() + b"0000000000 65535 f \n"
    for stelle in stellen:
        aus += f"{stelle:010d} 00000 n \n".encode()
    aus += f"trailer\n<< /Size {len(objekte) + 1} /Root 1 0 R >>\n".encode()
    aus += f"startxref\n{xref}\n%%EOF\n".encode()
    return bytes(aus)


HEAD = {"Authorization": "Bearer smoke-admin-token"}


async def main_test() -> int:
    app = main.app
    events = []

    async with app.router.lifespan_context(app):
        from app.events import bus

        async def collect():
            async with bus.subscribe() as queue:
                while True:
                    events.append(await queue.get())

        collector = asyncio.create_task(collect())

        transport = httpx.ASGITransport(app=app)

        async def anmelden(token, pin=""):
            # Eigener Client je Anmeldung: sonst bleibt das Cookie am
            # gemeinsamen Client haengen und faelscht spaetere Pruefungen.
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as sitzung:
                return await sitzung.post("/api/login", json={"token": token, "pin": pin})

        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            me = (await c.get("/api/me", headers=HEAD)).json()
            assert me["name"] == "admin", me
            print("[ok] Login als", me["name"])

            # Ohne PIN gibt es keinen Datenschluessel - und ohne den keinen
            # Modell-Zugang. Das ist der Kern der Umschlagverschluesselung.
            r = await c.post("/api/credentials", headers=HEAD, json={
                "label": "Zu frueh", "provider": "openai", "api_key": "x"})
            assert r.status_code == 409, "ohne Freischaltung darf nichts angelegt werden"

            r = await c.post("/api/me/pin", headers=HEAD, json={"neue_pin": "Admin-PIN-2026"})
            assert r.status_code == 200, r.text
            assert r.json()["unlocked_until"], "PIN setzen schaltet frei"
            print("[ok] Ohne PIN kein Datenschluessel, mit PIN sofort freigeschaltet")

            r = await c.post(
                "/api/credentials",
                headers=HEAD,
                json={"label": "Lokal vLLM", "provider": "openai", "api_key": "geheim",
                      "api_base": "http://vllm:8000/v1"},
            )
            assert r.status_code == 201, r.text
            cred = r.json()
            # Genau pruefen: der Wert darf nicht raus, und es darf kein Feld
            # geben, das ihn enthaelt. "api_key" als *Wert* von auth_style
            # ist dagegen in Ordnung.
            assert "geheim" not in r.text, "Der Schluessel darf nie ausgeliefert werden!"
            assert '"api_key":' not in r.text, r.text
            assert "api_key_enc" not in r.text, r.text
            assert cred["has_key"] is True
            assert cred["auth_style"] == "api_key", cred
            print("[ok] Credential angelegt, Key nicht in der Antwort,")
            print("     alte Datenbank wurde um die neuen Spalten ergaenzt")

            # Anmeldearten fuer Anbieter, die keinen api_key nehmen
            r = await c.post("/api/credentials", headers=HEAD, json={
                "label": "Gateway mit Bearer", "provider": "openai",
                "api_key": "tok", "auth_style": "bearer",
                "api_base": "https://gateway.example/v1"})
            assert r.status_code == 201 and r.json()["auth_style"] == "bearer", r.text
            r = await c.post("/api/credentials", headers=HEAD, json={
                "label": "Kaputtes JSON", "provider": "openai",
                "api_key": "x", "extra_json": "{nicht json"})
            assert r.status_code == 400, r.text
            print("[ok] Anmeldearten waehlbar, ungueltiges JSON wird abgewiesen")

            agents = []
            for name, role in [("Architekt", "entwirft"), ("Kritikerin", "prueft")]:
                r = await c.post(
                    "/api/agents",
                    headers=HEAD,
                    json={"name": name, "role": role, "model": "openai/dummy",
                          "persona": f"Du bist {name}.", "credential_id": cred["id"]},
                )
                assert r.status_code == 201, r.text
                agents.append(r.json())
            print("[ok] 2 Agenten angelegt")

            # Foren: schachteln, Ringe verhindern, Oberforum sammelt ein
            async def forum(name, eltern=None):
                r = await c.post("/api/forums", headers=HEAD,
                                 json={"name": name, "parent_id": eltern})
                assert r.status_code == 201, r.text
                return r.json()["id"]

            ober = await forum("Forschung")
            mitte = await forum("Datenarchitektur", ober)
            unten = await forum("Replikation", mitte)
            r = await c.patch(f"/api/forums/{ober}", headers=HEAD,
                              json={"parent_id": unten})
            assert r.status_code == 400, "Ring haette abgelehnt werden muessen"
            r = await c.patch(f"/api/forums/{ober}", headers=HEAD, json={"parent_id": ober})
            assert r.status_code == 400, "Selbstbezug haette abgelehnt werden muessen"
            r = await c.delete(f"/api/forums/{mitte}", headers=HEAD)
            assert r.status_code == 409, "Nicht leeres Forum haette bleiben muessen"
            print("[ok] Foren schachteln, Ringe und volle Foren abgewiesen")

            r = await c.post(
                "/api/threads",
                headers=HEAD,
                json={
                    "title": "Global verteilte Datenbank",
                    "goal": "Ansaetze vergleichen und empfehlen.",
                    "agent_ids": [a["id"] for a in agents],
                    "mode": "selector",
                    "moderator_agent_id": agents[0]["id"],
                    "max_rounds": 3,
                    "pace_seconds": 0,
                    "forum_id": unten,
                },
            )
            assert r.status_code == 201, r.text
            thread = r.json()
            assert len(thread["participants"]) == 2
            print("[ok] Thread gestartet:", thread["id"])

            async def themen(filter_wert):
                r = await c.get(f"/api/threads?forum_id={filter_wert}", headers=HEAD)
                assert r.status_code == 200, r.text
                return [t["id"] for t in r.json()]

            assert thread["id"] in await themen(unten), "im eigenen Forum"
            assert thread["id"] in await themen(ober), "Oberforum sammelt Unterforen ein"
            assert thread["id"] not in await themen("-"), "gehoert nicht zu den unsortierten"
            print("[ok] Themen sind gefiltert, Oberforum sammelt seine Unterforen ein")

            for _ in range(200):
                await asyncio.sleep(0.25)
                state = (await c.get(f"/api/threads/{thread['id']}", headers=HEAD)).json()
                if state["status"] in ("done", "error"):
                    break
            assert state["status"] == "done", (state["status"], state["status_detail"])
            assert state["rounds_done"] == 3, state
            print("[ok] Autonom bis Rundenlimit gelaufen:", state["status_detail"])

            posts = (await c.get(f"/api/threads/{thread['id']}/posts", headers=HEAD)).json()
            assert len(posts) == 4, [p["author_name"] for p in posts]
            assert len({p["author_name"] for p in posts[:3]}) == 2, "Sprecher muessen wechseln"
            assert posts[-1]["author_name"].endswith("(Synthese)"), posts[-1]["author_name"]
            assert all(p["status"] == "complete" for p in posts)
            assert all(p["content"] for p in posts)
            # Sprecherwahl: der Moderator hat "Kritikerin" geliefert, aber nie
            # zweimal am Stueck derselbe Sprecher (Round-Robin als Fallback).
            print("[ok] Beitraege:", [p["author_name"] for p in posts])

            r = await c.post(
                f"/api/threads/{thread['id']}/posts",
                headers=HEAD,
                json={"content": "Bitte den Kostenaspekt beruecksichtigen.", "resume": True},
            )
            assert r.status_code == 201, r.text
            state = (await c.get(f"/api/threads/{thread['id']}", headers=HEAD)).json()
            assert state["status"] == "active" and state["max_rounds"] == 6, state
            print("[ok] Menschlicher Zwischenruf weckt den Thread wieder auf")

            for _ in range(200):
                await asyncio.sleep(0.25)
                state = (await c.get(f"/api/threads/{thread['id']}", headers=HEAD)).json()
                if state["status"] in ("done", "error"):
                    break
            assert state["status"] == "done", state
            posts = (await c.get(f"/api/threads/{thread['id']}/posts", headers=HEAD)).json()
            human = [p for p in posts if p["author_type"] == "human"]
            assert len(human) == 1
            print("[ok] Weitergelaufen, jetzt", len(posts), "Beitraege")

            r = await c.post(
                f"/api/threads/{thread['id']}/control",
                headers=HEAD,
                json={"action": "pause"},
            )
            assert r.json()["status"] == "paused"
            print("[ok] Pause/Steuerung funktioniert")

            r = await c.post("/api/users", headers=HEAD, json={"name": "kollege"})
            assert r.status_code == 201, r.text
            einmal = r.json()["token"]
            assert r.json()["expires_at"], "Einmal-Token braucht ein Ablaufdatum"

            # Ein Einmal-Token taugt NUR zum Anmelden, nicht fuer die API.
            r = await c.get("/api/me", headers={"Authorization": f"Bearer {einmal}"})
            assert r.status_code == 401, "Einmal-Token darf keine API-Aufrufe erlauben"

            # Ohne PIN oder mit zu schwacher wird nichts eingeloest.
            r = await anmelden(einmal)
            assert r.status_code == 400, "Einloesen ohne PIN"
            r = await anmelden(einmal, "111111")
            assert r.status_code == 400, "zu einfache PIN"

            # Einloesen: es kommt ein neues, dauerhaftes Token zurueck.
            r = await anmelden(einmal, "Kollege-PIN-42")
            assert r.status_code == 200, r.text
            assert r.json()["has_pin"] is True, r.json()
            other = r.json()["new_token"]
            assert other and other != einmal, r.json()

            # Das Einmal-Token ist danach wertlos - auch fuer den, der es ausgab.
            r = await anmelden(einmal, "Kollege-PIN-42")
            assert r.status_code == 401, "verbrauchtes Einmal-Token muss abgewiesen werden"

            r = await c.get("/api/me", headers={"Authorization": f"Bearer {other}"})
            assert r.json()["name"] == "kollege"
            # Beim zweiten Anmelden wird nicht noch einmal getauscht.
            r = await anmelden(other, "Kollege-PIN-42")
            assert r.status_code == 200 and r.json()["new_token"] is None, r.text
            print("[ok] Einmal-Token wird beim Anmelden gegen ein eigenes getauscht")
            r = await c.get("/api/credentials", headers={"Authorization": f"Bearer {other}"})
            assert r.json() == [], "Credentials duerfen nicht geteilt werden"
            r = await c.get("/api/agents", headers={"Authorization": f"Bearer {other}"})
            assert len(r.json()) == 2, "oeffentliche Agenten muessen sichtbar sein"
            # Fremden Zugang unterschieben: beim Anlegen UND beim Aendern
            r = await c.post("/api/agents", headers={"Authorization": f"Bearer {other}"},
                             json={"name": "Schmarotzer", "model": "openai/x",
                                   "credential_id": cred["id"]})
            assert r.status_code == 400, "fremder Zugang beim Anlegen"
            r = await c.post("/api/agents", headers={"Authorization": f"Bearer {other}"},
                             json={"name": "Eigener", "model": "openai/x"})
            assert r.status_code == 201, r.text
            fremd = r.json()["id"]
            r = await c.patch(f"/api/agents/{fremd}",
                              headers={"Authorization": f"Bearer {other}"},
                              json={"credential_id": cred["id"]})
            assert r.status_code == 400, "fremder Zugang beim Aendern"
            print("[ok] Fremde Modell-Zugaenge lassen sich nicht unterschieben")

            print("[ok] Zweiter User: Agenten geteilt, Keys nicht")

            r = await c.get("/api/me")
            assert r.status_code == 401
            r = await c.get("/api/users", headers={"Authorization": f"Bearer {other}"})
            assert r.status_code == 403
            # Zustimmung nur eines Agenten darf nichts beenden - erst wenn
            # alle im selben Durchgang zustimmen, ist die Diskussion vorbei.
            EINIG = set()

            async def stopper(agent, messages, dk=None):
                zustimmung = " EINVERSTANDEN" if agent.name in EINIG else ""
                text = f"Beitrag von {agent.name}.{zustimmung}"
                yield "delta", text, None
                yield "usage", "", None

            async def lauf_bis_ende(daten):
                r = await c.post("/api/threads", headers=HEAD, json=daten)
                assert r.status_code == 201, r.text
                angelegt = r.json()
                for _ in range(300):
                    await asyncio.sleep(0.2)
                    zustand = (
                        await c.get(f"/api/threads/{angelegt['id']}", headers=HEAD)
                    ).json()
                    if zustand["status"] in ("done", "error"):
                        return zustand
                raise AssertionError("Thread wurde nicht fertig")

            # Nur "Architekt" stimmt zu - das darf nichts beenden.
            EINIG.clear()
            EINIG.add("Architekt")
            llm.stream = stopper
            zustand = await lauf_bis_ende({
                "title": "Einer allein reicht nicht",
                "goal": "Konsens pruefen.",
                "agent_ids": [a["id"] for a in agents],
                "mode": "roundrobin", "max_rounds": 6, "pace_seconds": 0})
            assert zustand["rounds_done"] == 6, zustand
            assert zustand["status_detail"] == "Rundenbudget aufgebraucht.", zustand
            print("[ok] Zustimmung eines Einzelnen beendet nichts")

            # Jetzt stimmen beide zu: nach dem zweiten Beitrag ist Schluss.
            EINIG.add("Kritikerin")
            zustand = await lauf_bis_ende({
                "title": "Beide einig",
                "goal": "Konsens pruefen.",
                "agent_ids": [a["id"] for a in agents],
                "mode": "roundrobin", "max_rounds": 9, "pace_seconds": 0})
            assert zustand["rounds_done"] == 2, zustand
            assert "einig" in zustand["status_detail"], zustand
            llm.stream = fake_stream
            print("[ok] Einigkeit aller beendet die Diskussion")

            # Nachtraeglich eine Position dazuholen
            r = await c.post("/api/agents", headers=HEAD, json={
                "name": "Betreiber", "role": "bewertet Aufwand",
                "model": "openai/dummy", "credential_id": cred["id"]})
            betreiber = r.json()
            r = await c.post("/api/threads", headers=HEAD, json={
                "title": "Position fehlt",
                "goal": "Nachtraegliches Dazuholen pruefen.",
                "agent_ids": [a["id"] for a in agents],
                "mode": "roundrobin", "max_rounds": 2, "pace_seconds": 0})
            offen = r.json()
            for _ in range(200):
                await asyncio.sleep(0.2)
                zustand = (await c.get(f"/api/threads/{offen['id']}", headers=HEAD)).json()
                if zustand["status"] in ("done", "error"):
                    break
            assert zustand["status"] == "done", zustand

            r = await c.post(f"/api/threads/{offen['id']}/participants", headers=HEAD,
                             json={"agent_id": betreiber["id"]})
            assert r.status_code == 201, r.text
            nachher = r.json()
            assert len(nachher["participants"]) == 3, nachher["participants"]
            assert nachher["status"] == "active", nachher
            assert nachher["max_rounds"] > 2, "Runden muessen nachgelegt werden"

            r = await c.post(f"/api/threads/{offen['id']}/participants", headers=HEAD,
                             json={"agent_id": betreiber["id"]})
            assert r.status_code == 409, "doppelt dazuholen"

            beitraege = (await c.get(f"/api/threads/{offen['id']}/posts", headers=HEAD)).json()
            hinweise = [b for b in beitraege if b["author_type"] == "system"]
            assert hinweise and "Betreiber" in hinweise[-1]["content"], hinweise
            print("[ok] Agent nachtraeglich dazugeholt, Thread laeuft weiter")

            r = await c.delete(f"/api/threads/{offen['id']}/participants/{betreiber['id']}",
                               headers=HEAD)
            assert r.status_code == 204, r.text
            danach = (await c.get(f"/api/threads/{offen['id']}", headers=HEAD)).json()
            assert len(danach["participants"]) == 2, danach["participants"]
            print("[ok] Und wieder herausgenommen")

            # Schriftstuecke beisteuern: Text landet im Verlauf, Datei nicht
            import io as _io
            import zipfile as _zip

            puffer = _io.BytesIO()
            absaetze = ["Messreihe B ergab 4,2 Prozent.", "Die Abweichung ist erklaerbar."]
            xml = (
                '<?xml version="1.0"?><w:document xmlns:w="http://x"><w:body>'
                + "".join(f"<w:p><w:r><w:t>{a}</w:t></w:r></w:p>" for a in absaetze)
                + "</w:body></w:document>"
            )
            with _zip.ZipFile(puffer, "w") as z:
                z.writestr("word/document.xml", xml)

            r = await c.post(
                f"/api/threads/{offen['id']}/documents",
                headers=HEAD,
                files={"datei": ("bericht.docx", puffer.getvalue(), "application/octet-stream")},
            )
            assert r.status_code == 201, r.text
            dokument = r.json()
            assert dokument["author_type"] == "document", dokument
            assert dokument["author_name"] == "bericht.docx"
            assert "Messreihe B ergab 4,2 Prozent." in dokument["content"], dokument["content"]
            print("[ok] Dokument ausgelesen und in den Verlauf gelegt")

            # PDF: der einzige Weg, der von einer Fremdbibliothek abhaengt -
            # und der, ueber den Fremde Dateien hereingeben.
            r = await c.post(
                f"/api/threads/{offen['id']}/documents",
                headers=HEAD,
                files={"datei": ("studie.pdf", pdf_mit_text("Messwert 42 bestaetigt"),
                                 "application/pdf")},
            )
            assert r.status_code == 201, r.text
            pdf_beitrag = r.json()
            assert "Messwert 42 bestaetigt" in pdf_beitrag["content"], pdf_beitrag["content"]
            await c.delete(
                f"/api/threads/{offen['id']}/documents/{pdf_beitrag['id']}", headers=HEAD
            )
            print("[ok] PDF ausgelesen, Text kommt im Verlauf an")

            # Quelltext: wird gelesen wie Text, aber als Code ausgezeichnet -
            # sonst behandeln ihn die Agenten wie Prosa.
            r = await c.post(
                f"/api/threads/{offen['id']}/documents",
                headers=HEAD,
                files={"datei": ("kreis.py", (
                    "def flaeche(r):\n    return 3.14159 * r ** 2\n"
                ).encode(), "text/x-python")},
            )
            assert r.status_code == 201, r.text
            quelle = r.json()
            assert quelle["content"].startswith("```python"), quelle["content"]
            assert "return 3.14159" in quelle["content"], quelle["content"]
            await c.delete(
                f"/api/threads/{offen['id']}/documents/{quelle['id']}", headers=HEAD
            )
            print("[ok] Quelldatei kommt als Code-Block in die Diskussion")

            r = await c.post(
                f"/api/threads/{offen['id']}/documents",
                headers=HEAD,
                files={"datei": ("bild.png", bytes([137, 80, 78, 71]) + bytes(200), "image/png")},
            )
            assert r.status_code == 400, "Bilder muessen abgelehnt werden"
            r = await c.post(
                f"/api/threads/{offen['id']}/documents",
                headers=HEAD,
                files={"datei": ("leer.txt", b"", "text/plain")},
            )
            assert r.status_code == 400, "leere Dateien muessen abgelehnt werden"
            print("[ok] Unbrauchbare Dateien werden abgewiesen")

            r = await c.delete(
                f"/api/threads/{offen['id']}/documents/{dokument['id']}", headers=HEAD
            )
            assert r.status_code == 204, r.text
            uebrig = (await c.get(f"/api/threads/{offen['id']}/posts", headers=HEAD)).json()
            assert not [b for b in uebrig if b["author_type"] == "document"], uebrig
            print("[ok] Dokument wieder aus der Diskussion entfernt")

            # --- Zugangsantraege ---------------------------------------
            r = await c.post("/api/requests", json={"name": "neuling", "note": "moechte mitlesen"})
            assert r.status_code == 201, r.text
            antrag = r.json()
            assert antrag["status"] == "offen", antrag

            r = await c.post("/api/requests", json={"name": "neuling", "note": "nochmal"})
            assert r.status_code == 409, "zweiter Antrag fuer denselben Namen"
            r = await c.post("/api/requests", json={"name": "admin", "note": "gibts schon"})
            assert r.status_code == 409, "Antrag fuer vorhandenen Namen"

            r = await c.get("/api/requests", headers={"Authorization": f"Bearer {other}"})
            assert r.status_code == 403, "Antraege sind nur fuer Admins sichtbar"

            offene = (await c.get("/api/requests", headers=HEAD)).json()
            assert any(a["id"] == antrag["id"] for a in offene), offene

            r = await c.post(f"/api/requests/{antrag['id']}/approve", headers=HEAD)
            assert r.status_code == 201 or r.status_code == 200, r.text
            freigabe = r.json()
            assert freigabe["name"] == "neuling", freigabe
            r = await c.post(f"/api/requests/{antrag['id']}/approve", headers=HEAD)
            assert r.status_code == 409, "zweimal freigeben"

            r = await anmelden(freigabe["token"], "Neuling-PIN-7")
            assert r.status_code == 200 and r.json()["new_token"], r.text
            neulings_token = r.json()["new_token"]
            print("[ok] Antrag gestellt, freigegeben, Einmal-Token eingeloest")

            # Admin setzt zurueck: das bisherige Token gilt dann nicht mehr
            neuling_id = freigabe["id"]
            r = await c.post(
                f"/api/users/{neuling_id}/reset", headers=HEAD, json={}
            )
            assert r.status_code == 200, r.text
            neues_einmal = r.json()["token"]
            r = await c.get("/api/me", headers={"Authorization": f"Bearer {neulings_token}"})
            assert r.status_code == 401, "altes Token muss nach dem Zuruecksetzen tot sein"

            # Das ist der Kern: ein Admin hat ein frisches Einmal-Token, aber
            # ohne die PIN der Person kommt er damit nicht hinein.
            r = await anmelden(neues_einmal, "geraten-123")
            assert r.status_code == 401, "Uebernahme ohne PIN muss scheitern"
            r = await anmelden(neues_einmal, "Neuling-PIN-7")
            assert r.status_code == 200 and r.json()["new_token"], r.text
            print("[ok] Neues Einmal-Token allein reicht fuer keine Uebernahme")

            # Wer auch die PIN vergisst: Notausgang, aber ohne Erbschaft.
            r = await c.post("/api/users", headers=HEAD, json={"name": "vergesslich"})
            vergesslich = r.json()
            r = await anmelden(vergesslich["token"], "Erste-PIN-99")
            v_token = r.json()["new_token"]
            v_kopf = {"Authorization": f"Bearer {v_token}"}
            r = await c.post("/api/credentials", headers=v_kopf, json={
                "label": "Eigener Zugang", "provider": "openai", "api_key": "privat"})
            assert r.status_code == 201, r.text
            assert len((await c.get("/api/credentials", headers=v_kopf)).json()) == 1

            r = await c.post(
                f"/api/users/{vergesslich['id']}/reset",
                headers=HEAD,
                json={"pin_zuruecksetzen": True},
            )
            assert r.status_code == 200, r.text
            r = await anmelden(r.json()["token"], "Neue-PIN-2026")
            assert r.status_code == 200, r.text
            neu_kopf = {"Authorization": f"Bearer {r.json()['new_token']}"}
            assert (await c.get("/api/credentials", headers=neu_kopf)).json() == [], (
                "Beim PIN-Zuruecksetzen muessen die Modell-Zugaenge verschwinden"
            )
            print("[ok] PIN-Notausgang loescht die Modell-Zugaenge mit")

            # Sperre nach zu vielen Fehlversuchen
            r = await c.post("/api/users", headers=HEAD, json={"name": "geduldig"})
            g_einmal = r.json()["token"]
            r = await anmelden(g_einmal, "Geduld-PIN-1")
            g_token = r.json()["new_token"]
            for _ in range(5):
                r = await anmelden(g_token, "falsch-falsch")
                assert r.status_code == 401, r.text
            r = await anmelden(g_token, "Geduld-PIN-1")
            assert r.status_code == 429, "nach 5 Fehlversuchen muss gesperrt sein"
            print("[ok] Nach fuenf falschen PINs wird gesperrt")

            print("[ok] Auth greift (401 ohne Token, 403 fuer Nicht-Admin)")

            # Fehlermeldungen in der Sprache der Oberflaeche
            r = await c.get("/api/me", headers={"Authorization": "Bearer falsch"})
            assert r.json()["detail"] == "Unbekanntes Token", r.text
            r = await c.get(
                "/api/me",
                headers={"Authorization": "Bearer falsch", "X-Agora-Sprache": "en"},
            )
            assert r.json()["detail"] == "Unknown token", r.text
            # Zusammengesetzte Meldung ueber ein Muster
            r = await c.post(
                "/api/agents",
                headers={"Authorization": f"Bearer {other}", "X-Agora-Sprache": "en"},
                json={"name": "X", "model": "openai/x", "credential_id": cred["id"]},
            )
            assert r.json()["detail"] == "That credential is not yours", r.text
            print("[ok] Fehlermeldungen folgen der Sprache der Oberflaeche")

            # Ausweg fuer Umgebungen, in denen eine vorgelagerte Middleware
            # den Authorization-Header fuer sich beansprucht.
            r = await c.get("/api/me", headers={"X-Agora-Token": "smoke-admin-token"})
            assert r.status_code == 200 and r.json()["name"] == "admin", r.text
            r = await c.get("/api/me", headers={"X-Agora-Token": "falsch"})
            assert r.status_code == 401
            print("[ok] X-Agora-Token funktioniert als Ersatz fuer Bearer")

            # --- Sperre: ohne Freischaltung laeuft nichts ----------------
            r = await c.post("/api/threads", headers=HEAD, json={
                "title": "Laeuft nur freigeschaltet",
                "goal": "Sperre pruefen.",
                "agent_ids": [a["id"] for a in agents],
                "mode": "roundrobin", "max_rounds": 4, "pace_seconds": 0,
                "start_now": False})
            gesperrt = r.json()

            r = await c.post("/api/me/lock", headers=HEAD)
            assert r.status_code == 200 and r.json()["unlocked_until"] is None, r.text

            r = await c.post(f"/api/threads/{gesperrt['id']}/control", headers=HEAD,
                             json={"action": "resume"})
            assert r.status_code == 200, r.text
            for _ in range(200):
                await asyncio.sleep(0.2)
                zustand = (await c.get(f"/api/threads/{gesperrt['id']}", headers=HEAD)).json()
                if zustand["status"] != "active":
                    break
            assert zustand["status"] == "paused", zustand
            assert zustand["status_detail"].startswith("Zugang gesperrt"), zustand
            assert zustand["rounds_done"] == 0, "gesperrt darf kein Beitrag entstehen"
            print("[ok] Gesperrte Zugaenge: der Thread pausiert statt zu laufen")

            # Wieder anmelden schaltet frei und weckt den Thread
            r = await anmelden("smoke-admin-token", "Admin-PIN-2026")
            assert r.status_code == 200 and r.json()["unlocked_until"], r.text
            for _ in range(200):
                await asyncio.sleep(0.2)
                zustand = (await c.get(f"/api/threads/{gesperrt['id']}", headers=HEAD)).json()
                if zustand["rounds_done"] > 0:
                    break
            assert zustand["rounds_done"] > 0, zustand
            print("[ok] Freischalten weckt die gesperrten Threads wieder auf")

            # --- Teilnahmefenster ----------------------------------------
            r = await c.patch("/api/me", headers=HEAD, json={"teilnahme_stunden": 1})
            assert r.status_code == 200 and r.json()["teilnahme_stunden"] == 1, r.text

            r = await c.post("/api/threads", headers=HEAD, json={
                "title": "Fenster laeuft ab",
                "goal": "Teilnahmefenster pruefen.",
                "agent_ids": [a["id"] for a in agents],
                "mode": "roundrobin", "max_rounds": 4, "pace_seconds": 0,
                "start_now": False})
            fenster = r.json()

            # Beitritt zwei Stunden zurueckdatieren, als waere das Fenster um
            from datetime import timedelta as _td

            from app.db import SessionLocal as _Sitzung
            from app.models import Participant as _Teil
            from app.models import utcnow as _jetzt
            from sqlalchemy import update as _update

            async with _Sitzung() as sitzung:
                await sitzung.execute(
                    _update(_Teil)
                    .where(_Teil.thread_id == fenster["id"])
                    .values(created_at=_jetzt() - _td(hours=2))
                )
                await sitzung.commit()

            r = await c.post(f"/api/threads/{fenster['id']}/control", headers=HEAD,
                             json={"action": "resume"})
            assert r.status_code == 200, r.text
            for _ in range(200):
                await asyncio.sleep(0.2)
                zustand = (await c.get(f"/api/threads/{fenster['id']}", headers=HEAD)).json()
                if zustand["status"] != "active":
                    break
            assert zustand["status"] == "paused", zustand
            assert "Teilnahmefenster" in zustand["status_detail"], zustand
            assert zustand["rounds_done"] == 0, zustand
            print("[ok] Abgelaufenes Teilnahmefenster haelt fremde Threads an")

            # Ein eigener Beitrag setzt das Fenster zurueck
            r = await c.post(f"/api/threads/{fenster['id']}/posts", headers=HEAD,
                             json={"content": "Ich bin wieder da, macht weiter.", "resume": True})
            assert r.status_code == 201, r.text
            for _ in range(200):
                await asyncio.sleep(0.2)
                zustand = (await c.get(f"/api/threads/{fenster['id']}", headers=HEAD)).json()
                if zustand["rounds_done"] > 0:
                    break
            assert zustand["rounds_done"] > 0, zustand
            print("[ok] Ein eigener Beitrag oeffnet das Fenster wieder")

            r = await c.patch("/api/me", headers=HEAD, json={"teilnahme_stunden": 0})
            assert r.status_code == 200, r.text

            # --- Rechnen ---------------------------------------------------
            RECHNUNGEN = {"n": 0, "erledigt": set()}

            async def rechnet(agent, messages, dk=None):
                """Rechnet genau einmal je Thema.

                Am Thema festgemacht, nicht an einem Zaehler: Threads von
                frueher koennen noch laufen und wuerden den Block sonst
                abgreifen, bevor das Thema unter Test an der Reihe ist.
                """
                RECHNUNGEN["n"] += 1
                text = f"Beitrag {RECHNUNGEN['n']} von {agent.name}."
                zusammen = " ".join(str(m.get("content") or "") for m in messages)
                for thema in ("Nachrechnen", "Ohne Rechnen"):
                    if (
                        f"THEMA: {thema}" in zusammen
                        and thema not in RECHNUNGEN["erledigt"]
                    ):
                        RECHNUNGEN["erledigt"].add(thema)
                        text = (
                            "Das pruefe ich nach.\n\n```rechnen\n"
                            "print(sum(range(1, 101)))\n```"
                        )
                yield "delta", text, None
                yield "usage", "", None

            llm.stream = rechnet
            r = await c.post("/api/threads", headers=HEAD, json={
                "title": "Nachrechnen",
                "goal": "Werkzeug pruefen.",
                "agent_ids": [a["id"] for a in agents],
                "mode": "roundrobin", "max_rounds": 4, "pace_seconds": 0,
                "stop_phrase": "", "werkzeuge": True})
            assert r.status_code == 201, r.text
            rechnend = r.json()
            assert rechnend["werkzeuge"] is True, rechnend

            for _ in range(300):
                await asyncio.sleep(0.2)
                zustand = (await c.get(f"/api/threads/{rechnend['id']}", headers=HEAD)).json()
                if zustand["status"] in ("done", "error"):
                    break
            assert zustand["status"] == "done", zustand

            beitraege = (await c.get(f"/api/threads/{rechnend['id']}/posts", headers=HEAD)).json()
            rechner = [b for b in beitraege if b["author_type"] == "tool"]
            assert len(rechner) == 1, [
                (b["author_type"], b["author_name"], (b["content"] or "")[:90])
                for b in beitraege
            ]
            inhalt = rechner[0]["content"]
            assert inhalt.startswith("```"), inhalt
            assert inhalt.strip().strip("`").replace("text", "", 1).strip() == "5050", inhalt
            assert rechner[0]["author_name"] == "Rechner"

            # Nach der Rechnung ist derselbe Agent wieder dran, um sie einzuordnen.
            reihenfolge = [b["author_name"] for b in beitraege]
            stelle = reihenfolge.index("Rechner")
            assert reihenfolge[stelle - 1] == reihenfolge[stelle + 1], reihenfolge
            llm.stream = fake_stream
            print("[ok] Agent rechnet, Ergebnis landet im Verlauf, er ordnet es ein")

            # Ohne Erlaubnis im Thema wird nichts ausgefuehrt
            llm.stream = rechnet
            RECHNUNGEN["n"] = 0
            r = await c.post("/api/threads", headers=HEAD, json={
                "title": "Ohne Rechnen",
                "goal": "Werkzeug bleibt aus.",
                "agent_ids": [a["id"] for a in agents],
                "mode": "roundrobin", "max_rounds": 2, "pace_seconds": 0,
                "stop_phrase": ""})
            ohne = r.json()
            assert ohne["werkzeuge"] is False, ohne
            for _ in range(300):
                await asyncio.sleep(0.2)
                zustand = (await c.get(f"/api/threads/{ohne['id']}", headers=HEAD)).json()
                if zustand["status"] in ("done", "error"):
                    break
            beitraege = (await c.get(f"/api/threads/{ohne['id']}/posts", headers=HEAD)).json()
            assert not [b for b in beitraege if b["author_type"] == "tool"], beitraege
            llm.stream = fake_stream
            print("[ok] Ohne Erlaubnis im Thema wird nichts ausgefuehrt")

            # --- Beweis pruefen --------------------------------------------
            # Der eigentliche Zweck der Werkzeuge: ein hochgeladener Beweis
            # wird nicht referiert, sondern nachgerechnet - und faellt an
            # einem Schritt, den blosses Lesen durchgehen liesse.
            BEWEIS_TEX = (
                "\\documentclass{article}\n"
                "\\begin{satz}\n"
                "  Es gilt $\\sum_{n=1}^{\\infty} \\frac{1}{n^2} = \\frac{\\pi^2}{8}$.\n"
                "\\end{satz}\n"
                "\\begin{proof} Nach Euler ist die Summe der reziproken Quadrate $\\pi^2/8$. \\end{proof}\n"
            )

            PRUEFT = {"n": 0, "erledigt": False}

            async def prueft(agent, messages, dk=None):
                PRUEFT["n"] += 1
                zusammen = " ".join(str(m.get("content") or "") for m in messages)
                # Beides muss beim Agenten ankommen, sonst prueft er nichts.
                assert "pi^2}{8}" in zusammen, "Beweis fehlt im Prompt"
                assert "Es liegt Material bei" in zusammen, \
                    "Hinweis auf das Dokument fehlt im Prompt"
                assert "geh die Schritte dann EINZELN durch" in zusammen, \
                    "Pruefauftrag der Art 'pruefen' fehlt im Prompt"
                assert "Stimme nicht zu, solange ein Punkt ungeprueft ist" in zusammen, \
                    "Zustimmungsbremse fehlt im Prompt"
                if not PRUEFT["erledigt"]:
                    PRUEFT["erledigt"] = True
                    text = (
                        "Das rechne ich nach, bevor ich es glaube.\n\n```rechnen\n"
                        "import sympy as sp\n"
                        "n = sp.symbols('n', positive=True, integer=True)\n"
                        "wert = sp.summation(1/n**2, (n, 1, sp.oo))\n"
                        "print('Reihe =', wert, '| Behauptung haelt:', "
                        "sp.simplify(wert - sp.pi**2/8) == 0)\n"
                        "```"
                    )
                else:
                    text = f"Beitrag {PRUEFT['n']} von {agent.name}."
                yield "delta", text, None
                yield "usage", "", None

            # start_now=False: das Dokument muss liegen, bevor der erste Zug faellt.
            r = await c.post("/api/threads", headers=HEAD, json={
                "title": "Reihe pruefen",
                "goal": "Stimmt der behauptete Wert der Reihe?",
                "agent_ids": [a["id"] for a in agents],
                "mode": "roundrobin", "max_rounds": 3, "pace_seconds": 0,
                "stop_phrase": "", "werkzeuge": True, "start_now": False,
                "art": "pruefen"})
            assert r.status_code == 201, r.text
            beweis = r.json()
            assert beweis["art"] == "pruefen", beweis

            r = await c.post(
                f"/api/threads/{beweis['id']}/documents",
                headers=HEAD,
                files={"datei": ("beweis.tex", BEWEIS_TEX.encode(), "text/x-tex")},
            )
            assert r.status_code == 201, r.text
            # LaTeX ist Prosa mit Mathematik darin, kein Quelltext: als
            # Code-Block ausgezeichnet besprechen die Agenten das Markup.
            assert not r.json()["content"].startswith("```"), r.json()["content"][:80]

            llm.stream = prueft
            r = await c.post(f"/api/threads/{beweis['id']}/control",
                             headers=HEAD, json={"action": "resume"})
            assert r.status_code == 200, r.text

            for _ in range(300):
                await asyncio.sleep(0.2)
                zustand = (await c.get(f"/api/threads/{beweis['id']}", headers=HEAD)).json()
                if zustand["status"] in ("done", "error"):
                    break
            assert zustand["status"] == "done", zustand

            beitraege = (await c.get(f"/api/threads/{beweis['id']}/posts", headers=HEAD)).json()
            rechnung = [b for b in beitraege if b["author_type"] == "tool"]
            assert len(rechnung) == 1, [b["author_type"] for b in beitraege]
            # pi**2/6, nicht pi**2/8 - die Behauptung faellt.
            assert "pi**2/6" in rechnung[0]["content"], rechnung[0]["content"]
            assert "haelt: False" in rechnung[0]["content"], rechnung[0]["content"]
            llm.stream = fake_stream
            print("[ok] Hochgeladener Beweis wird nachgerechnet und faellt")

            # --- Art der Diskussion ----------------------------------------
            # Ein Antrag will anders gelesen werden als ein Beweis. Geprueft
            # wird, dass die Wahl wirklich im Prompt ankommt - und dass die
            # freie Diskussion ohne Pruefauftrag bleibt.
            from app import pruefarten

            GESEHEN = {}

            def merker(schluessel):
                async def fake(agent, messages, dk=None):
                    GESEHEN[schluessel] = " ".join(
                        str(m.get("content") or "") for m in messages
                    )
                    yield "delta", f"Beitrag von {agent.name}.", None
                    yield "usage", "", None
                return fake

            for kunst, kennsatz in (
                ("antrag", "Lies wie ein Gutachter des Geldgebers"),
                ("begutachten", "Begutachte wie fuer eine Fachzeitschrift"),
                ("text", "Pruefe den Text, statt ihn nachzuerzaehlen"),
                ("code", "Lies den Code wie in einem Review"),
                ("frei", None),
            ):
                llm.stream = merker(kunst)
                r = await c.post("/api/threads", headers=HEAD, json={
                    "title": f"Art {kunst}",
                    "goal": "Die Art soll im Prompt ankommen.",
                    "agent_ids": [a["id"] for a in agents],
                    "mode": "roundrobin", "max_rounds": 1, "pace_seconds": 0,
                    "stop_phrase": "", "art": kunst})
                assert r.status_code == 201, r.text
                gewaehlt = r.json()
                assert gewaehlt["art"] == kunst, gewaehlt
                for _ in range(300):
                    await asyncio.sleep(0.2)
                    zustand = (await c.get(f"/api/threads/{gewaehlt['id']}",
                                           headers=HEAD)).json()
                    if zustand["status"] in ("done", "error"):
                        break
                gesehen = GESEHEN.get(kunst, "")
                if kennsatz:
                    assert kennsatz in gesehen, f"{kunst}: Auftrag fehlt im Prompt"
                else:
                    # Freie Diskussion: kein Pruefauftrag, keine Bremse.
                    assert "Stimme nicht zu, solange" not in gesehen, gesehen[:400]
            llm.stream = fake_stream

            # Erfundene Arten werden abgewiesen, nicht stillschweigend geschluckt.
            r = await c.post("/api/threads", headers=HEAD, json={
                "title": "Unbekannte Art",
                "goal": "Soll scheitern.",
                "agent_ids": [a["id"] for a in agents],
                "mode": "roundrobin", "max_rounds": 1, "pace_seconds": 0,
                "art": "gibtesnicht"})
            assert r.status_code == 400, r.text

            # Jede Art im Code hat eine Beschriftung in beiden Sprachen.
            i18n = (ROOT / "app" / "static" / "i18n.js").read_text(encoding="utf-8")
            for kunst in pruefarten.ARTEN:
                schluessel = f'"neuesThema.art{kunst[:1].upper()}{kunst[1:]}"'
                assert i18n.count(schluessel) == 2, \
                    f"{schluessel} fehlt in einer der beiden Sprachen"
            print("[ok] Art der Diskussion steuert den Auftrag der Agenten")

            # --- Grundlage beim Anlegen ------------------------------------
            # Genau die Reihenfolge, die die Oberflaeche faehrt, wenn beim
            # Anlegen Dateien mitgegeben werden: angehalten anlegen, alles
            # hochladen, dann starten. Sonst redet die erste Runde ins Leere.
            ERSTER = {}

            async def merkt_ersten(agent, messages, dk=None):
                ERSTER.setdefault(
                    "prompt", " ".join(str(m.get("content") or "") for m in messages)
                )
                yield "delta", f"Beitrag von {agent.name}.", None
                yield "usage", "", None

            llm.stream = merkt_ersten
            r = await c.post("/api/threads", headers=HEAD, json={
                "title": "Mit Grundlage",
                "goal": "Beide Dokumente sollen von Anfang an dastehen.",
                "agent_ids": [a["id"] for a in agents],
                "mode": "roundrobin", "max_rounds": 1, "pace_seconds": 0,
                "stop_phrase": "", "art": "begutachten", "start_now": False})
            assert r.status_code == 201, r.text
            mit = r.json()
            assert mit["status"] == "paused", mit

            for name, inhalt in (
                ("messreihe.csv", b"probe;wert\nA;4.2\nB;7.9\n"),
                ("notiz.md", "# Vorbemerkung\n\nProbe B war kontaminiert.".encode()),
            ):
                r = await c.post(
                    f"/api/threads/{mit['id']}/documents",
                    headers=HEAD,
                    files={"datei": (name, inhalt, "application/octet-stream")},
                )
                assert r.status_code == 201, r.text

            # Vor dem Start steht nichts als die Grundlage im Verlauf.
            vorher = (await c.get(f"/api/threads/{mit['id']}/posts", headers=HEAD)).json()
            assert [b["author_type"] for b in vorher] == ["document", "document"], vorher

            r = await c.post(f"/api/threads/{mit['id']}/control",
                             headers=HEAD, json={"action": "resume"})
            assert r.status_code == 200, r.text
            for _ in range(300):
                await asyncio.sleep(0.2)
                zustand = (await c.get(f"/api/threads/{mit['id']}", headers=HEAD)).json()
                if zustand["status"] in ("done", "error"):
                    break
            assert zustand["status"] == "done", zustand

            # Beide Dokumente lagen dem allerersten Beitrag schon vor.
            erster = ERSTER.get("prompt", "")
            assert "Probe B war kontaminiert" in erster, "Notiz fehlte im ersten Prompt"
            assert "probe;wert" in erster, "Messreihe fehlte im ersten Prompt"
            llm.stream = fake_stream
            print("[ok] Grundlage liegt vor dem ersten Beitrag im Thema")

            # --- Eingriff waehrend eines laufenden Zuges -------------------
            # Ein Zug kann Minuten dauern. Drueckt in dieser Zeit jemand
            # "+5 Runden", darf der Abschluss des Zuges das nicht
            # ueberschreiben - sonst passiert scheinbar nichts.
            from sqlalchemy import select as _waehle

            from app.db import SessionLocal as _Sitzung
            from app.models import Thread as _Thema

            TOR = asyncio.Event()
            ZUEGE = {"n": 0}

            async def haengt(agent, messages, dk=None):
                ZUEGE["n"] += 1
                yield "delta", f"Zug {ZUEGE['n']}: ich denke nach ", None
                await TOR.wait()
                # Der naechste Zug soll wieder haengen bleiben, damit der
                # Zustand zwischendurch in Ruhe zu pruefen ist.
                TOR.clear()
                yield "delta", "... fertig.", None
                yield "usage", "", None

            llm.stream = haengt
            # max_rounds=1: ohne Eingriff waere nach diesem einen Zug Schluss.
            r = await c.post("/api/threads", headers=HEAD, json={
                "title": "Eingriff mitten im Zug",
                "goal": "Der Eingriff soll stehen bleiben.",
                "agent_ids": [a["id"] for a in agents],
                "mode": "roundrobin", "max_rounds": 1, "pace_seconds": 0,
                "stop_phrase": ""})
            assert r.status_code == 201, r.text
            eingriff = r.json()

            # Warten, bis der Zug wirklich laeuft (der Worker haelt die Lease).
            for _ in range(300):
                await asyncio.sleep(0.2)
                if ZUEGE["n"] >= 1:
                    break
            assert ZUEGE["n"] >= 1, "Der Zug ist nie angelaufen"

            # Jetzt eingreifen, waehrend der Worker noch beim Modell wartet.
            r = await c.post(f"/api/threads/{eingriff['id']}/control", headers=HEAD,
                             json={"action": "extend", "rounds": 3})
            assert r.status_code == 200, r.text
            assert r.json()["max_rounds"] == 4, r.json()

            # Die Lease bleibt liegen, nur der Anspruch ist fort: sonst
            # griffe ein zweiter Worker zu, waehrend der erste noch laeuft.
            async with _Sitzung() as sitzung:
                gesperrt = (await sitzung.execute(
                    _waehle(_Thema).where(_Thema.id == eingriff["id"]))).scalar_one()
                assert gesperrt.locked_by is None, "Der Anspruch haette fort sein muessen"
                assert gesperrt.locked_until is not None, "Die Lease haette bleiben muessen"

            # Zug zu Ende laufen lassen. Der naechste bleibt am Tor haengen,
            # deshalb ist der Zustand danach in Ruhe zu pruefen.
            TOR.set()
            for _ in range(300):
                await asyncio.sleep(0.2)
                zustand = (await c.get(f"/api/threads/{eingriff['id']}",
                                       headers=HEAD)).json()
                if zustand["rounds_done"] >= 1:
                    break

            assert zustand["rounds_done"] >= 1, zustand
            assert zustand["status"] != "done", (
                f"Der Abschluss hat den Eingriff ueberschrieben: {zustand}")
            assert zustand["max_rounds"] == 4, zustand

            # Aufraeumen: Tor auf und Thema beenden, sonst haengt der Worker.
            TOR.set()
            await c.post(f"/api/threads/{eingriff['id']}/control", headers=HEAD,
                         json={"action": "stop"})
            llm.stream = fake_stream
            print("[ok] Eingriff waehrend des Zuges wird nicht ueberschrieben")

            # --- Uebersetzen auf Knopfdruck --------------------------------
            UEBERSETZT = {"n": 0}

            async def uebersetzer(agent, messages, max_tokens=None, dk=None):
                UEBERSETZT["n"] += 1
                inhalt = messages[0]["content"]
                assert "Uebersetze den folgenden Forumsbeitrag" in inhalt, inhalt[:80]
                return "Translated: " + inhalt.split("--- Beitrag ---")[1].strip()[:40]

            beitraege = (await c.get(f"/api/threads/{thread['id']}/posts", headers=HEAD)).json()
            einer = [b for b in beitraege if b["author_type"] == "agent"][0]

            llm.complete = uebersetzer
            r = await c.post(f"/api/posts/{einer['id']}/translate", headers=HEAD,
                             json={"ziel": "en"})
            assert r.status_code == 200, r.text
            assert r.json()["text"].startswith("Translated: "), r.json()
            assert r.json()["gecacht"] is False, r.json()
            assert UEBERSETZT["n"] == 1

            # Zweiter Aufruf kommt aus dem Zwischenspeicher, kostet nichts
            r = await c.post(f"/api/posts/{einer['id']}/translate", headers=HEAD,
                             json={"ziel": "en"})
            assert r.status_code == 200 and r.json()["gecacht"] is True, r.json()
            assert UEBERSETZT["n"] == 1, "zweimal uebersetzt statt zwischengespeichert"

            r = await c.post(f"/api/posts/{einer['id']}/translate", headers=HEAD,
                             json={"ziel": "fr"})
            assert r.status_code == 400, "nur de und en"

            # Ohne eigenen Agenten mit Zugang geht es nicht - und der Zugang
            # dessen, der liest, wird benutzt, nicht der des Verfassers.
            r = await c.post(f"/api/posts/{einer['id']}/translate",
                             headers={"Authorization": f"Bearer {other}"},
                             json={"ziel": "de"})
            assert r.status_code == 409, r.text
            llm.complete = fake_complete
            print("[ok] Uebersetzen auf Knopfdruck, mit Zwischenspeicher")

            # Cookie-Anmeldung: der Weg, der auch durch vorgelagerte
            # Authentifizierungs-Schichten kommt.
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as browser:
                r = await browser.post("/api/login", json={"token": "falsch"})
                assert r.status_code == 401, r.text
                r = await browser.post(
                    "/api/login", json={"token": "smoke-admin-token", "pin": "falsch-999"}
                )
                assert r.status_code == 401, "ohne richtige PIN kein Cookie"
                r = await browser.post(
                    "/api/login", json={"token": "smoke-admin-token", "pin": "Admin-PIN-2026"}
                )
                assert r.status_code == 200 and r.json()["name"] == "admin", r.text
                assert "agora_token" in browser.cookies, dict(browser.cookies)
                # Ab jetzt ohne jeden Header, nur mit dem Cookie:
                r = await browser.get("/api/me")
                assert r.status_code == 200 and r.json()["name"] == "admin", r.text
                r = await browser.get("/api/threads")
                assert r.status_code == 200, r.text
                await browser.post("/api/logout")
                r = await browser.get("/api/me")
                assert r.status_code == 401
            print("[ok] Cookie-Anmeldung traegt durch alle Aufrufe und laesst sich beenden")

            r = await c.get("/healthz")
            assert r.json().get("version"), r.text
            print("[ok] /healthz meldet Version", r.json()["version"])

        collector.cancel()

    kinds = {}
    for payload in events:
        import json
        kinds[json.loads(payload)["type"]] = kinds.get(json.loads(payload)["type"], 0) + 1
    assert kinds.get("post.delta", 0) > 0, kinds
    assert kinds.get("post.created", 0) > 0 and kinds.get("post.done", 0) > 0
    print("[ok] Event-Bus:", kinds)
    print("\nALLE TESTS BESTANDEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_test()))
