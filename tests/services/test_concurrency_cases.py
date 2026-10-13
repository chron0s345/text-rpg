"""Concurrency cases from ARCHITECTURE.md ("Tests", Nebenläufigkeit) not covered elsewhere:

- group loot while a member leaves the group,
- an ambush while the victim walks out of the room,
- an offer that changes between the two confirmations of a trade.

Each case runs in both orders. ``in_order`` makes the order deterministic while keeping the
operations truly concurrent: a third transaction holds the contested row, the first operation
queues up for it, then the second one starts, then the row is released. PostgreSQL grants row
locks in queue order, so the outcome must be exactly the serial one, never a mix of both.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterator
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlmodel import col

from app.api.schemas import (
    AttackAction,
    GroupAcceptAction,
    GroupInviteAction,
    GroupLeaveAction,
    MoveAction,
    TradeAcceptAction,
    TradeConfirmAction,
    TradeOfferGoldAction,
    TradeRequestAction,
)
from app.content.schemas import Ambush, LootEntry
from app.db.models import (
    CombatParticipation,
    FloorItem,
    Group,
    InventoryItem,
    MonsterInstance,
    Trade,
)
from app.db.models import Player as PlayerRow
from tests.db.factories import add_player
from tests.services.conftest import ServiceHarness

Order = Literal["first", "second"]
ORDERS = pytest.mark.parametrize("order", ["first", "second"])


async def player(h: ServiceHarness, room: str, **fields: Any) -> PlayerRow:
    h.committed.rooms.append(room)
    async with h.committed.session() as db, db.begin():
        row = await add_player(db, current_room_id=room, **fields)
        h.committed.accounts.append(row.account_id)
    return row


async def load(h: ServiceHarness, model: type[Any], key: object) -> Any:
    async with h.committed.session() as db:
        return await db.get(model, key)


async def act(h: ServiceHarness, player_id: UUID, action: Any) -> None:
    h.clock.advance(seconds=1)  # stay clear of the rate limit
    await h.service.execute(player_id, action)


async def lock_waiters(h: ServiceHarness) -> int:
    """How many connections wait for a row lock. A second waiter for the same row waits for the
    first one (queue), not for the holder, so this counts all waiters (tests run one at a time)."""
    async with h.committed.engine.connect() as probe:
        count = await probe.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
        )
    return int(count or 0)


async def in_order(
    h: ServiceHarness,
    contested: type[Any],
    key: object,
    first: Callable[[], Coroutine[Any, Any, Any]],
    second: Callable[[], Coroutine[Any, Any, Any]],
) -> None:
    """Runs ``first`` and ``second`` concurrently; ``first`` gets the contested row first.

    ``second`` may also finish without waiting (e.g. rejected because the same character is
    already busy); that is a serial outcome too.
    """
    async with h.committed.session() as blocker, blocker.begin():
        await blocker.execute(
            select(contested)
            .where(*(c == v for c, v in zip(contested.__table__.primary_key, [key], strict=True)))
            .with_for_update()
        )
        one = asyncio.create_task(first())
        for _ in range(200):
            if await lock_waiters(h) >= 1:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("the first operation never waited for the contested row")
        two = asyncio.create_task(second())
        for _ in range(200):
            if two.done() or await lock_waiters(h) >= 2:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("the second operation neither waited nor finished")
    await asyncio.gather(one, two)


async def add_monster(h: ServiceHarness, monster_id: str, room: str, hp: int) -> MonsterInstance:
    async with h.committed.session() as db, db.begin():
        monster = MonsterInstance(monster_id=monster_id, room_id=room, hp=hp)
        db.add(monster)
    return monster


# --- group loot while a member leaves ---


@pytest.fixture
def hoard(harness: ServiceHarness) -> Iterator[str]:
    """A monster with fixed rewards: 6 gold, 3 goblin ears (removed afterwards: shared content)."""
    monster_id = f"hoard-{uuid4().hex[:6]}"
    harness.content.monsters[monster_id] = harness.content.get_monster("goblin").model_copy(
        update={
            "id": monster_id,
            "gold_reward": (6, 6),
            "loot_table": [LootEntry(item_id="goblin_ear", chance=1.0, min_qty=3, max_qty=3)],
            "ambush": None,
        }
    )
    try:
        yield monster_id
    finally:
        del harness.content.monsters[monster_id]


@ORDERS
async def test_group_loot_while_a_member_leaves(
    harness: ServiceHarness, hoard: str, order: Order
) -> None:
    h = harness
    room = h.scratch_room()
    anna = await player(h, room, base_attack=50)
    ben, cleo = await player(h, room), await player(h, room)
    for member in (ben, cleo):
        await act(h, anna.id, GroupInviteAction(player_name=member.name))
        await act(h, member.id, GroupAcceptAction(inviter_id=anna.id))
    group_id = (await load(h, PlayerRow, anna.id)).group_id
    assert group_id is not None
    monster = await add_monster(h, hoard, room, hp=1)
    xp_reward = h.content.get_monster(hoard).xp_reward

    h.clock.advance(seconds=1)

    def kill() -> Coroutine[Any, Any, Any]:
        return h.service.execute(anna.id, AttackAction(target_id=monster.id))

    def leave() -> Coroutine[Any, Any, Any]:
        return h.service.execute(ben.id, GroupLeaveAction())

    await in_order(h, Group, group_id, *((kill, leave) if order == "first" else (leave, kill)))

    rows = {p.id: await load(h, PlayerRow, p.id) for p in (anna, ben, cleo)}
    assert (await load(h, MonsterInstance, monster.id)).alive is False
    # Ben left either before or after the kill, but he did leave, and the group lives on
    assert rows[ben.id].group_id is None
    assert {rows[anna.id].group_id, rows[cleo.id].group_id} == {group_id}
    assert await load(h, Group, group_id) is not None
    # nothing is created or lost on the way: rewards add up exactly once
    assert sum(r.gold for r in rows.values()) == 6
    assert sum(r.xp for r in rows.values()) == xp_reward
    async with h.committed.session() as db:
        ears = (
            await db.scalars(
                select(InventoryItem).where(
                    col(InventoryItem.player_id).in_(list(rows)),
                    col(InventoryItem.item_id) == "goblin_ear",
                )
            )
        ).all()
        on_floor = (await db.scalars(select(FloorItem).where(col(FloorItem.room_id) == room))).all()
        participations = (
            await db.scalars(
                select(CombatParticipation).where(
                    col(CombatParticipation.monster_instance_id) == monster.id
                )
            )
        ).all()
    assert sum(e.quantity for e in ears) + sum(f.quantity for f in on_floor) == 3
    assert participations == []
    assert {r.state for r in rows.values()} == {"exploring"}
    ben_share = rows[ben.id].gold + sum(e.quantity for e in ears if e.player_id == ben.id)
    if order == "first":
        # killed while Ben was still a member in the room: he shares in gold or items
        assert ben_share > 0 and rows[ben.id].xp > 0
    else:
        # Ben had left: the rewards went to the remaining group only
        assert (ben_share, rows[ben.id].xp) == (0, 0)


# --- ambush while walking out of the room ---


@pytest.fixture
def lurker(harness: ServiceHarness) -> Iterator[str]:
    monster_id = f"lurker-{uuid4().hex[:6]}"
    harness.content.monsters[monster_id] = harness.content.get_monster("goblin").model_copy(
        update={
            "id": monster_id,
            "ambush": Ambush(chance_on_enter=0, chance_per_tick=1, max_level_difference=None),
        }
    )
    try:
        yield monster_id
    finally:
        del harness.content.monsters[monster_id]


@ORDERS
async def test_ambush_while_the_victim_leaves_the_room(
    harness: ServiceHarness, lurker: str, order: Order
) -> None:
    """Both run for the same character, so the second one does not even wait: it finds the
    character busy (per-player claim) and is refused. The ticker rolls again next tick."""
    h = harness
    gate, forest = h.scratch_path()  # forest -> north -> gate
    victim = await player(h, forest)
    monster = await add_monster(h, lurker, forest, hp=12)

    h.clock.advance(seconds=1)

    def ambush() -> Coroutine[Any, Any, Any]:
        return h.service.idle_ambush(victim.id)

    def walk() -> Coroutine[Any, Any, Any]:
        return h.service.execute(victim.id, MoveAction(direction="north"))

    await in_order(
        h, PlayerRow, victim.id, *((ambush, walk) if order == "first" else (walk, ambush))
    )

    row = await load(h, PlayerRow, victim.id)
    participation = await load(h, CombatParticipation, (monster.id, victim.id))
    lurking = await load(h, MonsterInstance, monster.id)
    if order == "first":
        assert row.state == "in_combat"
        # ambushed first: the move was refused, the fight is where the monster is
        assert row.current_room_id == forest
        assert row.combat_target_id == monster.id
        assert participation is not None
        assert lurking.tagged_by_party == f"solo:{victim.id}"
    else:
        # walked out first: nobody ambushes a player who is not in the room any more
        assert (row.state, row.current_room_id, row.combat_target_id) == ("exploring", gate, None)
        assert participation is None
        assert lurking.tagged_by_party is None


# --- offer changes between the two confirmations ---


@ORDERS
async def test_offer_changes_between_the_confirmations(
    harness: ServiceHarness, order: Order
) -> None:
    h = harness
    room = h.scratch_room("town_square")
    anna, ben = await player(h, room, gold=50), await player(h, room, gold=10)
    await act(h, anna.id, TradeRequestAction(target_player_id=ben.id))
    async with h.committed.session() as db:
        trade = (await db.scalars(select(Trade).where(col(Trade.initiator_id) == anna.id))).one()
    await act(h, ben.id, TradeAcceptAction(trade_id=trade.id))
    await act(h, anna.id, TradeOfferGoldAction(trade_id=trade.id, amount=20))
    revision = (await load(h, Trade, trade.id)).revision
    await act(h, anna.id, TradeConfirmAction(trade_id=trade.id, revision=revision))

    # Ben confirms what he saw while Anna lowers her offer at the same moment
    h.clock.advance(seconds=1)

    def confirm() -> Coroutine[Any, Any, Any]:
        return h.service.execute(ben.id, TradeConfirmAction(trade_id=trade.id, revision=revision))

    def change() -> Coroutine[Any, Any, Any]:
        return h.service.execute(anna.id, TradeOfferGoldAction(trade_id=trade.id, amount=1))

    await in_order(
        h, Trade, trade.id, *((confirm, change) if order == "first" else (change, confirm))
    )

    final = await load(h, Trade, trade.id)
    gold = ((await load(h, PlayerRow, anna.id)).gold, (await load(h, PlayerRow, ben.id)).gold)
    if order == "first":
        assert final.status == "completed"
        # Ben's confirmation came first: exactly the terms both confirmed, the late change lost
        assert gold == (30, 30)
    else:
        # the change came first: Ben's confirmation was for an old revision and does not count
        assert final.status == "open"
        assert final.revision > revision
        assert (final.initiator_confirmed, final.partner_confirmed) == (False, False)
        assert final.initiator_gold == 1
        assert gold == (50, 10)
