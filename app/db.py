import logging
from collections.abc import AsyncIterator

from sqlalchemy import event, inspect, literal, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings
from .models import Base

log = logging.getLogger(__name__)

_settings = get_settings()

_connect_args = {}
if not _settings.is_postgres:
    _connect_args["check_same_thread"] = False

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_pre_ping=_settings.is_postgres,
    connect_args=_connect_args,
)


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_fremdschluessel(dbapi_connection, _record) -> None:
    """SQLite haelt Fremdschluessel nur, wenn man es darum bittet.

    Ohne das hier ist jedes ON DELETE CASCADE im Datenmodell auf SQLite
    wirkungslos, waehrend Postgres es einhaelt - dieselbe Anwendung
    verhaelt sich auf zwei Datenbanken verschieden, und was auf dem Server
    geloescht wird, bleibt beim Entwickeln liegen. Seit die Sichtbarkeit
    geschlossener Foren daran haengt, ist das kein Schoenheitsfehler mehr.
    """
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _vorgabe_sql(spalte, dialect) -> str | None:
    """Die Vorgabe einer Spalte als SQL-Literal.

    server_default="offen" traegt die nackte Zeichenkette. Direkt eingesetzt
    wird daraus DEFAULT offen - was SQLite als Text durchgehen laesst und
    Postgres als Spaltennamen liest ("column \"offen\" does not exist").
    Deshalb wird hier ueber den Literal-Compiler gegangen, der jede Sorte
    richtig setzt: Text in Anfuehrungszeichen, Zahlen nackt, und SQL-Ausdruecke
    wie text("now()") unveraendert.
    """
    vorgabe = spalte.server_default
    if vorgabe is None:
        return None
    arg = vorgabe.arg
    if isinstance(arg, str):
        return literal(arg).compile(
            dialect=dialect, compile_kwargs={"literal_binds": True}
        ).string
    # Ein SQL-Ausdruck bleibt, wie er ist.
    return str(arg.compile(dialect=dialect))


def _add_missing_columns(connection) -> None:
    """Fehlende Spalten nachziehen.

    create_all legt nur fehlende *Tabellen* an. Kommt eine Spalte hinzu,
    laeuft eine bestehende Datenbank sonst in "no such column" - und zwar
    erst beim ersten Zugriff, nicht beim Start.

    Bewusst klein gehalten: nur ADD COLUMN, kein Umbenennen, kein Typwechsel.
    Sobald das Datenmodell ernsthaft wandert, gehoert Alembic hierher.
    """
    inspector = inspect(connection)
    vorhandene_tabellen = set(inspector.get_table_names())

    for tabelle in Base.metadata.sorted_tables:
        if tabelle.name not in vorhandene_tabellen:
            continue
        bekannt = {spalte["name"] for spalte in inspector.get_columns(tabelle.name)}
        for spalte in tabelle.columns:
            if spalte.name in bekannt:
                continue
            typ = spalte.type.compile(connection.dialect)
            sql = f"ALTER TABLE {tabelle.name} ADD COLUMN {spalte.name} {typ}"
            vorgabe = _vorgabe_sql(spalte, connection.dialect)
            if vorgabe is not None:
                sql += f" DEFAULT {vorgabe}"
            log.info("Datenbank: ergaenze %s.%s", tabelle.name, spalte.name)
            connection.execute(text(sql))


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
