"""World ticker against PostgreSQL: one tick per test at a fixed time."""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import col

from app.content.schemas import Ambush
from app.db.models import (
    CombatParticipation,
    FloorItem,
    GroupInvite,
    ItemSpawnState,
    LogEntry,
    MonsterInstance,
    Trade,
)
from app.db.models import Player as PlayerRow
from app.services.world_ticker import WorldTicker
from tests.db.factories import add_player
from tests.services.conftest import ServiceHarness


@pytest.fixture
async def ticker(harness: ServiceHarness, engine: AsyncEngine) -> AsyncIterator[WorldTicker]:
    h = harness
    ticker = WorldTicker(
        engine,
        h.service.sessions,
        h.content,
        h.service,
        h.publisher,
        clock=h.clock,
        rng_factory=lambda: random.Random(11),
    )
    try:
        yield ticker
    finally:
        await ticker.release_lock()


async def player(h: ServiceHarness, room: str, **fields: Any) -> PlayerRow:
    h.committed.rooms.append(room)
    async with h.committed.session() as db, db.begin():
        row = await add_player(db, current_room_id=room, **fields)
        h.committed.accounts.append(row.account_id)
    return row


async def add(h: ServiceHarness, *rows: Any) -> None:
    async with h.committed.session() as db, db.begin():
        db.add_all(rows)


async def get(h: ServiceHarness, model: type[Any], key: object) -> Any:
    async with h.committed.session() as db:
        return await db.get(model, key)


# --- 1. monsters ---


async def test_dead_monsters_come_back_when_due(
    harness: ServiceHarness, ticker: WorldTicker
) -> None:
    h = harness
    room = h.scratch_room()
    watcher = await player(h, room)
    due = MonsterInstance(
        monster_id="goblin", room_id=room, hp=0, alive=False, respawn_at=h.clock.now
    )
    later = MonsterInstance(
        monster_id="wolf",
        room_id=room,
        hp=0,
        alive=False,
        respawn_at=h.clock.now + timedelta(seconds=1),
    )
    await add(h, due, later)

    report = await ticker.tick()

    back = await get(h, MonsterInstance, due.id)
    assert (back.alive, back.hp, back.respawn_at) == (
        True,
        h.content.monsters["goblin"].max_hp,
        None,
    )
    assert not (await get(h, MonsterInstance, later.id)).alive
    assert report.monsters_respawned >= 1
    assert [e.kind for e in h.publisher.published] == ["monster.respawned"]
    async with h.committed.session() as db:
        kinds = (
            await db.scalars(
                select(col(LogEntry.kind)).where(col(LogEntry.player_id) == watcher.id)
            )
        ).all()
    assert kinds == ["monster.respawned"]


# --- 2. + 3. items ---


async def test_spawn_items_come_back_and_expired_items_vanish(
    harness: ServiceHarness, ticker: WorldTicker
) -> None:
    h = harness
    camp = h.scratch_room("goblin_camp")  # bread x2, respawn 120 s
    now = h.clock.now
    old = FloorItem(room_id=camp, item_id="goblin_ear", source="drop", expires_at=now)
    fresh = FloorItem(
        room_id=camp, item_id="goblin_ear", source="loot", expires_at=now + timedelta(seconds=1)
    )
    await add(h, ItemSpawnState(room_id=camp, item_id="bread", next_spawn_at=now), old, fresh)

    await ticker.tick()

    async with h.committed.session() as db:
        lying = (await db.scalars(select(FloorItem).where(col(FloorItem.room_id) == camp))).all()
    assert sorted((f.item_id, f.quantity, f.source) for f in lying) == [
        ("bread", 2, "spawn"),
        ("goblin_ear", 1, "loot"),
    ]
    state = await get(h, ItemSpawnState, (camp, "bread"))
    assert state.next_spawn_at is None


# --- 4. grace periods ---


async def test_expired_grace_period_logs_out(harness: ServiceHarness, ticker: WorldTicker) -> None:
    h = harness
    room = h.scratch_room()
    grace = timedelta(seconds=h.content.rules.timings.disconnect_grace_seconds)
    gone = await player(h, room, disconnected_at=h.clock.now - grace)
    waiting = await player(h, room, disconnected_at=h.clock.now - grace + timedelta(seconds=1))
    connected = await player(h, room)

    report = await ticker.tick()

    row = await get(h, PlayerRow, gone.id)
    assert (row.online, row.state, row.current_room_id, row.disconnected_at) == (
        False,
        "exploring",
        h.content.rules.town_room_id,
        None,
    )
    assert (await get(h, PlayerRow, waiting.id)).online
    assert (await get(h, PlayerRow, connected.id)).online
    assert report.connections_expired == 1


async def test_expired_grace_period_in_combat_kills(
    harness: ServiceHarness, ticker: WorldTicker
) -> None:
    h = harness
    room = h.scratch_room()
    grace = timedelta(seconds=h.content.rules.timings.disconnect_grace_seconds)
    monster = MonsterInstance(monster_id="goblin", room_id=room, hp=10)
    await add(h, monster)
    fighter = await player(
        h,
        room,
        state="in_combat",
        combat_target_id=monster.id,
        gold=100,
        disconnected_at=h.clock.now - grace,
    )
    await add(
        h,
        CombatParticipation(
            monster_instance_id=monster.id, player_id=fighter.id, party_key=f"solo:{fighter.id}"
        ),
    )

    await ticker.tick()

    row = await get(h, PlayerRow, fighter.id)
    assert (row.online, row.state, row.current_room_id) == (False, "dead", room)
    assert row.gold < 100
    async with h.committed.session() as db:
        texts = (
            await db.scalars(
                select(col(LogEntry.text)).where(col(LogEntry.player_id) == fighter.id)
            )
        ).all()
    assert any(t.startswith("Du bist gestorben.") for t in texts)  # read after the next login


