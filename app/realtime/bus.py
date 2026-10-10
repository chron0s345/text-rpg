"""Event bus over PostgreSQL LISTEN/NOTIFY (ARCHITECTURE.md, "Warum PostgreSQL LISTEN/NOTIFY").

Every server process runs one ``PostgresEventBus``: it LISTENs on a single channel and hands each
message to its local ``ConnectionManager``, which knows the SSE connections of this process.
Publishing sends one NOTIFY per message, so all processes (including the sender) see it.

Messages (JSON):
- ``{"type": "relocate", "player_id", "room_id", "group_id"}``
- ``{"type": "event", "event": <GameEvent>}``

NOTIFY payloads are limited to 8000 bytes. An event that does not fit loses its ``data`` (and,
if still too large, its text is shortened); clients can always re-read state via the API.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import asyncpg
from sqlalchemy.engine import make_url

from app.engine.events import GameEvent
from app.realtime.connections import ConnectionManager
from app.services.events import Relocation

logger = logging.getLogger(__name__)

CHANNEL = "game_events"
MAX_PAYLOAD_BYTES = 7900  # PostgreSQL limit is 8000, keep headroom
RECONNECT_DELAY_SECONDS = 1.0


def asyncpg_dsn(sqlalchemy_url: str) -> str:
    """``postgresql+asyncpg://...`` -> ``postgresql://...`` for a raw asyncpg connection."""
    return (
        make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(hide_password=False)
    )


def _dump(message: dict[str, Any]) -> str:
    # ensure_ascii=False: umlauts stay 2 UTF-8 bytes instead of 6-byte \u escapes
    return json.dumps(message, ensure_ascii=False)


def _fits(payload: str) -> bool:
    return len(payload.encode()) <= MAX_PAYLOAD_BYTES


def encode_event(event: GameEvent) -> str:
    payload = _dump({"type": "event", "event": event.model_dump(mode="json")})
    if _fits(payload):
        return payload
    slim = event.model_copy(update={"data": {}})

    def with_text(length: int) -> str:
        text = slim.text if length == len(slim.text) else slim.text[:length] + "…"
        return _dump({"type": "event", "event": {**slim.model_dump(mode="json"), "text": text}})

    if _fits(with_text(len(slim.text))):
        return with_text(len(slim.text))
    # longest prefix that still fits (binary search: characters have 1-4 bytes)
    low, high = 0, len(slim.text)
    while low < high:
        middle = (low + high + 1) // 2
        if _fits(with_text(middle)):
            low = middle
        else:
            high = middle - 1
    return with_text(low)


def encode_relocation(relocation: Relocation) -> str:
    return _dump(
        {
            "type": "relocate",
            "player_id": str(relocation.player_id),
            "room_id": relocation.room_id,
            "group_id": str(relocation.group_id) if relocation.group_id else None,
        }
    )


class PostgresEventBus:
    """Implements ``EventPublisher``; ``start()`` must be called before use (app lifespan)."""

    def __init__(self, database_url: str, manager: ConnectionManager) -> None:
        self._dsn = asyncpg_dsn(database_url)
        self.manager = manager
        self._listener: asyncpg.Connection[Any] | None = None
        self._sender: asyncpg.Connection[Any] | None = None
        self._send_lock = asyncio.Lock()
        self._closing = False
        self._reconnect_task: asyncio.Task[None] | None = None

    # --- lifecycle --------------------------------------------------------------------------

    async def start(self) -> None:
        self._closing = False
        await self._listen()

    async def stop(self) -> None:
        self._closing = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
        for connection in (self._listener, self._sender):
            if connection is not None and not connection.is_closed():
                await connection.close()
        self._listener = self._sender = None

    async def _listen(self) -> None:
        listener = await asyncpg.connect(self._dsn)
        await listener.add_listener(CHANNEL, self._on_notify)
        listener.add_termination_listener(self._on_terminated)
        self._listener = listener

    def _on_terminated(self, _connection: object) -> None:
        if self._closing:
            return
        logger.warning("event bus listener connection lost, reconnecting")
        self._reconnect_task = asyncio.get_running_loop().create_task(self._reconnect())

    async def _reconnect(self) -> None:
        # Events sent while disconnected are lost for this process; clients catch up via the log.
        while not self._closing:
            try:
                await self._listen()
                return
            except (OSError, asyncpg.PostgresError):
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    # --- publishing -------------------------------------------------------------------------

    async def publish(
        self, events: Sequence[GameEvent], relocations: Sequence[Relocation] = ()
    ) -> None:
        payloads = [encode_relocation(r) for r in relocations]
        payloads += [encode_event(e) for e in events if e.audience.kind != "actor"]
        if not payloads:
            return
        async with self._send_lock:
            if self._sender is None or self._sender.is_closed():
                self._sender = await asyncpg.connect(self._dsn)
            # one connection, sequential NOTIFYs: every listener sees them in this order
            for payload in payloads:
                await self._sender.execute("SELECT pg_notify($1, $2)", CHANNEL, payload)

    # --- receiving --------------------------------------------------------------------------

    def _on_notify(self, _connection: object, _pid: int, _channel: str, payload: str) -> None:
        try:
            message = json.loads(payload)
            if message["type"] == "relocate":
                group = message["group_id"]
                self.manager.relocate(
                    UUID(message["player_id"]), message["room_id"], UUID(group) if group else None
                )
            elif message["type"] == "event":
                self.manager.deliver(GameEvent.model_validate(message["event"]))
        except (ValueError, KeyError, TypeError):
            logger.exception("ignoring malformed event bus message")
