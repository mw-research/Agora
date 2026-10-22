"""Kann eine Eingabe die Datenbank sprengen?

Eine String(n)-Spalte, die aus einem Schema befuellt wird, braucht dort
dieselbe Grenze. Fehlt sie, kommt kein sauberes 422, sondern Postgres weist
die Zeile ab und die API antwortet mit einem nackten 500 - in der
Oberflaeche sichtbar als "Unexpected token 'I'".

Genau so ist es passiert: AgentCreate.role hatte keine Grenze, die Spalte
fasst 200 Zeichen, und jemand setzte einen Absatz von 700 hinein. SQLite
schluckt das stillschweigend und schneidet nicht einmal ab - der Fehler
zeigt sich erst auf Postgres, also erst im Betrieb. Deshalb prueft dieser
Test die Schemata selbst und nicht nur einen Aufruf.
"""
import asyncio
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VERZEICHNIS = tempfile.mkdtemp(prefix="agora-laengen-")
os.environ["AGORA_DATABASE_URL"] = f"sqlite+aiosqlite:///{VERZEICHNIS}/l.db"
os.environ["AGORA_SECRET_KEY"] = "xUqK7nHnV1nQ0Yq4Zk8vZ0mVQ1pQ9yYy2Zt8Xy3Kk3A="
os.environ["AGORA_ADMIN_TOKEN"] = "admin-token-fuer-den-laengentest"
os.environ["AGORA_WORKER_ENABLED"] = "false"

import httpx  # noqa: E402
from sqlalchemy import String  # noqa: E402

from app import main, schemas  # noqa: E402
from app.models import Base  # noqa: E402

# Welches Schema fuellt welche Tabelle? Kommt eine Tabelle hinzu, gehoert
# sie hier hinein - oder ausdruecklich als leer markiert.
ZUORDNUNG: dict[str, list[str]] = {
    "agents": ["AgentCreate", "AgentUpdate"],
    "credentials": ["CredentialCreate", "CredentialUpdate"],
    "forums": ["ForumCreate", "ForumUpdate"],
    "threads": ["ThreadCreate", "ThreadUpdate"],
    "users": ["UserCreate"],
    "access_requests": ["RequestCreate"],
    # Kommen nicht aus einem Schema:
    "posts": [],          # der Server setzt die Felder selbst
    "participants": [],
    "uebersetzungen": [],
    "gelesen": [],
    "mitglieder": [],
    "wissen": [],         # Name kommt aus dem Dateinamen, siehe add_wissen
}


def grenze(feld) -> int | None:
    for pruefung in getattr(feld, "metadata", []):
        wert = getattr(pruefung, "max_length", None)
        if wert is not None:
            return wert
    return None


async def main_test() -> int:
    # --- Jede Spalte gegen ihr Schema ---------------------------------
    fehlt = [t.name for t in Base.metadata.sorted_tables if t.name not in ZUORDNUNG]
    assert not fehlt, f"Neue Tabelle ohne Zuordnung in diesem Test: {fehlt}"

    luecken = []
    for tabelle in Base.metadata.sorted_tables:
        spalten = {
            s.name: s.type.length
            for s in tabelle.columns
            if isinstance(s.type, String) and s.type.length
        }
        for schema_name in ZUORDNUNG[tabelle.name]:
            schema = getattr(schemas, schema_name)
            for name, feld in schema.model_fields.items():
                if name not in spalten:
                    continue
                hat = grenze(feld)
                if hat is None or hat > spalten[name]:
                    luecken.append(
                        f"{schema_name}.{name}: Spalte {spalten[name]}, "
                        f"Schema {hat if hat is not None else 'ohne Grenze'}"
                    )
    assert not luecken, "Diese Felder passen nicht in ihre Spalte:\n  " + "\n  ".join(luecken)
    print("[ok] Jedes Textfeld passt in seine Spalte")

    # --- Und die Meldung, die dabei herauskommt ------------------------
    transport = httpx.ASGITransport(app=main.app)
    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/api/login",
                json={"token": os.environ["AGORA_ADMIN_TOKEN"], "pin": "Laengen-PIN-11"},
            )
            assert r.status_code == 200, r.text
            c.cookies.clear()
            tok = r.json().get("new_token") or os.environ["AGORA_ADMIN_TOKEN"]
            H = {"Authorization": f"Bearer {tok}"}

            # Genau der Fall aus dem Betrieb: ein Absatz in der Rollenzeile.
            lang = "Diese Rolle beschreibt ausfuehrlich, was der Agent tut. " * 13
            assert len(lang) > 200

            r = await c.post("/api/agents", headers=H,
                             json={"name": "Lang", "model": "openai/x", "role": lang})
            assert r.status_code == 422, f"muss 422 sein, nicht {r.status_code}: {r.text[:200]}"
            meldung = r.json()["detail"]
            # Ein Satz, kein Objekt - sonst steht in der Oberflaeche
            # "[object Object]".
            assert isinstance(meldung, str), meldung
            assert "role" in meldung and "200" in meldung, meldung
            print(f"[ok] Zu lange Rolle: 422 mit \"{meldung}\"")

            r = await c.post("/api/agents", headers={**H, "X-Agora-Sprache": "en"},
                             json={"name": "Lang2", "model": "openai/x", "role": lang})
            assert "too long" in r.json()["detail"], r.json()
            print("[ok] Auch auf Englisch ein Satz")

            # Das Aendern war die Hintertuer: AgentUpdate hatte gar keine
            # Grenzen, nicht einmal die, die AgentCreate schon hatte.
            r = await c.post("/api/agents", headers=H,
                             json={"name": "Kurz", "model": "openai/x", "role": "prueft"})
            assert r.status_code == 201, r.text
            agent_id = r.json()["id"]
            for feld, wert in (("role", lang), ("name", "N" * 81), ("model", "m" * 161)):
                r = await c.patch(f"/api/agents/{agent_id}", headers=H, json={feld: wert})
                assert r.status_code == 422, f"{feld} beim Aendern: {r.status_code}"
                assert feld in r.json()["detail"], r.json()
            print("[ok] Auch beim Aendern, nicht nur beim Anlegen")

            # Was passt, geht weiterhin durch.
            r = await c.patch(f"/api/agents/{agent_id}", headers=H,
                              json={"role": "prueft Annahmen und Methodik"})
            assert r.status_code == 200, r.text
            assert r.json()["role"] == "prueft Annahmen und Methodik"
            print("[ok] Eine kurze Rolle geht unveraendert durch")

    print("\nALLE TESTS BESTANDEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_test()))
