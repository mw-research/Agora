"""Die harte Rundengrenze - und was danach passiert.

Eigene Datei, weil dafuer AGORA_MAX_ROUNDS_HARD niedrig stehen muss. Im
Rauchtest wuerde das alle anderen Themen mit abwuergen.

    python tests/grenze_test.py

Wogegen das schuetzt: bis September 2026 nahm die Steuerung "Weiter" und
"+5 Runden" jenseits der Grenze klaglos an. Der Worker sah die Grenze beim
naechsten Zug wieder, beendete sofort und schrieb noch eine Synthese - man
drueckte also immer wieder und bekam jedes Mal nur den Moderator.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = pathlib.Path(tempfile.gettempdir()) / "agora_grenze.db"
if DB.exists():
    DB.unlink()

from cryptography.fernet import Fernet  # noqa: E402

GRENZE = 2  # statt 200 - der Ablauf ist derselbe

os.environ["AGORA_SECRET_KEY"] = Fernet.generate_key().decode()
os.environ["AGORA_ADMIN_TOKEN"] = "grenz-token"
os.environ["AGORA_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB.as_posix()}"
os.environ["AGORA_WORKER_ENABLED"] = "true"
os.environ["AGORA_WORKER_INTERVAL"] = "0.2"
os.environ["AGORA_MAX_ROUNDS_HARD"] = str(GRENZE)

import httpx  # noqa: E402

from app import llm, main  # noqa: E402

HEAD = {"Authorization": "Bearer grenz-token"}


async def falscher_strom(agent, messages, dk=None):
    yield "delta", f"Beitrag von {agent.name}.", None
    yield "usage", "", None


async def falsche_synthese(agent, messages, max_tokens=None, dk=None):
    return f"Zusammenfassung von {agent.name}"


llm.stream = falscher_strom
llm.complete = falsche_synthese


async def main_test() -> int:
    app = main.app
    transport = httpx.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            await c.post("/api/me/pin", headers=HEAD, json={"neue_pin": "Grenz-PIN-2026"})
            zugang = (await c.post("/api/credentials", headers=HEAD, json={
                "label": "X", "provider": "openai", "api_key": "x"})).json()
            agenten = []
            for name in ("Eins", "Zwei"):
                r = await c.post("/api/agents", headers=HEAD, json={
                    "name": name, "model": "openai/x", "credential_id": zugang["id"]})
                agenten.append(r.json()["id"])

            # Eigenes Budget absichtlich hoch: greifen soll die harte Grenze.
            r = await c.post("/api/threads", headers=HEAD, json={
                "title": "An die Grenze", "goal": "Test", "agent_ids": agenten,
                "mode": "roundrobin", "max_rounds": 50, "pace_seconds": 0,
                "stop_phrase": ""})
            assert r.status_code == 201, r.text
            thema = r.json()["id"]

            async def warten() -> dict:
                for _ in range(300):
                    await asyncio.sleep(0.2)
                    z = (await c.get(f"/api/threads/{thema}", headers=HEAD)).json()
                    if z["status"] in ("done", "error"):
                        return z
                raise AssertionError("Thema kam nicht zur Ruhe")

            async def synthesen() -> int:
                p = (await c.get(f"/api/threads/{thema}/posts", headers=HEAD)).json()
                return sum(1 for b in p if "(Synthese)" in (b["author_name"] or ""))

            zustand = await warten()
            assert zustand["rounds_done"] == GRENZE, zustand
            # Der Grund muss die harte Grenze nennen, nicht das eigene Budget -
            # das eine laesst sich anheben, das andere nicht.
            assert str(GRENZE) in zustand["status_detail"], zustand["status_detail"]
            assert await synthesen() == 1, "genau eine Synthese erwartet"
            print(f"[ok] Harte Grenze greift bei {GRENZE} Runden und sagt das auch")

            # Und jetzt das, was ein Mensch tut.
            for versuch in (1, 2):
                for aktion in ("extend", "resume"):
                    r = await c.post(f"/api/threads/{thema}/control", headers=HEAD,
                                     json={"action": aktion, "rounds": 5})
                    assert r.status_code == 409, f"{aktion} #{versuch}: {r.status_code} {r.text}"
                    assert str(GRENZE) in r.text, r.text
                    assert "AGORA_MAX_ROUNDS_HARD" in r.text, r.text

            # Nichts hat sich bewegt - vor allem keine weitere Synthese.
            danach = (await c.get(f"/api/threads/{thema}", headers=HEAD)).json()
            assert danach["rounds_done"] == GRENZE, danach
            assert await synthesen() == 1, "es kamen weitere Synthesen dazu"
            print("[ok] Weiter und +5 Runden werden abgewiesen, keine zweite Synthese")

            # Beenden muss weiter gehen - sonst haengt das Thema fest.
            r = await c.post(f"/api/threads/{thema}/control", headers=HEAD,
                             json={"action": "stop"})
            assert r.status_code == 200, r.text
            print("[ok] Beenden bleibt moeglich")

    print("\nALLE TESTS BESTANDEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_test()))
