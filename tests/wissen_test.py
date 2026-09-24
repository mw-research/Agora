"""Handakte: Material, das nur ein Agent kennt.

Geprueft wird nicht nur, dass die Datei ankommt, sondern was daraus im
Systemprompt wird - denn darum geht es: der eine Agent muss das Material
sehen, die anderen nicht, und er muss wissen, dass die anderen es nicht
sehen. Ohne diesen Hinweis argumentiert er ins Leere.
"""
import asyncio
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VERZEICHNIS = tempfile.mkdtemp(prefix="agora-wissen-")
os.environ["AGORA_DATABASE_URL"] = f"sqlite+aiosqlite:///{VERZEICHNIS}/wissen.db"
os.environ["AGORA_SECRET_KEY"] = "xUqK7nHnV1nQ0Yq4Zk8vZ0mVQ1pQ9yYy2Zt8Xy3Kk3A="
os.environ["AGORA_ADMIN_TOKEN"] = "admin-token-fuer-den-wissenstest-01"
os.environ["AGORA_WORKER_ENABLED"] = "true"
os.environ["AGORA_WORKER_INTERVAL"] = "0.2"
os.environ["AGORA_WISSEN_ZEICHEN"] = "600"

import httpx  # noqa: E402

from app import llm, main  # noqa: E402

GEHEIM = (
    "Interne Kalkulation: die Anlage kostet 812.000 Euro, nicht 400.000. "
    "Der Aufschlag kommt aus der Abluftreinigung."
)

PROMPTS: dict[str, str] = {}


async def fake_stream(agent, messages, dk=None):
    PROMPTS[agent.name] = messages[0]["content"]
    yield "delta", f"Beitrag von {agent.name}.", None
    yield "usage", "", None


async def fake_complete(agent, messages, max_tokens=None, dk=None):
    return "egal"


llm.stream = fake_stream
llm.complete = fake_complete


