"""WorldTicker against PostgreSQL: respawns, cleanup, idle ambushes, expired requests, step
isolation, and the single-leader advisory lock."""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel import col

from app.content.registry import ContentRegistry
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
from app.db.repository import InconsistentChangesError, Repository
from app.engine.events import FloorItemCreate, StateChanges
from app.services.events import RecordingPublisher
from app.services.world_ticker import TickerLeadership, TickReport, WorldTicker
from tests.db.factories import NOW, add_player
from tests.pg import Committed
from tests.services.conftest import Clock, ServiceHarness


@pytest.fixture
def ticker(engine: AsyncEngine, harness: ServiceHarness) -> WorldTicker:
    return WorldTicker(
        engine,
        async_sessionmaker(engine, expire_on_commit=False),
        harness.content,
        harness.publisher,
        clock=harness.clock,
        rng_factory=lambda: random.Random(7),
    )


@pytest.fixture
def ambusher(content: ContentRegistry) -> Iterator[str]:
    """A monster that always ambushes idling players (removed from the content afterwards)."""
    monster_id = f"lurker-{uuid4().hex[:6]}"
    content.monsters[monster_id] = content.get_monster("goblin").model_copy(
        update={
            "id": monster_id,
            "name": "Lauerer",
            "ambush": Ambush(chance_on_enter=0, chance_per_tick=1.0),
        }
    )
    yield monster_id
    del content.monsters[monster_id]


async def player(h: ServiceHarness, room: str, **fields: Any) -> PlayerRow:
    h.committed.rooms.append(room)
    async with h.committed.session() as db, db.begin():
        row = await add_player(db, current_room_id=room, **fields)
        h.committed.accounts.append(row.account_id)
    return row


async def add(committed: Committed, *rows: Any) -> None:
    async with committed.session() as db, db.begin():
        db.add_all(rows)


async def load(committed: Committed, model: type[Any], key: object) -> Any:
    async with committed.session() as db:
        return await db.get(model, key)


async def logs(committed: Committed, player_id: UUID) -> list[str]:
    async with committed.session() as db:
        rows = await db.scalars(
            select(LogEntry).where(col(LogEntry.player_id) == player_id).order_by(col(LogEntry.id))
        )
        return [r.text for r in rows.all()]


# --- 1. monster respawns ---


async def test_due_monsters_respawn_and_the_room_learns_about_it(
    harness: ServiceHarness, ticker: WorldTicker
) -> None:
    h = harness
    room = h.scratch_room()
    watcher = await player(h, room)
    due = MonsterInstance(
        monster_id="goblin", room_id=room, hp=0, alive=False, respawn_at=NOW, tagged_by_party="x"
    )
    later = MonsterInstance(
        monster_id="goblin",
        room_id=room,
        hp=0,
        alive=False,
        respawn_at=NOW + timedelta(seconds=1),
    )
    await add(h.committed, due, later)

    report = await ticker.tick()

    assert report.failed_steps == []
    row = await load(h.committed, MonsterInstance, due.id)
    assert (row.alive, row.hp, row.respawn_at, row.tagged_by_party) == (True, 12, None, None)
    assert (await load(h.committed, MonsterInstance, later.id)).alive is False
    assert await logs(h.committed, watcher.id) == ["Goblin-Späher taucht auf."]
    [event] = [e for e in h.publisher.published if e.kind == "monster.respawned"]
    assert event.audience.model_dump() == {"kind": "room", "room_id": room}


# --- 2. item respawns ---


async def test_spawn_item_reappears_once(harness: ServiceHarness, ticker: WorldTicker) -> None:
    h = harness
    camp = h.scratch_room("goblin_camp")  # 2x bread, respawns after 120 s
    await add(h.committed, ItemSpawnState(room_id=camp, item_id="bread", next_spawn_at=NOW))

    first = await ticker.tick()
    second = await ticker.tick()

    assert (first.items_respawned, second.items_respawned) == (1, 0)
    async with h.committed.session() as db:
        floor = (await db.scalars(select(FloorItem).where(col(FloorItem.room_id) == camp))).all()
    assert [(f.item_id, f.quantity, f.source, f.expires_at) for f in floor] == [
        ("bread", 2, "spawn", None)
    ]
    state = await load(h.committed, ItemSpawnState, (camp, "bread"))
    assert state.next_spawn_at is None
    assert [e.text for e in h.publisher.published if e.kind == "floor.spawned"] == [
        "2x Frisches Brot liegt jetzt hier."
    ]


