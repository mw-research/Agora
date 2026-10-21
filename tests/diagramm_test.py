"""Diagramme aus dem Rechner.

Der wichtigste Punkt ist nicht, dass ein Bild entsteht, sondern wo es
NICHT landet: der Inhalt eines Rechner-Beitrags geht in den Prompt der
naechsten Runde. Ein Base64-PNG waere dort zwanzigtausend Token - in jeder
Runde erneut. Deshalb liegen Bilder in einer eigenen Spalte.
"""
import asyncio
import base64
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VERZEICHNIS = tempfile.mkdtemp(prefix="agora-diagramm-")
os.environ["AGORA_DATABASE_URL"] = f"sqlite+aiosqlite:///{VERZEICHNIS}/d.db"
os.environ["AGORA_SECRET_KEY"] = "xUqK7nHnV1nQ0Yq4Zk8vZ0mVQ1pQ9yYy2Zt8Xy3Kk3A="
os.environ["AGORA_ADMIN_TOKEN"] = "admin-token-fuer-den-diagrammtest"
os.environ["AGORA_WERKZEUGE"] = "true"
os.environ["AGORA_WERKZEUG_SEKUNDEN"] = "90"
os.environ["AGORA_WORKER_ENABLED"] = "true"
os.environ["AGORA_WORKER_INTERVAL"] = "0.2"

import httpx  # noqa: E402

from app import llm, main, orchestrator, werkzeuge  # noqa: E402

ZEICHNEN = '''```rechnen
import matplotlib.pyplot as plt
werte = {"A": 3, "B": 7, "C": 5}
plt.figure(figsize=(5, 3))
plt.bar(list(werte), list(werte.values()))
plt.ylabel("Anzahl")
plt.show()
print("Summe:", sum(werte.values()))
```'''

PROMPTS: list[str] = []
GESAGT: list[str] = []


async def fake_stream(agent, messages, dk=None):
    PROMPTS.append("\n".join(str(m.get("content") or "") for m in messages))
    text = GESAGT.pop(0) if GESAGT else "Nichts weiter."
    yield "delta", text, None
    yield "usage", "", None


async def fake_complete(agent, messages, max_tokens=None, dk=None):
    return "egal"


llm.stream = fake_stream
llm.complete = fake_complete


async def main_test() -> int:
    # --- Der Sandkasten zeichnet ueberhaupt ---------------------------
    ausgabe, bilder, ok = await werkzeuge.ausfuehren(
        werkzeuge.bloecke_finden(ZEICHNEN)[0], sekunden=90
    )
    assert ok, ausgabe
    assert len(bilder) == 1, f"{len(bilder)} Bilder: {ausgabe[:200]}"
    roh = base64.b64decode(bilder[0])
    assert roh.startswith(b"\x89PNG\r\n\x1a\n"), "kein PNG"
    assert "Summe: 15" in ausgabe, ausgabe
    # Die matplotlib-Warnung ueber den Zwischenspeicher darf nicht in der
    # Ausgabe stehen - sie sieht fuer den Agenten wie ein Fehler aus.
    assert "cache directory" not in ausgabe.lower(), ausgabe
    print(f"[ok] Der Sandkasten zeichnet: {len(roh)} Bytes PNG, Text daneben")

    # --- scipy ist wirklich da ----------------------------------------
    pakete = werkzeuge.verfuegbare_pakete()
    assert "scipy" in pakete, pakete
    ausgabe, _, ok = await werkzeuge.ausfuehren(
        "import scipy.optimize as so\nprint(round(so.brentq(lambda x: x*x-2, 0, 2), 6))",
        sekunden=90,
    )
    assert ok and "1.414214" in ausgabe, ausgabe
    print("[ok] scipy steht bereit und rechnet")

    # --- Und jetzt der Weg durch das Forum -----------------------------
    transport = httpx.ASGITransport(app=main.app)
    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post("/api/login", json={
                "token": os.environ["AGORA_ADMIN_TOKEN"], "pin": "Diagramm-PIN-11"})
            assert r.status_code == 200, r.text
            c.cookies.clear()
            tok = r.json().get("new_token") or os.environ["AGORA_ADMIN_TOKEN"]
            H = {"Authorization": f"Bearer {tok}"}

            agenten = []
            for name in ("Zeichner", "Leser"):
                r = await c.post("/api/agents", headers=H,
                                 json={"name": name, "model": "openai/x"})
                assert r.status_code == 201, r.text
                agenten.append(r.json()["id"])

            GESAGT.extend([ZEICHNEN, "Ich ordne ein.", "Und ich auch."])
            r = await c.post("/api/threads", headers=H, json={
                "title": "Zeig mir die Zahlen",
                "goal": "Ein Bild sagt mehr.",
                "agent_ids": agenten,
                "mode": "roundrobin", "max_rounds": 3, "pace_seconds": 0,
                "werkzeuge": True})
            assert r.status_code == 201, r.text
            thema = r.json()["id"]

            rechnerbeitrag = None
            for _ in range(200):
                await asyncio.sleep(0.1)
                posts = (await c.get(f"/api/threads/{thema}/posts", headers=H)).json()
                rechnerbeitrag = next(
                    (p for p in posts if p["author_type"] == "tool"), None
                )
                if rechnerbeitrag and len(posts) >= 3:
                    break
            assert rechnerbeitrag, "der Rechner hat nie geantwortet"

            assert rechnerbeitrag["bilder"], "kein Bild am Beitrag"
            assert len(rechnerbeitrag["bilder"]) == 1, rechnerbeitrag["bilder"]
            assert base64.b64decode(rechnerbeitrag["bilder"][0]).startswith(
                b"\x89PNG\r\n\x1a\n"
            )
            print("[ok] Das Bild haengt am Rechner-Beitrag")

            # DER Punkt: nichts davon im Inhalt, also nichts davon im Prompt.
            inhalt = rechnerbeitrag["content"]
            assert "base64" not in inhalt and "iVBOR" not in inhalt, inhalt[:200]
            assert "Summe: 15" in inhalt, inhalt
            print("[ok] Der Inhalt traegt den Text, nicht das Bild")

            spaeter = [p for p in PROMPTS if "Ausgabe der Rechnung" in p]
            assert spaeter, "kein Prompt mit der Rechenausgabe"
            for prompt in spaeter:
                assert "iVBOR" not in prompt, "Base64 im Prompt!"
                assert len(prompt) < 40_000, f"Prompt ist {len(prompt)} Zeichen gross"
            print(f"[ok] Kein Byte davon im Prompt ({len(spaeter)} geprueft)")

            # Der Agent muss wissen, dass er sein Bild nicht wiedersieht.
            assert any("NICHT wieder" in p for p in PROMPTS), \
                "der Hinweis zum Beschreiben fehlt im Prompt"
            print("[ok] Der Agent wird aufgefordert, das Bild zu beschreiben")

    print("\nALLE TESTS BESTANDEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_test()))
