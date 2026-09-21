import logging
from collections.abc import AsyncIterator

from sqlalchemy import inspect, text
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

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


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
            if spalte.server_default is not None:
                sql += f" DEFAULT {spalte.server_default.arg}"
            log.info("Datenbank: ergaenze %s.%s", tabelle.name, spalte.name)
            connection.execute(text(sql))


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
