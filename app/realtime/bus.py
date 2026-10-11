"""Event bus over PostgreSQL LISTEN/NOTIFY (ARCHITECTURE.md, "Warum PostgreSQL LISTEN/NOTIFY").

Every server process runs one ``PostgresEventBus``: it LISTENs on a single channel and hands each
message to its local ``ConnectionManager``, which knows the SSE connections of this process.
Publishing sends one NOTIFY per message, so all processes (including the sender) see it.

Messages (JSON), each with a random ``id``:
- ``{"type": "relocate", "id", "player_id", "room_id", "group_id"}``
- ``{"type": "event", "id", "event": <GameEvent>}``

A NOTIFY whose connection breaks may still have been sent; the retry reuses the payload and
listeners drop ids they have already seen, so retries never deliver a message twice.

NOTIFY payloads are limited to 8000 bytes. An event that does not fit loses its ``data`` and,
if still too large, its text is shortened; if even that is not enough it is skipped (logged).
Clients can always re-read state via the API and catch up via their log.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from sqlalchemy.engine import make_url

from app.engine.events import GameEvent
from app.realtime.connections import ConnectionManager
from app.services.events import Relocation

logger = logging.getLogger(__name__)

CHANNEL = "game_events"
MAX_PAYLOAD_BYTES = 7900  # PostgreSQL limit is 8000, keep headroom
RECONNECT_DELAY_SECONDS = 1.0
MAX_RECONNECT_DELAY_SECONDS = 30.0
# publishing holds the send lock and actions await it: fail fast instead of asyncpg's 60 s
CONNECT_TIMEOUT_SECONDS = 5.0
NOTIFY_TIMEOUT_SECONDS = 5.0
SEEN_MESSAGE_IDS = 1024  # recent ids remembered per process to drop retried duplicates


def asyncpg_dsn(sqlalchemy_url: str) -> str:
    """``postgresql+asyncpg://...`` -> ``postgresql://...`` for a raw asyncpg connection."""
    return (
        make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(hide_password=False)
    )


def _dump(message: dict[str, Any]) -> str:
    # ensure_ascii=False: umlauts stay 2 UTF-8 bytes instead of 6-byte \u escapes
    return json.dumps(message, ensure_ascii=False)


def _message_id() -> str:
    return uuid4().hex


def _fits(payload: str) -> bool:
    return len(payload.encode()) <= MAX_PAYLOAD_BYTES


def encode_event(event: GameEvent) -> str | None:
    """JSON payload for NOTIFY, shrunk to the size limit; ``None`` if it cannot fit at all
    (e.g. a huge ``exclude_player_ids`` list) — such an event is skipped and logged."""
    message_id = _message_id()
    payload = _dump({"type": "event", "id": message_id, "event": event.model_dump(mode="json")})
    if _fits(payload):
        return payload
    slim = event.model_copy(update={"data": {}})

    def with_text(length: int) -> str:
        text = slim.text if length == len(slim.text) else slim.text[:length] + "…"
        event_json = {**slim.model_dump(mode="json"), "text": text}
        return _dump({"type": "event", "id": message_id, "event": event_json})

    if _fits(with_text(len(slim.text))):
        return with_text(len(slim.text))
    if not _fits(with_text(0)):
        return None
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
            "id": _message_id(),
            "player_id": str(relocation.player_id),
            "room_id": relocation.room_id,
            "group_id": str(relocation.group_id) if relocation.group_id else None,
        }
    )


