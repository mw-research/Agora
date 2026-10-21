"""Ein Agent, der aussetzt, haelt die Diskussion nicht mehr an.

Der Fehler aus dem Betrieb, woertlich aus dem Themenkopf:

    Modellaufruf von 'Beleglage' fehlgeschlagen: Timeout: litellm.Timeout
    - Connection timed out. Timeout passed=180.0

Eine einzige Zeitueberschreitung gegen das eigene vLLM setzte den ganzen
Thread auf 'error'. Er stand dann still, bis ein Mensch etwas schrieb - bei
einer Diskussion, die noch 150 Runden vor sich hatte.

Dazu der zweite Fall aus demselben Bild: ein Beitrag, der leer bleibt, weil
ein Modell mit Reasoning sein ganzes Token-Budget verdacht hat. Der galt
frueher als gueltiger Zug und verbrauchte eine Runde.
"""
import asyncio
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VERZEICHNIS = tempfile.mkdtemp(prefix="agora-aussetzer-")
os.environ["AGORA_DATABASE_URL"] = f"sqlite+aiosqlite:///{VERZEICHNIS}/a.db"
os.environ["AGORA_SECRET_KEY"] = "xUqK7nHnV1nQ0Yq4Zk8vZ0mVQ1pQ9yYy2Zt8Xy3Kk3A="
os.environ["AGORA_ADMIN_TOKEN"] = "admin-token-fuer-den-aussetzertest"
os.environ["AGORA_WORKER_ENABLED"] = "true"
os.environ["AGORA_WORKER_INTERVAL"] = "0.2"

import httpx  # noqa: E402

from app import llm, main, orchestrator  # noqa: E402

# Was das Modell beim naechsten Aufruf tun soll.
DREHBUCH: list[str] = []
GERUFEN: list[str] = []


async def fake_stream(agent, messages, dk=None):
    GERUFEN.append(agent.name)
    was = DREHBUCH.pop(0) if DREHBUCH else "text"
    if was == "timeout":
        raise llm.LLMError(
            "Timeout: litellm.Timeout: Timeout Error: Hosted_vllmException - "
            "litellm.Timeout: Connection timed out. Timeout passed=180.0, "
            "time taken=180.012 seconds"
        )
        yield  # pragma: no cover - macht die Funktion zum Generator
    if was == "nur_denken":
        # Genau der Qwen3-Fall: viel Denken, nie Inhalt.
        yield "denken", "Also, zuerst muesste ich " * 20, None
        yield "usage", "", None
        return
    yield "delta", f"Beitrag von {agent.name}.", None
    yield "usage", "", None


async def fake_complete(agent, messages, max_tokens=None, dk=None):
    return "egal"


llm.stream = fake_stream
llm.complete = fake_complete


