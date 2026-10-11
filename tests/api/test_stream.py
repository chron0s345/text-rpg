"""The SSE generator: subscribe/connect order, event delivery, heartbeat re-confirmation,
overflow, and starting the grace period only when the last local stream closes."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast
from uuid import UUID, uuid4

from app.api.stream import _background, event_stream
from app.engine.events import GameEvent, RoomAudience
from app.realtime.connections import QUEUE_SIZE, ConnectionManager
from app.services.session_service import SessionService


class FakePresence:
    def __init__(self, online: bool = True) -> None:
        self.online = online
        self.connects = 0
        self.disconnects: list[UUID] = []

    async def connect(self, player_id: UUID) -> bool:
        self.connects += 1
        return self.online

    async def disconnect(self, player_id: UUID) -> None:
        self.disconnects.append(player_id)


def parse(chunk: str) -> tuple[str, Any]:
    lines = chunk.strip().splitlines()
    return lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))


def room_event(text: str, room: str = "r") -> GameEvent:
    return GameEvent(kind="say", audience=RoomAudience(room_id=room), text=text)


def open_stream(
    manager: ConnectionManager, presence: FakePresence, player: UUID, **kwargs: Any
) -> Any:
    return event_stream(
        player, "r", None, manager, cast(SessionService, presence), **kwargs
    ).__aiter__()


async def settle() -> None:
    await asyncio.gather(*_background)


async def test_events_reach_the_stream_and_closing_starts_the_grace_period() -> None:
    manager, presence, player = ConnectionManager(), FakePresence(), uuid4()
    stream = open_stream(manager, presence, player)

    assert parse(await anext(stream)) == ("ready", {"room_id": "r"})
    assert manager.connected(player)
    manager.deliver(room_event("Hallo"))
    manager.deliver(room_event("anderswo", room="x"))
    name, data = parse(await anext(stream))
    assert (name, data["text"]) == ("game", "Hallo")

    await stream.aclose()
    await settle()
    assert not manager.connected(player)
    assert presence.disconnects == [player]


async def test_second_tab_keeps_the_character_connected() -> None:
    manager, presence, player = ConnectionManager(), FakePresence(), uuid4()
    first, second = open_stream(manager, presence, player), open_stream(manager, presence, player)
    await anext(first)
    await anext(second)

    await first.aclose()
    await settle()
    assert presence.disconnects == []

    await second.aclose()
    await settle()
    assert presence.disconnects == [player]


async def test_offline_character_gets_closed() -> None:
    manager, presence, player = ConnectionManager(), FakePresence(online=False), uuid4()
    stream = open_stream(manager, presence, player)

    assert parse(await anext(stream)) == ("closed", {"reason": "offline"})
    try:
        await anext(stream)
        raise AssertionError("the stream should have ended")
    except StopAsyncIteration:
        pass
    assert not manager.connected(player)


async def test_heartbeat_reconfirms_and_closes_after_logout() -> None:
    manager, presence, player = ConnectionManager(), FakePresence(), uuid4()
    stream = open_stream(manager, presence, player, heartbeat_seconds=0.01)
    await anext(stream)

    assert await anext(stream) == ": heartbeat\n\n"
    assert presence.connects == 2

    presence.online = False  # logged out elsewhere (other tab, ticker)
    assert parse(await anext(stream)) == ("closed", {"reason": "offline"})
    await stream.aclose()


async def test_slow_client_is_told_to_reload() -> None:
    manager, presence, player = ConnectionManager(), FakePresence(), uuid4()
    stream = open_stream(manager, presence, player)
    await anext(stream)

    for i in range(QUEUE_SIZE + 1):
        manager.deliver(room_event(f"#{i}"))

    assert parse(await anext(stream)) == ("reload", {})
    await stream.aclose()
