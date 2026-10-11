"""Merchant and bank actions against PostgreSQL: persistence, bank survives death, and an item
sold and deposited at the same time ends up in exactly one place."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlmodel import col

from app.api.schemas import (
    BankDepositAction,
    BankWithdrawAction,
    BuyAction,
    LogoutAction,
    SellAction,
)
from app.db.models import BankItem, CombatParticipation, InventoryItem, MonsterInstance
from app.db.models import Player as PlayerRow
from tests.db.factories import add_player
from tests.services.conftest import ServiceHarness


async def player(h: ServiceHarness, room: str, **fields: Any) -> PlayerRow:
    h.committed.rooms.append(room)
    async with h.committed.session() as db, db.begin():
        row = await add_player(db, current_room_id=room, **fields)
        h.committed.accounts.append(row.account_id)
    return row


async def give(h: ServiceHarness, player_id: UUID, item_id: str, quantity: int = 1) -> UUID:
    async with h.committed.session() as db, db.begin():
        item = InventoryItem(player_id=player_id, item_id=item_id, quantity=quantity)
        db.add(item)
    return item.id


async def holdings(
    h: ServiceHarness, player_id: UUID
) -> tuple[int, list[tuple[str, int]], list[tuple[str, int]]]:
    async with h.committed.session() as db:
        row = await db.get(PlayerRow, player_id)
        assert row is not None
        inventory = (
            await db.scalars(select(InventoryItem).where(col(InventoryItem.player_id) == player_id))
        ).all()
        bank = (
            await db.scalars(select(BankItem).where(col(BankItem.player_id) == player_id))
        ).all()
    return (
        row.gold,
        sorted((i.item_id, i.quantity) for i in inventory),
        sorted((b.item_id, b.quantity) for b in bank),
    )


async def test_buy_and_sell_persist(harness: ServiceHarness) -> None:
    h = harness
    market = h.scratch_room("town_market")
    h.content.merchants["alchemist-" + market] = h.content.merchants["alchemist"].model_copy(
        update={"id": "alchemist-" + market, "room_id": market}
    )
    merchant = "alchemist-" + market
    try:
        actor = await player(h, market, gold=50)
        await h.service.execute(
            actor.id, BuyAction(merchant_id=merchant, item_id="healing_potion", quantity=2)
        )
        assert await holdings(h, actor.id) == (20, [("healing_potion", 2)], [])

        async with h.committed.session() as db:
            stack_id = (
                await db.scalars(
                    select(col(InventoryItem.id)).where(col(InventoryItem.player_id) == actor.id)
                )
            ).one()
        h.clock.advance(seconds=1)
        await h.service.execute(
            actor.id, SellAction(merchant_id=merchant, inventory_item_id=stack_id)
        )
        assert await holdings(h, actor.id) == (23, [("healing_potion", 1)], [])  # floor(15 * .25)
    finally:
        del h.content.merchants[merchant]


async def test_bank_round_trip_and_survives_death(harness: ServiceHarness) -> None:
    h = harness
    vault = h.scratch_room("town_bank")
    actor = await player(h, vault)
    ears = await give(h, actor.id, "goblin_ear", 5)

    await h.service.execute(actor.id, BankDepositAction(inventory_item_id=ears, quantity=5))
    assert await holdings(h, actor.id) == (0, [], [("goblin_ear", 5)])

    async with h.committed.session() as db:
        bank_id = (
            await db.scalars(select(col(BankItem.id)).where(col(BankItem.player_id) == actor.id))
        ).one()
    h.clock.advance(seconds=1)
    await h.service.execute(actor.id, BankWithdrawAction(bank_item_id=bank_id, quantity=2))
    assert await holdings(h, actor.id) == (0, [("goblin_ear", 2)], [("goblin_ear", 3)])

    # dying (logout in combat) never touches the bank
    forest = h.scratch_room()
    async with h.committed.session() as db, db.begin():
        monster = MonsterInstance(monster_id="goblin", room_id=forest, hp=10)
        db.add(monster)
        await db.flush()
        row = await db.get(PlayerRow, actor.id)
        assert row is not None
        row.current_room_id, row.state, row.combat_target_id = forest, "in_combat", monster.id
        db.add(
            CombatParticipation(
                monster_instance_id=monster.id, player_id=actor.id, party_key=f"solo:{actor.id}"
            )
        )
    h.clock.advance(seconds=1)
    await h.service.execute(actor.id, LogoutAction(confirm_death=True))
    assert (await holdings(h, actor.id))[2] == [("goblin_ear", 3)]


async def test_item_sold_and_deposited_at_once(harness: ServiceHarness) -> None:
    """A room with both a bank and a merchant; both actions race for the same stack."""
    h = harness
    room = h.scratch_room("town_bank")
    merchant = "alchemist-" + room
    h.content.merchants[merchant] = h.content.merchants["alchemist"].model_copy(
        update={"id": merchant, "room_id": room}
    )
    try:
        actor = await player(h, room)
        dagger = await give(h, actor.id, "rusty_dagger")
        await asyncio.gather(
            h.service.execute(actor.id, SellAction(merchant_id=merchant, inventory_item_id=dagger)),
            h.service.execute(actor.id, BankDepositAction(inventory_item_id=dagger)),
        )
        gold, inventory, bank = await holdings(h, actor.id)
        sold, deposited = gold == 2, bank == [("rusty_dagger", 1)]
        assert sold != deposited  # exactly one of them happened
        assert inventory == []
    finally:
        del h.content.merchants[merchant]
