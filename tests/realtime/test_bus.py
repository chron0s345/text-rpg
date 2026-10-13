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
    encoded = encode_event(huge)
    assert encoded is not None and len(encoded.encode()) <= MAX_PAYLOAD_BYTES

    await first.publish([huge])

    delivered = await next_event(sub)
    assert delivered.data == {}
    assert delivered.text.endswith("…")
    # as much text as fits survives (6000 umlauts = 12000 bytes, about 7800 bytes fit)
    assert 3500 < len(delivered.text) < 4000
    assert set(delivered.text[:-1]) == {"ä"}


async def test_listener_reconnects_and_marks_clients_for_reload(
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
    # anything sent during the gap is lost: the client has to reload its view
    assert sub.needs_reload


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


# --- review follow-ups ---


async def test_unsendable_event_is_skipped_and_the_rest_delivered(
    buses: tuple[PostgresEventBus, PostgresEventBus],
) -> None:
    first, second = buses
    sub = second.manager.subscribe(uuid4(), "forest")
    impossible = room_event("forest", "zu gross", exclude_player_ids=[uuid4() for _ in range(250)])
    assert encode_event(impossible) is None

    await first.publish([room_event("forest", "a"), impossible, room_event("forest", "c")])

    assert [(await next_event(sub)).text, (await next_event(sub)).text] == ["a", "c"]
    assert not sub.needs_reload


async def test_reconnect_survives_unexpected_errors(
    buses: tuple[PostgresEventBus, PostgresEventBus],
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncpg

    from app.realtime import bus as bus_module

    first, second = buses
    monkeypatch.setattr(bus_module, "RECONNECT_DELAY_SECONDS", 0.05)
    original = second._listen
    failures = 0

    async def flaky_listen() -> None:
        nonlocal failures
        if failures < 2:
            failures += 1
            raise asyncpg.InterfaceError("not ready")
        await original()

    monkeypatch.setattr(second, "_listen", flaky_listen)
    sub = second.manager.subscribe(uuid4(), "forest")
    assert second._listener is not None
    pid = second._listener.get_server_pid()
    async with engine.connect() as admin:
        await admin.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})

    for _ in range(100):
        if failures == 2 and second._reconnect_task is not None and second._reconnect_task.done():
            break
        await asyncio.sleep(0.05)
    await first.publish([room_event("forest", "trotzdem da")])

    assert (await next_event(sub)).text == "trotzdem da"
    assert failures == 2


async def test_failed_publish_marks_local_clients_for_reload(
    buses: tuple[PostgresEventBus, PostgresEventBus], monkeypatch: pytest.MonkeyPatch
) -> None:
    first, _second = buses
    local = first.manager.subscribe(uuid4(), "forest")

    async def unreachable(payload: str) -> None:
        raise OSError("database unreachable")

    monkeypatch.setattr(first, "_notify", unreachable)
    with pytest.raises(OSError):
        await first.publish([room_event("forest")])
    assert local.needs_reload


async def test_sender_reconnects_after_connection_loss(
    buses: tuple[PostgresEventBus, PostgresEventBus], engine: AsyncEngine
) -> None:
    first, second = buses
    sub = second.manager.subscribe(uuid4(), "forest")
    await first.publish([room_event("forest", "eins")])
    assert (await next_event(sub)).text == "eins"
    assert first._sender is not None
    async with engine.connect() as admin:
        await admin.execute(
            text("SELECT pg_terminate_backend(:pid)"), {"pid": first._sender.get_server_pid()}
        )

    await first.publish([room_event("forest", "zwei")])

    assert (await next_event(sub)).text == "zwei"


# --- adversary review follow-ups (no database needed) ---


def offline_bus() -> PostgresEventBus:
    return PostgresEventBus("postgresql+asyncpg://user:pw@localhost/none", ConnectionManager())


async def test_retried_message_is_delivered_once() -> None:
    bus = offline_bus()
    sub = bus.manager.subscribe(uuid4(), "forest")
    payload = encode_event(room_event("forest", "einmal"))
    assert payload is not None

    bus._on_notify(None, 0, "game_events", payload)
    bus._on_notify(None, 0, "game_events", payload)  # same id: the sender retried

    assert (await next_event(sub)).text == "einmal"
    assert sub.queue.empty()
    # a different message with the same content is not a duplicate
    again = encode_event(room_event("forest", "einmal"))
    assert again is not None
    bus._on_notify(None, 0, "game_events", again)
    assert (await next_event(sub)).text == "einmal"


async def test_sender_retries_on_any_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncpg

    sent: list[str] = []

    class FakeConnection:
        def __init__(self, broken: bool) -> None:
            self.broken = broken
            self.closed = False

        def is_closed(self) -> bool:
            return self.closed

        async def close(self) -> None:
            self.closed = True

        async def execute(self, _query: str, _channel: str, payload: str, **_: object) -> None:
            if self.broken:
                # what asyncpg raises on a connection whose backend was terminated
                raise asyncpg.exceptions._base.InternalClientError("cannot switch to state 12")
            sent.append(payload)

    connections = [FakeConnection(broken=True), FakeConnection(broken=False)]

    async def connect(*_: object, **__: object) -> FakeConnection:
        return connections.pop(0)

    monkeypatch.setattr(asyncpg, "connect", connect)
    bus = offline_bus()

    await bus.publish([room_event("forest", "zwei")])

    assert len(sent) == 1 and '"zwei"' in sent[0]
    assert connections == []


async def test_lost_listener_marks_clients_for_reload_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = offline_bus()
    sub = bus.manager.subscribe(uuid4(), "forest")
    database_down = asyncio.Event()

    async def reconnect_never_succeeds() -> None:
        await database_down.wait()

    monkeypatch.setattr(bus, "_reconnect", reconnect_never_succeeds)

    bus._on_terminated(None)
    bus._on_terminated(None)  # a second notification does not start a second reconnect

    assert sub.needs_reload
    assert bus._reconnect_task is not None and not bus._reconnect_task.done()
    await bus.stop()
    assert bus._reconnect_task is None
