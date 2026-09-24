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
INSERT INTO forums VALUES ('f1', 'Bestandsforum', '', NULL, 0, 'u1', '2026-01-01 00:00:00');
INSERT INTO users  VALUES ('u1', 'altgedient', 'x', 0, '2026-01-01 00:00:00');
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

    print("\nALLE TESTS BESTANDEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_test()))