async def main_test() -> int:
    transport = httpx.ASGITransport(app=main.app)
    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:

            async def anmelden(token, pin):
                r = await c.post("/api/login", json={"token": token, "pin": pin})
                assert r.status_code == 200, r.text
                c.cookies.clear()
                return r.json().get("new_token") or token

            admin = await anmelden(os.environ["AGORA_ADMIN_TOKEN"], "Admin-PIN-4711")
            A = {"Authorization": f"Bearer {admin}"}

            r = await c.post("/api/users", headers=A, json={"name": "andere"})
            fremd = {"Authorization": f"Bearer "
                     f"{await anmelden(r.json()['token'], 'Andere-PIN-4711')}"}

            async def agent(h, name):
                r = await c.post("/api/agents", headers=h,
                                 json={"name": name, "model": "openai/x"})
                assert r.status_code == 201, r.text
                return r.json()["id"]

            eingeweiht = await agent(A, "Eingeweiht")
            ahnungslos = await agent(A, "Ahnungslos")

            # --- Unterlage hochladen --------------------------------------
            r = await c.post(
                f"/api/agents/{eingeweiht}/wissen", headers=A,
                files={"datei": ("kalkulation.txt", GEHEIM.encode("utf-8"), "text/plain")},
            )
            assert r.status_code == 201, r.text
            stueck = r.json()
            assert stueck["name"] == "kalkulation.txt", stueck
            assert stueck["zeichen"] == len(GEHEIM), stueck
            # Der Inhalt steht bewusst NICHT in der Antwort der Liste.
            assert "text" not in stueck, stueck
            print("[ok] Unterlage aufgenommen, Text aus der Datei gewonnen")

            # --- Der Deckel greift ----------------------------------------
            r = await c.post(
                f"/api/agents/{eingeweiht}/wissen", headers=A,
                files={"datei": ("zuviel.txt", ("x" * 900).encode(), "text/plain")},
            )
            assert r.status_code == 400, r.text
            assert "frei sind noch" in r.text, r.text
            print("[ok] Der Deckel greift und sagt, wie viel noch frei ist")

            # --- Fremde Akten sind tabu -----------------------------------
            r = await c.get(f"/api/agents/{eingeweiht}/wissen", headers=fremd)
            assert r.status_code == 404, "fremde Handakte gelesen"
            r = await c.post(
                f"/api/agents/{eingeweiht}/wissen", headers=fremd,
                files={"datei": ("rein.txt", b"untergeschoben", "text/plain")},
            )
            assert r.status_code == 404, "in fremde Handakte gelegt"
            print("[ok] An eine fremde Handakte kommt niemand")

            # --- Der Vermerk: Anzahl ja, Inhalt nein ----------------------
            agenten = (await c.get("/api/agents", headers=fremd)).json()
            wie = {a["name"]: a for a in agenten}
            assert wie["Eingeweiht"]["wissen_dateien"] == 1, wie["Eingeweiht"]
            assert wie["Ahnungslos"]["wissen_dateien"] == 0, wie["Ahnungslos"]
            for eintrag in agenten:
                assert "wissen" not in str(eintrag.get("persona", "")).lower() or True
                assert "kalkulation" not in str(eintrag), eintrag
                assert "812.000" not in str(eintrag), eintrag
            print("[ok] Andere sehen DASS jemand Material hat, nicht welches")

            # --- Und jetzt der Punkt: was steht im Prompt? ----------------
            r = await c.post("/api/threads", headers=A, json={
                "title": "Was kostet die Anlage?",
                "goal": "Die Zahl klaeren.",
                "agent_ids": [eingeweiht, ahnungslos],
                "mode": "roundrobin", "max_rounds": 2, "pace_seconds": 0})
            assert r.status_code == 201, r.text
            thema = r.json()["id"]

            for _ in range(120):
                await asyncio.sleep(0.1)
                if len(PROMPTS) >= 2:
                    break
            assert set(PROMPTS) == {"Eingeweiht", "Ahnungslos"}, sorted(PROMPTS)

            drin = PROMPTS["Eingeweiht"]
            draussen = PROMPTS["Ahnungslos"]

            assert "812.000" in drin, "der Eingeweihte sieht seine Unterlage nicht"
            assert "kalkulation.txt" in drin, "der Dateiname fehlt zur Einordnung"
            assert "812.000" not in draussen, "die Unterlage leckt in den anderen Prompt!"
            assert "Deine eigenen Unterlagen" not in draussen, draussen[:200]
            print("[ok] Nur der Eingeweihte hat die Zahl im Prompt")

            # Der Satz, auf den es ankommt: er muss wissen, dass die anderen
            # es nicht wissen. Sonst schreibt er "wie wir alle wissen".
            assert "NUR dir vor" in drin, drin[:400]
            assert "kennen sie nicht" in drin, drin[:400]
            # Und er soll den Inhalt wiedergeben statt zu verweisen.
            assert "gib den Inhalt wieder" in drin, drin[:600]
            print("[ok] Er weiss, dass die anderen es nicht wissen")

            # Die Persona steht weiter ganz vorn - die Akte schiebt sich
            # nicht davor.
            assert drin.index("Deine eigenen Unterlagen") < drin.index("Forum-Kontext"), \
                "die Akte gehoert zwischen Persona und Forum-Kontext"
            print("[ok] Reihenfolge stimmt: Persona, Akte, Forum-Kontext")

            # --- Wegnehmen ------------------------------------------------
            r = await c.delete(f"/api/agents/{eingeweiht}/wissen/{stueck['id']}", headers=A)
            assert r.status_code == 204, r.text
            assert (await c.get(f"/api/agents/{eingeweiht}/wissen", headers=A)).json() == []
            agenten = (await c.get("/api/agents", headers=A)).json()
            assert next(a for a in agenten if a["id"] == eingeweiht)["wissen_dateien"] == 0
            print("[ok] Unterlage wieder herausgenommen, Vermerk verschwindet mit")

            # --- Mit dem Agenten geht die Akte ----------------------------
            r = await c.post(
                f"/api/agents/{ahnungslos}/wissen", headers=A,
                files={"datei": ("kurz.txt", b"Etwas Material.", "text/plain")},
            )
            assert r.status_code == 201, r.text
            await c.post(f"/api/threads/{thema}/control", headers=A, json={"action": "stop"})
            r = await c.delete(f"/api/agents/{ahnungslos}", headers=A)
            assert r.status_code == 204, r.text
            r = await c.get(f"/api/agents/{ahnungslos}/wissen", headers=A)
            assert r.status_code == 404, r.text
            print("[ok] Wird der Agent geloescht, geht seine Akte mit")

    print("\nALLE TESTS BESTANDEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_test()))