class PostgresEventBus:
    """Implements ``EventPublisher``; ``start()`` must be called before use (app lifespan).

    Whenever messages may have been lost for this process (listener connection lost, publishing
    failed), all local subscriptions are marked ``needs_reload``: the client must re-read its
    view and the SSE handler re-subscribes with room and group from the database.

    If publishing fails halfway through a batch, other processes with a healthy listener may
    have received only part of it and are not told; their clients catch up via the log.
    """

    def __init__(self, database_url: str, manager: ConnectionManager) -> None:
        self._dsn = asyncpg_dsn(database_url)
        self.manager = manager
        self._listener: asyncpg.Connection[Any] | None = None
        self._sender: asyncpg.Connection[Any] | None = None
        self._send_lock = asyncio.Lock()
        self._closing = False
        self._reconnect_task: asyncio.Task[None] | None = None
        self._seen_ids: OrderedDict[str, None] = OrderedDict()

    # --- lifecycle --------------------------------------------------------------------------

    async def start(self) -> None:
        self._closing = False
        await self._listen()

    async def stop(self) -> None:
        self._closing = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
            self._reconnect_task = None
        for connection in (self._listener, self._sender):
            if connection is not None and not connection.is_closed():
                await connection.close()
        self._listener = self._sender = None

    async def _listen(self) -> None:
        listener = await asyncpg.connect(self._dsn, timeout=CONNECT_TIMEOUT_SECONDS)
        try:
            await listener.add_listener(CHANNEL, self._on_notify)
            listener.add_termination_listener(self._on_terminated)
        except BaseException:
            with contextlib.suppress(Exception):
                await listener.close()
            raise
        self._listener = listener

    def _on_terminated(self, _connection: object) -> None:
        if self._closing or (self._reconnect_task is not None and not self._reconnect_task.done()):
            return
        logger.warning("event bus listener connection lost, reconnecting")
        # from now on messages are lost for this process; flag clients right away, not only
        # once the database is reachable again
        self.manager.mark_all_for_reload()
        self._reconnect_task = asyncio.get_running_loop().create_task(self._reconnect())

    async def _reconnect(self) -> None:
        delay = RECONNECT_DELAY_SECONDS
        while not self._closing:
            try:
                await self._listen()
            except asyncio.CancelledError:
                raise
            except Exception:
                # never give up: without a listener this process gets no realtime events
                logger.exception("event bus reconnect failed, retrying in %.1f s", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY_SECONDS)
                continue
            # again: clients that subscribed during the outage missed messages too
            self.manager.mark_all_for_reload()
            return

    # --- publishing -------------------------------------------------------------------------

    async def publish(
        self, events: Sequence[GameEvent], relocations: Sequence[Relocation] = ()
    ) -> None:
        payloads = [encode_relocation(r) for r in relocations]
        for event in events:
            if event.audience.kind == "actor":
                continue
            payload = encode_event(event)
            if payload is None:
                logger.error("event %s does not fit into a NOTIFY payload, skipped", event.kind)
                continue
            payloads.append(payload)
        if not payloads:
            return
        async with self._send_lock:
            try:
                # one connection, sequential NOTIFYs: every listener sees them in this order
                for payload in payloads:
                    await self._notify(payload)
            except Exception:
                # Others may have missed messages (e.g. a relocation) - at least this process
                # makes its clients reload. If the database is unreachable for the sender, the
                # listeners of other processes lose their connection too and do the same.
                self.manager.mark_all_for_reload()
                raise

    async def _notify(self, payload: str) -> None:
        for attempt in (1, 2):
            try:
                if self._sender is None or self._sender.is_closed():
                    self._sender = await asyncpg.connect(self._dsn, timeout=CONNECT_TIMEOUT_SECONDS)
                await self._sender.execute(
                    "SELECT pg_notify($1, $2)", CHANNEL, payload, timeout=NOTIFY_TIMEOUT_SECONDS
                )
                return
            except asyncpg.InvalidParameterValueError:
                logger.error("NOTIFY payload rejected by PostgreSQL, skipped")
                return
            except Exception:
                # A broken connection surfaces as anything from OSError to asyncpg's
                # InternalClientError; start over on a fresh one (CancelledError passes through).
                if self._sender is not None:
                    with contextlib.suppress(Exception):
                        await self._sender.close()
                self._sender = None
                if attempt == 2:
                    raise

    # --- receiving --------------------------------------------------------------------------

    def _on_notify(self, _connection: object, _pid: int, _channel: str, payload: str) -> None:
        try:
            message = json.loads(payload)
            if self._already_seen(message.get("id")):
                return
            if message["type"] == "relocate":
                group = message["group_id"]
                self.manager.relocate(
                    UUID(message["player_id"]), message["room_id"], UUID(group) if group else None
                )
            elif message["type"] == "event":
                self.manager.deliver(GameEvent.model_validate(message["event"]))
        except Exception:
            logger.exception("ignoring malformed event bus message")

    def _already_seen(self, message_id: object) -> bool:
        if not isinstance(message_id, str):
            return False
        if message_id in self._seen_ids:
            return True
        self._seen_ids[message_id] = None
        if len(self._seen_ids) > SEEN_MESSAGE_IDS:
            self._seen_ids.popitem(last=False)
        return False