async def test_spawn_item_still_lying_is_not_duplicated(
    harness: ServiceHarness, ticker: WorldTicker
) -> None:
    h = harness
    camp = h.scratch_room("goblin_camp")
    await add(
        h.committed,
        FloorItem(room_id=camp, item_id="bread", quantity=1, source="spawn"),
        ItemSpawnState(room_id=camp, item_id="bread", next_spawn_at=NOW),
    )

    await ticker.tick()

    async with h.committed.session() as db:
        floor = (await db.scalars(select(FloorItem).where(col(FloorItem.room_id) == camp))).all()
    assert [f.quantity for f in floor] == [1]
    assert (await load(h.committed, ItemSpawnState, (camp, "bread"))).next_spawn_at is None


async def test_spawn_lock_only_allows_creating_that_spawn(
    harness: ServiceHarness, engine: AsyncEngine
) -> None:
    """``apply_changes`` accepts a floor item under a spawn-state lock only if it is that spawn."""
    h = harness
    camp = h.scratch_room("goblin_camp")
    await add(h.committed, ItemSpawnState(room_id=camp, item_id="bread", next_spawn_at=NOW))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db, db.begin():
        repo = Repository(db)
        await repo.lock_due_item_spawns(NOW)
        dropped = FloorItemCreate(
            id=uuid4(),
            room_id=camp,
            item_id="bread",
            quantity=1,
            source="drop",
            expires_at=NOW,
        )
        with pytest.raises(InconsistentChangesError):
            await repo.apply_changes(StateChanges(floor_items_create=[dropped]))
        other = dropped.model_copy(update={"source": "spawn", "item_id": "goblin_ear"})
        with pytest.raises(InconsistentChangesError):
            await repo.apply_changes(StateChanges(floor_items_create=[other]))
        await db.rollback()


# --- 3. floor cleanup ---


async def test_expired_floor_items_are_deleted(
    harness: ServiceHarness, ticker: WorldTicker
) -> None:
    h = harness
    room = h.scratch_room()
    watcher = await player(h, room)
    expired = FloorItem(
        room_id=room, item_id="goblin_ear", quantity=2, source="drop", expires_at=NOW
    )
    fresh = FloorItem(
        room_id=room,
        item_id="goblin_ear",
        quantity=1,
        source="loot",
        expires_at=NOW + timedelta(seconds=1),
    )
    spawned = FloorItem(room_id=room, item_id="bread", quantity=1, source="spawn")
    await add(h.committed, expired, fresh, spawned)

    await ticker.tick()

    assert await load(h.committed, FloorItem, expired.id) is None
    assert await load(h.committed, FloorItem, fresh.id) is not None
    assert await load(h.committed, FloorItem, spawned.id) is not None
    assert await logs(h.committed, watcher.id) == ["2x Goblinohr zerfällt zu Staub."]


# --- 4. idle ambushes ---


async def test_idle_player_is_ambushed(
    harness: ServiceHarness, ticker: WorldTicker, ambusher: str
) -> None:
    h = harness
    room = h.scratch_room()
    victim = await player(h, room, name=f"Opfer{uuid4().hex[:4]}")
    bystander = await player(h, room, state="in_combat")
    monster = MonsterInstance(monster_id=ambusher, room_id=room, hp=12)
    await add(h.committed, monster)

    report = await ticker.tick()

    assert report.ambushes == 1
    row = await load(h.committed, PlayerRow, victim.id)
    assert (row.state, row.combat_target_id) == ("in_combat", monster.id)
    assert row.hp < 20  # the monster strikes first
    assert await load(h.committed, CombatParticipation, (monster.id, victim.id)) is not None
    assert (await load(h.committed, MonsterInstance, monster.id)).tagged_by_party == (
        f"solo:{victim.id}"
    )
    victim_logs = await logs(h.committed, victim.id)
    assert victim_logs[0] == "Lauerer überfällt dich!"
    assert f"Lauerer überfällt {victim.name}!" in await logs(h.committed, bystander.id)
    # no actor in a ticker result: the victim is told via its player channel
    assert all(e.audience.kind != "actor" for e in h.publisher.published)


async def test_no_ambush_against_offline_fighting_or_safe_players(
    harness: ServiceHarness, ticker: WorldTicker, ambusher: str
) -> None:
    h = harness
    room = h.scratch_room()
    offline = await player(h, room, online=False)
    fighting = await player(h, room, state="in_combat")
    town = h.scratch_room("town_square")  # safe
    safe = await player(h, town)
    await add(
        h.committed,
        MonsterInstance(monster_id=ambusher, room_id=room, hp=12),
        MonsterInstance(monster_id=ambusher, room_id=town, hp=12),
    )

    report = await ticker.tick()

    assert report.ambushes == 0
    for p in (offline, safe):
        assert (await load(h.committed, PlayerRow, p.id)).state == "exploring"
    assert (await load(h.committed, PlayerRow, fighting.id)).combat_target_id is None


