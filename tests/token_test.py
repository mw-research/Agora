"""Ueberlebt ein erneuertes Token den Neustart?

Der Fehler aus dem Betrieb: nach einem Neustart des Clusters galt wieder
das allererste Token. Wer es nicht mehr hatte, kam nicht mehr hinein - und
wer es noch hatte, kam wieder hinein, obwohl es zurueckgesetzt worden war.

Schuld war _bootstrap_admin: es schrieb bei jedem Start das Token aus
AGORA_ADMIN_TOKEN zurueck, sobald es vom gespeicherten abwich. Der Test
faehrt den Lebenszyklus deshalb mehrfach - genau das ist ein Neustart.
"""
import asyncio
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VERZEICHNIS = tempfile.mkdtemp(prefix="agora-token-")
os.environ["AGORA_DATABASE_URL"] = f"sqlite+aiosqlite:///{VERZEICHNIS}/t.db"
os.environ["AGORA_SECRET_KEY"] = "xUqK7nHnV1nQ0Yq4Zk8vZ0mVQ1pQ9yYy2Zt8Xy3Kk3A="
os.environ["AGORA_ADMIN_TOKEN"] = "das-allererste-token-aus-der-umgebung"
os.environ["AGORA_WORKER_ENABLED"] = "false"

import httpx  # noqa: E402

from app import main  # noqa: E402
from app.config import get_settings  # noqa: E402

UMGEBUNG = os.environ["AGORA_ADMIN_TOKEN"]


def kopf(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def main_test() -> int:
    transport = httpx.ASGITransport(app=main.app)

    async def neustart():
        """Einen Lebenszyklus fahren - das ist ein Pod-Neustart."""
        async with main.app.router.lifespan_context(main.app):
            pass

    # --- Erster Start: das Konto entsteht --------------------------------
    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/api/me", headers=kopf(UMGEBUNG))
            assert r.status_code == 200 and r.json()["name"] == "admin", r.text
            print("[ok] Erster Start legt das Admin-Konto aus der Umgebung an")

            # Eine zweite Person mit Verwaltungsrechten - sie soll dem
            # Installationskonto gleich ein neues Token geben.
            r = await c.post("/api/users", headers=kopf(UMGEBUNG),
                             json={"name": "markus", "is_admin": True})
            assert r.status_code == 201, r.text
            r = await c.post("/api/login",
                             json={"token": r.json()["token"], "pin": "Markus-PIN-4711"})
            assert r.status_code == 200, r.text
            c.cookies.clear()
            markus = r.json()["new_token"]

            leute = (await c.get("/api/users", headers=kopf(markus))).json()
            admin_id = next(x["id"] for x in leute if x["name"] == "admin")
            r = await c.post(f"/api/users/{admin_id}/reset", headers=kopf(markus),
                             json={"pin_zuruecksetzen": False})
            assert r.status_code == 200, r.text
            neues_admin_token = r.json()["token"]
            assert neues_admin_token != UMGEBUNG
            print("[ok] Das Token des Installationskontos wurde erneuert")

            # Sofort danach gilt das alte nicht mehr.
            r = await c.get("/api/me", headers=kopf(UMGEBUNG))
            assert r.status_code == 401, "das alte Token darf nicht mehr gelten"

    # --- Neustart ---------------------------------------------------------
    await neustart()

    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            # Das ist der Kern: das alte Token darf NICHT wieder gelten.
            r = await c.get("/api/me", headers=kopf(UMGEBUNG))
            assert r.status_code == 401, (
                "Das Token aus der Umgebung gilt nach dem Neustart wieder - "
                "genau der Fehler, um den es geht."
            )
            print("[ok] Nach dem Neustart gilt das alte Token NICHT wieder")

            # Auch nicht ueber die Anmeldung. Das war der eigentliche Weg
            # hinein: der alte Start liess das Konto auf one_time stehen und
            # schrieb den Umgebungstoken zurueck - wer den kannte, konnte das
            # Admin-Konto nach jedem Neustart neu einloesen.
            r = await c.post("/api/login",
                             json={"token": UMGEBUNG, "pin": "Egal-PIN-4711"})
            assert r.status_code == 401, (
                "Mit dem Umgebungstoken laesst sich das Admin-Konto neu "
                f"einloesen: {r.text}"
            )
            c.cookies.clear()
            print("[ok] Und es laesst sich damit auch nicht neu einloesen")

            # Und markus kommt weiterhin mit seinem eigenen hinein.
            r = await c.get("/api/me", headers=kopf(markus))
            assert r.status_code == 200 and r.json()["name"] == "markus", r.text
            print("[ok] Das Token einer gewoehnlichen Person bleibt unberuehrt")

            # Das erneuerte Einmal-Token des Admin-Kontos taugt weiter zum
            # Einloesen - es wurde vom Start nicht ueberschrieben.
            r = await c.post("/api/login",
                             json={"token": neues_admin_token, "pin": "Admin-PIN-4711"})
            assert r.status_code == 200, f"das erneuerte Token wurde zerstoert: {r.text}"
            c.cookies.clear()
            print("[ok] Das erneuerte Token hat den Neustart ueberlebt")

    # --- Der Notschluessel ------------------------------------------------
    get_settings.cache_clear()
    os.environ["AGORA_ADMIN_TOKEN_UEBERNEHMEN"] = "true"
    await neustart()

    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/api/me", headers=kopf(UMGEBUNG))
            assert r.status_code == 200, (
                "Mit AGORA_ADMIN_TOKEN_UEBERNEHMEN muss der Weg zurueck offen sein: "
                f"{r.text}"
            )
            print("[ok] Der Notschluessel oeffnet - aber nur, wenn man ihn setzt")

    os.environ.pop("AGORA_ADMIN_TOKEN_UEBERNEHMEN")
    get_settings.cache_clear()

    print("\nALLE TESTS BESTANDEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_test()))
