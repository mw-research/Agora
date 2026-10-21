"""Laeuft eine BESTEHENDE Datenbank sauber auf den neuen Stand?

Der Rauchtest faengt immer bei einer leeren Datenbank an - dort legt
create_all alles in einem Zug an, und das Nachziehen fehlender Spalten wird
nie geprueft. Genau das passiert aber bei jeder Aktualisierung im Betrieb.

Der Test baut deshalb eine Datenbank im alten Zustand, laesst init_db darauf
los und schaut nach, ob die Bestandszeilen danach brauchbare Werte tragen.
"""
import asyncio
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VERZEICHNIS = tempfile.mkdtemp(prefix="agora-migration-")
DATEI = pathlib.Path(VERZEICHNIS) / "alt.db"
os.environ["AGORA_DATABASE_URL"] = f"sqlite+aiosqlite:///{DATEI}"
os.environ["AGORA_SECRET_KEY"] = "xUqK7nHnV1nQ0Yq4Zk8vZ0mVQ1pQ9yYy2Zt8Xy3Kk3A="
os.environ["AGORA_ADMIN_TOKEN"] = "admin-token-fuer-die-migration-001"
os.environ["AGORA_WORKER_ENABLED"] = "false"

import sqlite3  # noqa: E402

from sqlalchemy.dialects import postgresql, sqlite as sqlite_dialect  # noqa: E402

from app.db import _vorgabe_sql, engine, init_db  # noqa: E402
from app.models import Base  # noqa: E402

# Der Stand vor den geschlossenen Foren: forums ohne 'sichtbar', und die
# Tabellen 'gelesen' und 'mitglieder' gibt es noch gar nicht.
ALTES_SCHEMA = """
CREATE TABLE forums (
    id VARCHAR(32) NOT NULL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    description TEXT,
    parent_id VARCHAR(32),
    position INTEGER,
    creator_id VARCHAR(32) NOT NULL,
    created_at DATETIME
);
CREATE TABLE users (
    id VARCHAR(32) NOT NULL PRIMARY KEY,
    name VARCHAR(80) NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    is_admin BOOLEAN,
    created_at DATETIME
);
CREATE TABLE threads (
    id VARCHAR(32) NOT NULL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    goal TEXT,
    creator_id VARCHAR(32) NOT NULL,
    forum_id VARCHAR(32),
    status VARCHAR(20),
    status_detail TEXT,
    mode VARCHAR(20),
    moderator_agent_id VARCHAR(32),
    max_rounds INTEGER,
    rounds_done INTEGER,
    pace_seconds INTEGER,
    stop_phrase VARCHAR(60),
    synthesize_on_end BOOLEAN,
    next_run_at DATETIME,
    locked_until DATETIME,
    locked_by VARCHAR(64),
    last_agent_id VARCHAR(32),
    created_at DATETIME,
    updated_at DATETIME
);
CREATE TABLE agents (
    id VARCHAR(32) NOT NULL PRIMARY KEY,
    owner_id VARCHAR(32) NOT NULL,
    name VARCHAR(80) NOT NULL,
    role VARCHAR(200),
    persona TEXT,
    model VARCHAR(160) NOT NULL,
    credential_id VARCHAR(32),
    temperature FLOAT,
    max_tokens INTEGER,
    is_public BOOLEAN,
    created_at DATETIME
);
INSERT INTO forums VALUES ('f1', 'Bestandsforum', '', NULL, 0, 'u1', '2026-01-01 00:00:00');
INSERT INTO users  VALUES ('u1', 'altgedient', 'x', 1, '2026-01-01 00:00:00');
INSERT INTO threads VALUES ('t1', 'Bestandsthema', 'Soll sichtbar bleiben', 'u1', 'f1',
    'paused', '', 'roundrobin', NULL, 12, 3, 5, 'EINVERSTANDEN', 1,
    '2026-01-01 00:00:00', NULL, NULL, NULL,
    '2026-01-01 00:00:00', '2026-01-01 00:00:00');
INSERT INTO agents VALUES ('a1', 'u1', 'Altgedienter', 'prueft', 'Du pruefst.',
    'openai/x', NULL, 0.7, 1200, 1, '2026-01-01 00:00:00');
"""