# --- 5. ambushes ---


@pytest.fixture
def eager_goblins(harness: ServiceHarness) -> Iterator[None]:
    """Goblins ambush idling players on every tick (restored afterwards: shared content)."""
    monsters = harness.content.monsters
    original = monsters["goblin"]
    assert original.ambush is not None
    monsters["goblin"] = original.model_copy(
        update={"ambush": Ambush(chance_on_enter=0, chance_per_tick=1, max_level_difference=None)}
    )
    try:
        yield
    finally:
        monsters["goblin"] = original


async def test_idling_players_get_ambushed(
    harness: ServiceHarness, ticker: WorldTicker, eager_goblins: None
) -> None:
    h = harness
    room = h.scratch_room()
    monster = MonsterInstance(monster_id="goblin", room_id=room, hp=10)
    await add(h, monster)
    victim = await player(h, room, hp=500, base_max_hp=500)
    dead = await player(h, room, hp=0, state="dead")
    offline = await player(h, room, online=False)

    report = await ticker.tick()

    row = await get(h, PlayerRow, victim.id)
    assert (row.state, row.combat_target_id) == ("in_combat", monster.id)
    assert row.hp < 500  # the monster strikes first
    assert row.last_action_at is None  # not a player action: no rate limit hit afterwards
    assert (await get(h, PlayerRow, dead.id)).state == "dead"
    assert (await get(h, PlayerRow, offline.id)).state == "exploring"
    assert report.ambushes == 1
    ambush = next(
        e for e in h.publisher.published if e.kind == "ambush" and e.audience.kind == "players"
    )
    assert ambush.audience.player_ids == [victim.id]  # type: ignore[union-attr]

    # a fighting monster does not ambush anybody else
    second = await player(h, room, hp=500, base_max_hp=500)
    h.clock.advance(seconds=5)
    await ticker.tick()
    assert (await get(h, PlayerRow, second.id)).state == "exploring"


async def test_no_ambush_in_safe_rooms(
    harness: ServiceHarness, ticker: WorldTicker, eager_goblins: None
) -> None:
    h = harness
    town = h.scratch_room("town_square")
    await add(h, MonsterInstance(monster_id="goblin", room_id=town, hp=10))
    idler = await player(h, town)
    await ticker.tick()
    assert (await get(h, PlayerRow, idler.id)).state == "exploring"


# --- 6. invites & trade requests ---


async def test_expired_invites_and_trade_requests_are_removed(
    harness: ServiceHarness, ticker: WorldTicker
) -> None:
    h = harness
    town = h.scratch_room("town_square")
    anna, ben = await player(h, town), await player(h, town)
    now = h.clock.now
    expired_request = Trade(initiator_id=anna.id, partner_id=ben.id, room_id=town, expires_at=now)
    open_trade = Trade(
        initiator_id=ben.id, partner_id=anna.id, room_id=town, status="open", expires_at=now
    )
    await add(
        h,
        GroupInvite(inviter_id=anna.id, player_id=ben.id, expires_at=now),
        GroupInvite(inviter_id=ben.id, player_id=anna.id, expires_at=now + timedelta(seconds=1)),
        expired_request,
        open_trade,
    )

    await ticker.tick()

    assert await get(h, GroupInvite, (anna.id, ben.id)) is None
    assert await get(h, GroupInvite, (ben.id, anna.id)) is not None
    assert await get(h, Trade, expired_request.id) is None
    assert await get(h, Trade, open_trade.id) is not None  # only requests expire


# --- leadership ---


async def test_only_one_process_ticks(harness: ServiceHarness, engine: AsyncEngine) -> None:
    h = harness

    def make() -> WorldTicker:
        return WorldTicker(engine, h.service.sessions, h.content, h.service, h.publisher)

    first, second = make(), make()
    try:
        assert await first.hold_lock()
        assert await first.hold_lock()  # still held, not stacked
        assert not await second.hold_lock()
        await first.release_lock()
        assert await second.hold_lock()  # takes over
    finally:
        await first.release_lock()
        await second.release_lock()


async def test_run_ticks_and_releases_the_lock_on_stop(
    harness: ServiceHarness, engine: AsyncEngine
) -> None:
    h = harness
    room = h.scratch_room()
    monster = MonsterInstance(
        monster_id="goblin", room_id=room, hp=0, alive=False, respawn_at=h.clock.now
    )
    await add(h, monster)
    ticker = WorldTicker(
        engine, h.service.sessions, h.content, h.service, h.publisher, clock=h.clock
    )
    stop = asyncio.Event()
    task = asyncio.create_task(ticker.run(stop))
    for _ in range(100):  # the first tick runs right away
        if (await get(h, MonsterInstance, monster.id)).alive:
            break
        await asyncio.sleep(0.05)
    assert (await get(h, MonsterInstance, monster.id)).alive

    stop.set()
    await asyncio.wait_for(task, timeout=5)
    other = WorldTicker(engine, h.service.sessions, h.content, h.service, h.publisher)
    try:
        assert await other.hold_lock()
    finally:
        await other.release_lock()
