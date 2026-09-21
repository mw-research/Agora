"""Event-Bus fuer den Live-Stream.

Der Worker, der einen Beitrag erzeugt, laeuft im Cluster in einem anderen Pod
als die API-Pods, an denen die Browser haengen. Ein Bus im Prozessspeicher
wuerde deshalb nur zufaellig funktionieren. Wir nutzen darum Postgres
LISTEN/NOTIFY: jeder Prozess haelt eine Listener-Verbindung und verteilt
eingehende Events an seine lokalen SSE-Abonnenten.

Ohne Postgres (SQLite-Entwicklungsmodus) faellt der Bus auf reine
Prozess-lokale Zustellung zurueck.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from .config import get_settings

log = logging.getLogger(__name__)

CHANNEL = "agora_events"
# pg_notify erlaubt max. 8000 Byte Payload.
MAX_PAYLOAD = 7000


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._conn = None
        self._task: asyncio.Task | None = None

    # -- Lifecycle --------------------------------------------------------
    async def start(self) -> None:
        settings = get_settings()
        if not settings.is_postgres:
            log.info("EventBus: prozesslokaler Modus (kein Postgres)")
            return
        import asyncpg

        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        self._conn = await asyncpg.connect(dsn)
        await self._conn.add_listener(CHANNEL, self._on_notify)
        log.info("EventBus: LISTEN %s aktiv", CHANNEL)

    async def stop(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.remove_listener(CHANNEL, self._on_notify)
                await self._conn.close()
            self._conn = None

    # -- Publish ----------------------------------------------------------
    async def publish(self, event: dict) -> None:
        payload = json.dumps(event, ensure_ascii=False, default=str)
        if self._conn is None:
            self._fanout(payload)
            return
        if len(payload.encode()) > MAX_PAYLOAD:
            # Uebergrosse Deltas werden nicht gestreamt; der Client holt den
            # fertigen Beitrag beim post.done-Event per REST nach.
            event = {k: v for k, v in event.items() if k != "text"}
            event["truncated"] = True
            payload = json.dumps(event, ensure_ascii=False, default=str)
        try:
            await self._conn.execute("SELECT pg_notify($1, $2)", CHANNEL, payload)
        except Exception:
            log.exception("EventBus: NOTIFY fehlgeschlagen, lokale Zustellung")
            self._fanout(payload)

    def _on_notify(self, _conn, _pid, _channel, payload: str) -> None:
        self._fanout(payload)

    def _fanout(self, payload: str) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Langsamer Client: wir werfen ihn nicht raus, er verliert nur
                # Deltas und holt sich den Endstand beim naechsten post.done.
                pass

    # -- Subscribe --------------------------------------------------------
    @contextlib.asynccontextmanager
    async def subscribe(self):
        """Liefert eine Queue mit JSON-Payloads aller Events.

        Gefiltert wird beim Abnehmer (siehe SSE-Endpunkt): so kann derselbe
        Bus spaeter auch eine Uebersichtsseite mit allen Threads speisen.
        """
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)


bus = EventBus()
