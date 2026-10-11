"""Live events (SSE, ``GET /stream``) and log catch-up (``GET /log``), ARCHITECTURE.md "Echtzeit".

One stream per tab. Opening it ends a disconnect grace period; when the last stream of the
character in this process closes, the grace period starts. Every heartbeat re-confirms the
connection: that undoes a grace period started elsewhere (another worker's server start, a
second tab closing in another process) and ends the stream if the character was logged out.

Events are sent as named SSE events ``game`` with the ``GameEvent`` as JSON; the HTMX views
will render them into partials. ``reload``: events were dropped (client too slow), the client
must reload its state; ``closed``: the character is not in the world any more.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.api.deps import NO_CHARACTER, CurrentPlayer, Services
from app.api.schemas import LogEntryResponse, LogPage
from app.realtime.connections import ConnectionManager
from app.services.logbook import read_log
from app.services.session_service import SessionService

router = APIRouter(tags=["realtime"])

HEARTBEAT_SECONDS = 15.0
MAX_LOG_PAGE = 200

# disconnect calls must outlive the cancelled stream task that starts them
_background: set[asyncio.Task[None]] = set()


def _spawn(coroutine: Coroutine[Any, Any, None]) -> None:
    task = asyncio.get_running_loop().create_task(coroutine)
    _background.add(task)
    task.add_done_callback(_background.discard)


def sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def event_stream(
    player_id: UUID,
    room_id: str,
    group_id: UUID | None,
    manager: ConnectionManager,
    presence: SessionService,
    *,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    # subscribe before connecting: no event between the two is lost
    subscription = manager.subscribe(player_id, room_id, group_id)
    try:
        if not await presence.connect(player_id):
            yield sse("closed", {"reason": "offline"})
            return
        yield sse("ready", {"room_id": room_id})
        while True:
            if subscription.overflowed:
                yield sse("reload", {})
                return
            try:
                event = await asyncio.wait_for(subscription.queue.get(), heartbeat_seconds)
            except TimeoutError:
                if not await presence.connect(player_id):
                    yield sse("closed", {"reason": "offline"})
                    return
                yield ": heartbeat\n\n"
                continue
            yield sse("game", event.model_dump(mode="json"))
    finally:
        manager.unsubscribe(subscription)
        if not manager.connected(player_id):
            _spawn(presence.disconnect(player_id))


@router.get("/stream")
async def stream(player_id: CurrentPlayer, services: Services) -> StreamingResponse:
    location = await services.presence.location(player_id)
    if location is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NO_CHARACTER)
    room_id, group_id = location
    return StreamingResponse(
        event_stream(player_id, room_id, group_id, services.connections, services.presence),
        media_type="text/event-stream",
        # proxies must not buffer the stream (ARCHITECTURE.md, "Deployment")
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/log")
async def log(
    player_id: CurrentPlayer,
    services: Services,
    after: Annotated[int | None, Query(ge=0)] = None,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LOG_PAGE)] = 50,
) -> LogPage:
    async with services.sessions() as session:
        entries = await read_log(session, player_id, after=after, before=before, limit=limit)
    return LogPage(
        entries=[
            LogEntryResponse(id=e.id or 0, kind=e.kind, text=e.text, created_at=e.created_at)
            for e in entries
        ]
    )
