"""``GET /stream`` (Server-Sent Events) and ``GET /log`` (ARCHITECTURE.md, "Echtzeit").

SSE messages (named events for the HTMX SSE extension):
- ``event: log``    – a game event of another player or the world, rendered as a log ``<li>``
                      (escaped; ``class="log-<kind>"``), appended to the game log
- ``event: update`` – the world around the player changed: the page reloads its panels
                      (``GET /ui/panels``, debounced); not sent for chat
- ``event: reload`` – events were dropped (client too slow); reload the page
- ``event: ended``  – the character left the world (logout, other character chosen)
- ``: ping``        – heartbeat comment, every ``heartbeat_seconds``

The actor's own results come with the action response, not via SSE.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlmodel import col

from app.api.deps import PlayerId, Services, ServicesDep, not_playing
from app.api.schemas import LogEntryOut, LogPage
from app.db.models import LogEntry
from app.engine.events import GameEvent
from app.realtime.connections import Subscription
from app.web.render import log_line

router = APIRouter(tags=["game"])

LOG_PAGE_SIZE = 50
RETRY_MILLISECONDS = 3000


def sse_message(event: str, data: str) -> str:
    lines = "".join(f"data: {line}\n" for line in data.split("\n"))
    return f"event: {event}\n{lines}\n"


def encode_game_event(event: GameEvent) -> str:
    message = sse_message("log", log_line(event.kind, event.text))
    # chat and announcements change no state: no panel refresh (an announcement reaches every
    # client at once and would make all of them reload their panels at the same moment)
    if not event.kind.startswith(("chat.", "server.")):
        message += sse_message("update", "{}")
    return message


async def event_stream(
    services: Services, subscription: Subscription, heartbeat_seconds: float
) -> AsyncIterator[str]:
    """Yields SSE messages until the character leaves the world, events may be missing (client
    too slow, event bus reconnected; the client reloads and reconnects) or the
    client disconnects (then the generator is cancelled)."""
    player_id = subscription.player_id
    yield f"retry: {RETRY_MILLISECONDS}\n\n"
    while True:
        if subscription.needs_reload:
            yield sse_message("reload", "{}")
            return
        try:
            event = await asyncio.wait_for(subscription.queue.get(), timeout=heartbeat_seconds)
        except TimeoutError:
            if not await services.presence.heartbeat(player_id):
                yield sse_message("ended", "{}")
                return
            yield ": ping\n\n"
            continue
        yield encode_game_event(event)


def _closed(services: Services, subscription: Subscription) -> None:
    """Stream ended: stop routing events to it; the last stream of a character starts the
    disconnect grace period. Runs as a task: the stream itself may be cancelled right now."""
    services.connections.unsubscribe(subscription)
    player_id = subscription.player_id
    if services.connections.connected(player_id):
        return
    task = asyncio.get_running_loop().create_task(services.presence.disconnected(player_id))
    services.background.add(task)
    task.add_done_callback(services.background.discard)


@router.get("/stream")
async def stream(request: Request, player_id: PlayerId, services: ServicesDep) -> StreamingResponse:
    target = await services.presence.connect(player_id)
    if target is None:
        raise not_playing(request)
    # Subscribed after reading room and group: an event in between is missed; clients reload
    # their view on (re)connect and catch up via GET /log?after=.
    subscription = services.connections.subscribe(player_id, target.room_id, target.group_id)

    async def body() -> AsyncIterator[str]:
        try:
            async for message in event_stream(services, subscription, services.heartbeat_seconds):
                yield message
        finally:
            _closed(services, subscription)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/log")
async def log(
    player_id: PlayerId,
    services: ServicesDep,
    before: Annotated[int | None, Query(ge=1)] = None,
    after: Annotated[int | None, Query(ge=0)] = None,
) -> LogPage:
    """The character's log, oldest first. ``after``: entries newer than that id (catching up
    after a reconnect); ``before``: older history; neither: the latest entries."""
    if before is not None and after is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "before oder after, nicht beides."
        )
    stmt = select(LogEntry).where(col(LogEntry.player_id) == player_id)
    if after is not None:
        stmt = stmt.where(col(LogEntry.id) > after).order_by(col(LogEntry.id))
    else:
        if before is not None:
            stmt = stmt.where(col(LogEntry.id) < before)
        stmt = stmt.order_by(col(LogEntry.id).desc())
    async with services.sessions() as session:
        rows = list((await session.scalars(stmt.limit(LOG_PAGE_SIZE))).all())
    if after is None:
        rows.reverse()
    return LogPage(entries=[_entry(row) for row in rows])


def _entry(row: LogEntry) -> LogEntryOut:
    assert row.id is not None
    return LogEntryOut(id=row.id, kind=row.kind, text=row.text, created_at=row.created_at)