async def main_test() -> int:
    roh = sqlite3.connect(DATEI)
    roh.executescript(ALTES_SCHEMA)
    roh.commit()
    roh.close()
    print("[ok] Datenbank im alten Zustand angelegt")

    await init_db()
    await engine.dispose()

    roh = sqlite3.connect(DATEI)

    tabellen = {n for (n,) in roh.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for neu in ("gelesen", "mitglieder"):
        assert neu in tabellen, f"{neu} fehlt: {sorted(tabellen)}"
    print("[ok] Neue Tabellen sind da")

    spalten = {s[1] for s in roh.execute("PRAGMA table_info(forums)")}
    assert "sichtbar" in spalten, f"sichtbar fehlt: {sorted(spalten)}"

    # Der entscheidende Punkt: das Bestandsforum muss OFFEN sein. Stuende da
    # NULL, waere es nach der Aktualisierung fuer niemanden mehr sichtbar -
    # ein Update, das die halbe Seitenleiste leert.
    wert = roh.execute("SELECT sichtbar FROM forums WHERE id='f1'").fetchone()[0]
    assert wert == "offen", f"Bestandsforum steht auf {wert!r} statt 'offen'"
    print("[ok] Das Bestandsforum ist danach offen, nicht NULL")

    # Und die Fremdschluessel sind eingeschaltet - sonst greift kein
    # ON DELETE CASCADE, und die geschlossenen Raeume raeumen nicht auf.
    assert roh.execute("PRAGMA foreign_keys").fetchone()[0] in (0, 1)
    roh.close()

    # Postgres laeuft auf diesem Rechner nicht - pruefbar ist hier, dass das
    # erzeugte SQL fuer beide Dialekte gueltig aussieht. Ein nacktes
    # DEFAULT offen waere in Postgres ein Spaltenverweis und riesse den
    # Start der naechsten Fassung auseinander.
    for tabelle, spalte in (("forums", "sichtbar"), ("credentials", "auth_style")):
        feld = Base.metadata.tables[tabelle].c[spalte]
        for dialekt in (sqlite_dialect.dialect(), postgresql.dialect()):
            vorgabe = _vorgabe_sql(feld, dialekt)
            assert vorgabe.startswith("'") and vorgabe.endswith("'"), \
                f"{tabelle}.{spalte} auf {dialekt.name}: DEFAULT {vorgabe}"
    print("[ok] Textvorgaben stehen in Anfuehrungszeichen - auch fuer Postgres")

    # Zweimal nachziehen darf nichts kaputt machen: beim Rollout starten Web
    # und Worker gemeinsam, und jeder Pod laeuft hier hindurch.
    await init_db()
    await engine.dispose()
    print("[ok] Ein zweiter Durchlauf aendert nichts und faellt nicht um")

    # --- Haelt das Schema auch ohne das Nachfuellen? -------------------
    # Das Nachfuellen unten raeumt die NULLs weg, bevor ein Modell sie
    # sieht. Das ist die Reparatur an der Wunde - hier wird das Pflaster
    # geprueft: kommt doch einmal NULL durch, darf daraus kein 500 werden.
    from app import schemas as _s

    class _NurNull:
        """Nur-NULL-Attrappe: eine Zeile, wie sie ein ALTER TABLE hinterlaesst."""

        id = "t" * 32
        title = "Alt"
        goal = ""
        creator_id = "u" * 32
        forum_id = None
        status = "paused"
        status_detail = ""
        mode = "roundrobin"
        moderator_agent_id = None
        max_rounds = 12
        rounds_done = 3
        pace_seconds = 5
        stop_phrase = "EINVERSTANDEN"
        synthesize_on_end = True
        created_at = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
        updated_at = created_at
        # Und genau die Felder, die im Betrieb NULL trugen:
        werkzeuge = None
        recherche = None
        art = None

    thema = _s.ThreadOut.model_validate(_NurNull())
    assert thema.werkzeuge is False and thema.recherche is False, thema
    assert thema.art == "frei", thema.art

    class _NurNullAgent:
        id = "a" * 32
        owner_id = "u" * 32
        name = "Alt"
        role = ""
        persona = ""
        model = "openai/x"
        credential_id = None
        temperature = 0.7
        max_tokens = 1200
        is_public = True
        geteilt = None

    agent = _s.AgentOut.model_validate(_NurNullAgent())
    assert agent.geteilt is False, agent
    print("[ok] Die Modelle lesen NULL als Vorgabewert, nicht als Fehler")

    # --- Und jetzt der Punkt, der im Betrieb gefehlt hat ---------------
    # Spalten anlegen genuegt nicht: die API muss eine Bestandszeile auch
    # ausliefern koennen. Genau daran ist 0.37.0 gescheitert - ADD COLUMN
    # ohne DEFAULT liess NULL stehen, und die Antwortmodelle verlangten
    # true/false. Jedes Bestandsthema gab einen 500, und das Forum sah
    # leer aus, obwohl alles dalag.
    import httpx

    from app import main

    transport = httpx.ASGITransport(app=main.app)
    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            K = {"Authorization": f"Bearer {os.environ['AGORA_ADMIN_TOKEN']}"}
            for weg, was in (("/api/threads", "Themen"),
                             ("/api/agents", "Agenten"),
                             ("/api/forums", "Foren"),
                             ("/api/ungelesen", "Ungelesenes")):
                r = await c.get(weg, headers=K)
                assert r.status_code == 200, f"{was}: {r.status_code} {r.text[:200]}"

            themen = (await c.get("/api/threads", headers=K)).json()
            assert any(t["title"] == "Bestandsthema" for t in themen), themen
            alt = next(t for t in themen if t["title"] == "Bestandsthema")
            # Die nachgezogenen Felder muessen als false ankommen, nicht als
            # null und erst recht nicht als Fehler.
            assert alt["werkzeuge"] is False, alt
            assert alt["recherche"] is False, alt
            assert alt["art"] == "frei", alt
            print("[ok] Das Bestandsthema kommt durch die API - ohne 500")

            agenten = (await c.get("/api/agents", headers=K)).json()
            alter = next(a for a in agenten if a["name"] == "Altgedienter")
            assert alter["geteilt"] is False, alter
            assert alter["is_public"] is True, alter
            print("[ok] Der Bestandsagent ebenso")

            # Und die Datenbank steht danach auch nicht halb auf NULL.
            import sqlite3 as _s
            roh = _s.connect(DATEI)
            offen = roh.execute(
                "select count(*) from threads where recherche is null"
            ).fetchone()[0]
            offen += roh.execute(
                "select count(*) from agents where geteilt is null"
            ).fetchone()[0]
            roh.close()
            assert offen == 0, f"{offen} Zeilen stehen noch auf NULL"
            print("[ok] Keine Zeile bleibt auf NULL stehen")

    print("\nALLE TESTS BESTANDEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_test()))
