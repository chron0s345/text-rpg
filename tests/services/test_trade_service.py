"""Trading against PostgreSQL: the whole flow, revisions between confirmations, automatic
cancelling, and the concurrency cases (two requests at once, dropping an item while the trade
executes)."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlmodel import col

from app.api.schemas import (
    DropAction,
    MoveAction,
    TradeAcceptAction,
    TradeConfirmAction,
    TradeOfferGoldAction,
    TradeOfferItemAction,
    TradeRequestAction,
)
from app.db.models import FloorItem, InventoryItem, Trade, TradeLog, TradeOffer
from app.db.models import Player as PlayerRow
from app.services.session_service import SessionService
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


async def act(h: ServiceHarness, player_id: UUID, action: Any) -> list[str]:
    h.clock.advance(seconds=1)
    return [e.text for e in (await h.service.execute(player_id, action)).events]


async def trade_of(h: ServiceHarness, player_id: UUID) -> Trade:
    async with h.committed.session() as db:
        trade = (
            await db.scalars(
                select(Trade)
                .where(
                    (col(Trade.initiator_id) == player_id) | (col(Trade.partner_id) == player_id)
                )
                .order_by(col(Trade.created_at).desc())
            )
        ).first()
        assert trade is not None
        return trade


async def items_of(h: ServiceHarness, player_id: UUID) -> list[tuple[str, int]]:
    async with h.committed.session() as db:
        rows = (
            await db.scalars(select(InventoryItem).where(col(InventoryItem.player_id) == player_id))
        ).all()
        return sorted((r.item_id, r.quantity) for r in rows)


async def opened(h: ServiceHarness) -> tuple[PlayerRow, PlayerRow, UUID]:
    room = h.scratch_room("town_square")
    anna, ben = await player(h, room, gold=50), await player(h, room, gold=10)
    await act(h, anna.id, TradeRequestAction(target_player_id=ben.id))
    trade = await trade_of(h, anna.id)
    await act(h, ben.id, TradeAcceptAction(trade_id=trade.id))
    return anna, ben, trade.id


async def test_full_trade(harness: ServiceHarness) -> None:
    h = harness
    anna, ben, trade_id = await opened(h)
    potions = await give(h, anna.id, "healing_potion", 4)
    dagger = await give(h, ben.id, "rusty_dagger")

    await act(
        h, anna.id, TradeOfferItemAction(trade_id=trade_id, inventory_item_id=potions, quantity=3)
    )
    await act(h, anna.id, TradeOfferGoldAction(trade_id=trade_id, amount=5))
    await act(
        h, ben.id, TradeOfferItemAction(trade_id=trade_id, inventory_item_id=dagger, quantity=1)
    )
    # offered items are locked
    assert await act(h, anna.id, DropAction(inventory_item_id=potions)) == [
        "Dieser Gegenstand ist im Handel angeboten."
    ]
    revision = (await trade_of(h, anna.id)).revision
    await act(h, anna.id, TradeConfirmAction(trade_id=trade_id, revision=revision))
    texts = await act(h, ben.id, TradeConfirmAction(trade_id=trade_id, revision=revision))

    assert texts == [
        f"Handel mit {anna.name} abgeschlossen. Du erhältst 3x Heiltrank, 5 Gold "
        "und gibst Rostiger Dolch."
    ]
    assert await items_of(h, anna.id) == [("healing_potion", 1), ("rusty_dagger", 1)]
    assert await items_of(h, ben.id) == [("healing_potion", 3)]
    async with h.committed.session() as db:
        a, b = await db.get(PlayerRow, anna.id), await db.get(PlayerRow, ben.id)
        assert a is not None and b is not None
        assert (a.gold, b.gold) == (45, 15)
        assert (await db.get(Trade, trade_id)).status == "completed"  # type: ignore[union-attr]
        assert (
            await db.scalars(select(TradeOffer).where(col(TradeOffer.trade_id) == trade_id))
        ).all() == []
        [log] = (await db.scalars(select(TradeLog).where(col(TradeLog.trade_id) == trade_id))).all()
        assert log.summary["partner"]["items"] == [{"item_id": "rusty_dagger", "quantity": 1}]


async def test_changed_offer_needs_new_confirmations(harness: ServiceHarness) -> None:
    h = harness
    anna, ben, trade_id = await opened(h)
    bread = await give(h, ben.id, "bread", 2)

    await act(h, anna.id, TradeConfirmAction(trade_id=trade_id, revision=0))
    # Ben swaps the offer after Anna confirmed: her confirmation is gone
    await act(
        h, ben.id, TradeOfferItemAction(trade_id=trade_id, inventory_item_id=bread, quantity=1)
    )
    trade = await trade_of(h, anna.id)
    assert (trade.revision, trade.initiator_confirmed) == (1, False)
    await act(h, ben.id, TradeConfirmAction(trade_id=trade_id, revision=1))
    assert (await trade_of(h, anna.id)).status == "open"  # not executed without Anna
    assert await act(h, anna.id, TradeConfirmAction(trade_id=trade_id, revision=0)) == [
        "Das Angebot hat sich geändert. Prüfe es und bestätige erneut."
    ]
    await act(h, anna.id, TradeConfirmAction(trade_id=trade_id, revision=1))
    assert (await trade_of(h, anna.id)).status == "completed"
    assert await items_of(h, anna.id) == [("bread", 1)]


async def test_walking_away_cancels_and_unlocks(harness: ServiceHarness) -> None:
    h = harness
    anna, ben, trade_id = await opened(h)
    potion = await give(h, anna.id, "healing_potion")
    await act(
        h, anna.id, TradeOfferItemAction(trade_id=trade_id, inventory_item_id=potion, quantity=1)
    )
    gate = h.scratch_room("town_bank")
    h.content.rooms[(await trade_of(h, anna.id)).room_id] = h.content.rooms[
        (await trade_of(h, anna.id)).room_id
    ].model_copy(update={"exits": {"north": gate}})

    await act(h, ben.id, MoveAction(direction="north"))

    assert (await trade_of(h, anna.id)).status == "cancelled"
    async with h.committed.session() as db:
        assert (
            await db.scalars(select(TradeOffer).where(col(TradeOffer.trade_id) == trade_id))
        ).all() == []
    # the potion is free again
    assert await act(h, anna.id, DropAction(inventory_item_id=potion)) == [
        "Du lässt Heiltrank fallen."
    ]


async def test_lost_connection_cancels(harness: ServiceHarness) -> None:
    h = harness
    anna, ben, trade_id = await opened(h)
    presence = SessionService(h.service.sessions, h.service, clock=h.clock)
    await presence.disconnected(ben.id)
    assert (await trade_of(h, anna.id)).status == "cancelled"


async def test_two_requests_for_the_same_player(harness: ServiceHarness) -> None:
    h = harness
    room = h.scratch_room("town_square")
    anna, cleo, ben = await player(h, room), await player(h, room), await player(h, room)
    h.clock.advance(seconds=1)
    results = await asyncio.gather(
        h.service.execute(anna.id, TradeRequestAction(target_player_id=ben.id)),
        h.service.execute(cleo.id, TradeRequestAction(target_player_id=ben.id)),
    )
    texts = sorted(e.text for r in results for e in r.events)
    assert sum(t.startswith("Du bittest") for t in texts) == 1
    assert sum(t.endswith("handelt gerade mit jemand anderem.") for t in texts) == 1
    async with h.committed.session() as db:
        active = (
            await db.scalars(
                select(Trade).where(
                    col(Trade.partner_id) == ben.id, col(Trade.status) == "requested"
                )
            )
        ).all()
    assert len(active) == 1


async def test_item_dropped_while_the_trade_executes(harness: ServiceHarness) -> None:
    h = harness
    anna, ben, trade_id = await opened(h)
    potion = await give(h, anna.id, "healing_potion")
    await act(
        h, anna.id, TradeOfferItemAction(trade_id=trade_id, inventory_item_id=potion, quantity=1)
    )
    revision = (await trade_of(h, anna.id)).revision
    await act(h, anna.id, TradeConfirmAction(trade_id=trade_id, revision=revision))

    h.clock.advance(seconds=1)
    await asyncio.gather(
        h.service.execute(anna.id, DropAction(inventory_item_id=potion)),
        h.service.execute(ben.id, TradeConfirmAction(trade_id=trade_id, revision=revision)),
    )
    potions = [
        quantity
        for owner in (anna.id, ben.id)
        for item_id, quantity in await items_of(h, owner)
        if item_id == "healing_potion"
    ]
    async with h.committed.session() as db:
        on_floor = (
            await db.scalars(select(FloorItem).where(col(FloorItem.item_id) == "healing_potion"))
        ).all()
    # exactly one potion: with Anna (trade not done yet) or with Ben, never dropped as well
    assert potions == [1]
    assert on_floor == []