async def test_a_monster_ambushes_only_one_player_at_a_time(
    harness: ServiceHarness, ticker: WorldTicker, ambusher: str
) -> None:
    h = harness
    room = h.scratch_room()
    first, second = await player(h, room), await player(h, room)
    await add(h.committed, MonsterInstance(monster_id=ambusher, room_id=room, hp=12))

    report = await ticker.tick()

    states = {(await load(h.committed, PlayerRow, p.id)).state for p in (first, second)}
    assert report.ambushes == 1
    assert states == {"in_combat", "exploring"}


# --- 5. expired invites and trade requests ---


async def test_expired_invites_and_trade_requests_are_deleted(
    harness: ServiceHarness, ticker: WorldTicker
) -> None:
    h = harness
    room = h.scratch_room("town_square")
    anna, ben = await player(h, room), await player(h, room)
    old_invite = GroupInvite(inviter_id=anna.id, player_id=ben.id, expires_at=NOW)
    new_invite = GroupInvite(
        inviter_id=ben.id, player_id=anna.id, expires_at=NOW + timedelta(seconds=1)
    )

    def trade(**fields: Any) -> Trade:
        return Trade(initiator_id=anna.id, partner_id=ben.id, room_id=room, **fields)

    old_request = trade(expires_at=NOW)
    new_request = trade(expires_at=NOW + timedelta(seconds=1))
    open_trade = trade(status="open", expires_at=NOW - timedelta(minutes=5))
    await add(h.committed, old_invite, new_invite, old_request, new_request, open_trade)

    report = await ticker.tick()

    assert report.invites_expired >= 1 and report.trade_requests_expired >= 1
    assert await load(h.committed, GroupInvite, (anna.id, ben.id)) is None
    assert await load(h.committed, GroupInvite, (ben.id, anna.id)) is not None
    assert await load(h.committed, Trade, old_request.id) is None
    assert await load(h.committed, Trade, new_request.id) is not None
    # an open trade is not a request: its expires_at does not matter
    assert await load(h.committed, Trade, open_trade.id) is not None


# --- robustness ---


async def test_a_failing_step_does_not_stop_the_others(
    harness: ServiceHarness, ticker: WorldTicker, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    room = h.scratch_room()
    expired = FloorItem(room_id=room, item_id="goblin_ear", source="drop", expires_at=NOW)
    await add(h.committed, expired)

    async def broken(now: object, report: TickReport) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(ticker, "_respawn_monsters", broken)
    report = await ticker.tick()

    assert report.failed_steps == ["monster_respawns"]
    assert await load(h.committed, FloorItem, expired.id) is None


async def test_publish_failure_keeps_the_committed_tick(
    harness: ServiceHarness, ticker: WorldTicker
) -> None:
    h = harness
    room = h.scratch_room()
    due = MonsterInstance(monster_id="goblin", room_id=room, hp=0, alive=False, respawn_at=NOW)
    await add(h.committed, due)

    class Broken(RecordingPublisher):
        async def publish(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("bus down")

    ticker.publisher = Broken()
    report = await ticker.tick()

    assert report.failed_steps == []
    assert (await load(h.committed, MonsterInstance, due.id)).alive is True


# --- leadership ---


@pytest.fixture
async def leaders(engine: AsyncEngine) -> AsyncIterator[list[TickerLeadership]]:
    key = f"test-ticker-{uuid4().hex}"
    created = [TickerLeadership(engine, key) for _ in range(2)]
    yield created
    for leader in created:
        await leader.release()


async def test_only_one_process_leads(leaders: list[TickerLeadership]) -> None:
    first, second = leaders

    assert await first.ensure() is True
    assert await second.ensure() is False
    assert await first.ensure() is True  # still holding it

    await first.release()
    assert await second.ensure() is True
    assert await first.ensure() is False


async def test_lost_leader_connection_frees_the_lock(leaders: list[TickerLeadership]) -> None:
    first, second = leaders
    assert await first.ensure()
    connection = first._connection
    assert connection is not None
    await connection.invalidate()  # e.g. the database dropped the connection

    assert await second.ensure() is True
    assert await first.ensure() is False


async def test_run_ticks_until_cancelled_and_releases_the_lock(
    harness: ServiceHarness, engine: AsyncEngine
) -> None:
    h = harness
    room = h.scratch_room()
    due = MonsterInstance(monster_id="goblin", room_id=room, hp=0, alive=False, respawn_at=NOW)
    await add(h.committed, due)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    ticker = WorldTicker(
        engine, sessions, h.content, h.publisher, clock=Clock(), interval_seconds=0.02
    )

    task = asyncio.create_task(ticker.run())
    try:
        for _ in range(250):
            if (await load(h.committed, MonsterInstance, due.id)).alive:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("the ticker never respawned the monster")
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    successor = TickerLeadership(engine)
    try:
        assert await successor.ensure() is True
    finally:
        await successor.release()
