"""PostgresEventBus against a real PostgreSQL: two buses play two server processes."""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.schemas import MoveAction, SayAction
from app.content.loader import load_content
from app.engine.events import GameEvent, GroupAudience, PlayersAudience, RoomAudience
from app.realtime.bus import MAX_PAYLOAD_BYTES, PostgresEventBus, encode_event
from app.realtime.connections import ConnectionManager, Subscription
from app.services.action_service import ActionService
from app.services.events import Relocation
from tests.conftest import DATA_DIR
from tests.db.factories import NOW, add_player
from tests.pg import Committed

TIMEOUT = 5


@pytest.fixture
async def buses(database_url: str) -> AsyncIterator[tuple[PostgresEventBus, PostgresEventBus]]:
    first = PostgresEventBus(database_url, ConnectionManager())
    second = PostgresEventBus(database_url, ConnectionManager())
    await first.start()
    await second.start()
    try:
        yield first, second
    finally:
        await first.stop()
        await second.stop()


def room_event(room: str, text_: str = "hallo", **fields: object) -> GameEvent:
    return GameEvent.model_validate(
        {"kind": "chat.say", "audience": RoomAudience(room_id=room), "text": text_, **fields}
    )


async def next_event(sub: Subscription) -> GameEvent:
    return await asyncio.wait_for(sub.queue.get(), timeout=TIMEOUT)


async def nothing_arrives(sub: Subscription, wait: float = 0.3) -> bool:
    await asyncio.sleep(wait)
    return sub.queue.empty()


async def test_event_reaches_connections_in_every_process(
    buses: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    first, second = buses
    local = first.manager.subscribe(uuid4(), "forest")
    remote = second.manager.subscribe(uuid4(), "forest")
    elsewhere = second.manager.subscribe(uuid4(), "town")

    await first.publish([room_event("forest")])

    assert (await next_event(local)).text == "hallo"
    assert (await next_event(remote)).text == "hallo"
    assert await nothing_arrives(elsewhere)


async def test_group_players_exclusions_and_order(
    buses: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    first, second = buses
    group = uuid4()
    member = second.manager.subscribe(uuid4(), "town", group)
    target = second.manager.subscribe(uuid4(), "cave")
    excluded = second.manager.subscribe(uuid4(), "forest")

    events = [
        GameEvent(kind="a", audience=GroupAudience(group_id=group), text="1"),
        GameEvent(kind="b", audience=PlayersAudience(player_ids=[target.player_id]), text="2"),
        room_event("forest", exclude_player_ids=[excluded.player_id]),
        GameEvent(kind="c", audience=GroupAudience(group_id=group), text="3"),
    ]
    await first.publish(events)

    assert [(await next_event(member)).text, (await next_event(member)).text] == ["1", "3"]
    assert (await next_event(target)).text == "2"
    assert await nothing_arrives(excluded)


async def test_relocation_is_applied_before_events(
    buses: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    first, second = buses
    player = uuid4()
    sub = second.manager.subscribe(player, "forest")

    await first.publish([room_event("town", "neu hier")], [Relocation(player, "town", None)])

    assert (await next_event(sub)).text == "neu hier"
    assert sub.room_id == "town"


async def test_oversized_event_is_shrunk_but_delivered(
    buses: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    first, second = buses
    sub = second.manager.subscribe(uuid4(), "forest")
    huge = room_event("forest", "ä" * 6000, data={"blob": "x" * 9000})
    assert len(encode_event(huge).encode()) <= MAX_PAYLOAD_BYTES

    await first.publish([huge])

    delivered = await next_event(sub)
    assert delivered.data == {}
    assert delivered.text.endswith("…")
    # as much text as fits survives (6000 umlauts = 12000 bytes, about 7800 bytes fit)
    assert 3500 < len(delivered.text) < 4000
    assert set(delivered.text[:-1]) == {"ä"}


async def test_listener_reconnects_after_connection_loss(
    buses: tuple[PostgresEventBus, PostgresEventBus], engine: AsyncEngine
) -> None:
    first, second = buses
    sub = second.manager.subscribe(uuid4(), "forest")
    assert second._listener is not None
    pid = second._listener.get_server_pid()
    async with engine.connect() as admin:
        await admin.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})

    for _ in range(50):  # wait for the reconnect
        listener = second._listener
        if listener is not None and not listener.is_closed() and listener.get_server_pid() != pid:
            break
        await asyncio.sleep(0.1)
    await first.publish([room_event("forest", "wieder da")])

    assert (await next_event(sub)).text == "wieder da"


# --- end to end with the ActionService ---


async def test_action_events_reach_other_players_live(
    buses: tuple[PostgresEventBus, PostgresEventBus],
    engine: AsyncEngine,
    committed: Committed,
) -> None:
    first, second = buses
    content = load_content(DATA_DIR)
    service = ActionService(
        async_sessionmaker(engine, expire_on_commit=False),
        content,
        first,
        clock=lambda: NOW,
        rng_factory=lambda: random.Random(7),
    )
    committed.rooms.extend(["forest_gate", "dark_forest"])
    async with committed.session() as db, db.begin():
        mover = await add_player(db, current_room_id="forest_gate")
        watcher = await add_player(db, current_room_id="dark_forest")
        committed.accounts += [mover.account_id, watcher.account_id]

    mover_stream = first.manager.subscribe(mover.id, "forest_gate")
    watcher_stream = second.manager.subscribe(watcher.id, "dark_forest")

    await service.execute(mover.id, MoveAction(direction="south"))
    entered = await next_event(watcher_stream)
    assert entered.kind == "move.enter"
    # the mover's own results come with the HTTP response, never via SSE
    assert await nothing_arrives(mover_stream)
    assert mover_stream.room_id == "dark_forest"  # its connection followed the move

    await service.execute(watcher.id, SayAction(text="Willkommen!"))
    said = await next_event(mover_stream)
    assert said.text.endswith("sagt: Willkommen!")