async def main_test() -> int:
    transport = httpx.ASGITransport(app=main.app)
    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            A = {"Authorization": f"Bearer {os.environ['AGORA_ADMIN_TOKEN']}"}

            agenten = []
            for name in ("Beleglage", "Lektorat"):
                r = await c.post("/api/agents", headers=A,
                                 json={"name": name, "model": "hosted_vllm/qwen3.8"})
                assert r.status_code == 201, r.text
                agenten.append(r.json()["id"])

            laufend: list[str] = []

            async def thema_mit(drehbuch, runden=6):
                # Vorheriges anhalten: ein noch laufendes Thema holt sich
                # sonst die Drehbuch-Eintraege des naechsten.
                for alt_id in laufend:
                    await c.post(f"/api/threads/{alt_id}/control", headers=A,
                                 json={"action": "stop"})
                laufend.clear()
                await asyncio.sleep(0.4)
                DREHBUCH.clear()
                DREHBUCH.extend(drehbuch)
                GERUFEN.clear()
                r = await c.post("/api/threads", headers=A, json={
                    "title": "Laeuft es weiter?",
                    "goal": "Ein Aussetzer darf nichts beenden.",
                    "agent_ids": agenten,
                    "mode": "roundrobin", "max_rounds": runden, "pace_seconds": 0})
                assert r.status_code == 201, r.text
                laufend.append(r.json()["id"])
                return r.json()["id"]

            async def warten(thema, bedingung, versuche=200):
                for _ in range(versuche):
                    await asyncio.sleep(0.1)
                    stand = (await c.get(f"/api/threads/{thema}", headers=A)).json()
                    posts = (await c.get(f"/api/threads/{thema}/posts", headers=A)).json()
                    if bedingung(stand, posts):
                        return stand, posts
                return stand, posts

            # --- Ein Timeout, dann geht es weiter -----------------------
            thema = await thema_mit(["timeout", "text", "text", "text"])
            stand, posts = await warten(
                thema,
                lambda s, p: sum(1 for x in p if x["status"] == "complete"
                                 and x["author_type"] == "agent") >= 2,
            )
            fehler = [p for p in posts if p["status"] == "error"]
            gelungen = [p for p in posts
                        if p["status"] == "complete" and p["author_type"] == "agent"]

            assert fehler, "der Aussetzer wurde gar nicht vermerkt"
            assert "Timeout" in fehler[0]["content"], fehler[0]["content"][:120]
            assert len(gelungen) >= 2, f"es ging nicht weiter: {len(gelungen)} Beitraege"
            assert stand["status"] != "error", (
                f"der Thread steht auf error: {stand['status_detail']}"
            )
            print(f"[ok] Timeout vermerkt, Diskussion lief weiter "
                  f"({len(gelungen)} Beitraege nach dem Aussetzer)")

            # Die misslungene Runde darf kein Budget verbrauchen. Erst
            # anhalten, dann zaehlen: Thema und Beitraege kommen aus zwei
            # Abfragen, und dazwischen arbeitet der Worker weiter - sonst
            # vergleicht man zwei verschiedene Zeitpunkte.
            await c.post(f"/api/threads/{thema}/control", headers=A,
                         json={"action": "stop"})
            await asyncio.sleep(0.6)
            stand = (await c.get(f"/api/threads/{thema}", headers=A)).json()
            posts = (await c.get(f"/api/threads/{thema}/posts", headers=A)).json()
            gelungen = [p for p in posts
                        if p["status"] == "complete" and p["author_type"] == "agent"]
            assert stand["rounds_done"] == len(gelungen), (
                f"{stand['rounds_done']} Runden bei {len(gelungen)} Beitraegen - "
                "ein Fehlschlag hat mitgezaehlt"
            )
            print(f"[ok] Der misslungene Zug hat keine Runde verbraucht "
                  f"({stand['rounds_done']} Runden, {len(gelungen)} Beitraege)")

            # --- Nur Denken, kein Inhalt --------------------------------
            thema = await thema_mit(["nur_denken", "text", "text"])
            stand, posts = await warten(
                thema,
                lambda s, p: any(x["status"] == "error" for x in p)
                and any(x["status"] == "complete" and x["author_type"] == "agent"
                        for x in p),
            )
            leer = next((p for p in posts if p["status"] == "error"), None)
            assert leer is not None, [p["status"] for p in posts]
            assert "gedacht" in leer["content"], leer["content"][:200]
            assert "enable_thinking" in leer["content"], leer["content"][:300]
            assert stand["status"] != "error", stand["status_detail"]
            print("[ok] Nur-Denken wird erklaert, samt Hinweis auf enable_thinking")

            # --- Dauerhaft kaputt: dann aber anhalten -------------------
            thema = await thema_mit(["timeout"] * 8, runden=12)
            stand, _ = await warten(thema, lambda s, p: s["status"] != "active")
            assert stand["status"] == "paused", stand
            assert "hintereinander misslungen" in stand["status_detail"], stand
            print(f"[ok] Nach mehreren Aussetzern haelt es an: "
                  f"\"{stand['status_detail'][:60]}…\"")

            # --- Haengender Beitrag wird aufgeraeumt --------------------
            # So sieht es aus, wenn der Worker mitten im Zug verschwindet.
            from sqlalchemy import select

            from app.db import SessionLocal
            from app.models import Post, Thread

            async with SessionLocal() as sitzung:
                thread = (await sitzung.execute(
                    select(Thread).where(Thread.id == thema)
                )).scalar_one()
                sitzung.add(Post(
                    thread_id=thema, author_type="agent", author_name="Verschwunden",
                    agent_id=agenten[0], content="", status="streaming", round_no=0,
                ))
                thread.status = "active"
                thread.locked_until = None
                thread.locked_by = None
                thread.next_run_at = orchestrator.utcnow()
                await sitzung.commit()

            DREHBUCH.clear()
            DREHBUCH.extend(["text"])
            stand, posts = await warten(
                thema,
                lambda s, p: all(x["status"] != "streaming" for x in p),
            )
            haengend = [p for p in posts if p["status"] == "streaming"]
            assert not haengend, f"{len(haengend)} Beitraege haengen noch"
            aufgeraeumt = next(
                (p for p in posts if p["author_name"] == "Verschwunden"), None)
            assert aufgeraeumt is not None, [p["author_name"] for p in posts]
            assert "unterbrochen" in aufgeraeumt["content"], aufgeraeumt["content"]
            print("[ok] Ein haengender Beitrag wird beim Uebernehmen aufgeraeumt")

    print("\nALLE TESTS BESTANDEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_test()))
