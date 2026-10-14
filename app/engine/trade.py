"""Trading between players (ARCHITECTURE.md, "Handel").

request -> accept (window opens) -> offers (items, gold) -> both confirm the current revision ->
executed in the same transaction. Every change of an offer raises ``revision`` and resets both
confirmations, so nobody can swap the offer at the last moment. If a check fails on execution,
nothing moves and both confirmations are reset.

Offered items stay in their owner's inventory until the execution (they are locked against
dropping, using, equipping, selling and banking). Items change hands as new inventory rows of
the receiver (a row never changes its owner).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.events import (
    ActionResult,
    ResultBuilder,
    TradeCreate,
    TradeLogCreate,
    TradeOfferDelete,
    TradeOfferUpsert,
    TradeUpdate,
    rejected,
)
from app.engine.inventory import add_item_to_inventory, calculate_inventory_capacity
from app.engine.inventory_actions import NOT_OWNED, remove_quantity
from app.engine.permissions import check_state
from app.engine.state import (
    InventoryItemState,
    PlayerSnapshot,
    TradeActionSnapshot,
    TradeSnapshot,
    new_id,
)

REQUESTED: Final = "trade.requested"
OPENED: Final = "trade.opened"
CHANGED: Final = "trade.changed"
CONFIRMED: Final = "trade.confirmed"
COMPLETED: Final = "trade.completed"
CANCELLED: Final = "trade.cancelled"
NO_TRADE = "Diesen Handel gibt es nicht."


def _available(player: PlayerSnapshot | None, room_id: str) -> bool:
    """Online with a live connection, in the trade's room, not fighting or dead."""
    return (
        player is not None
        and player.online
        and not player.disconnected
        and player.state == "exploring"
        and player.current_room_id == room_id
    )


def _label(quantity: int, name: str) -> str:
    return f"{quantity}x {name}" if quantity > 1 else name


# --- request & accept --------------------------------------------------------------------------


def resolve_trade_request(
    snapshot: TradeActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    target_player_id: UUID,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("trade_request", actor):
        return denied
    if target_player_id == actor.id:
        return rejected("Mit dir selbst kannst du nicht handeln.")
    target = snapshot.player(target_player_id)
    if target is None or not target.online or target.current_room_id != actor.current_room_id:
        return rejected("Diese Person ist nicht hier.")
    if target.account_id == actor.account_id:
        return rejected("Deine eigenen Charaktere können nicht miteinander handeln.")
    if not _available(target, actor.current_room_id):
        return rejected(f"{target.name} ist gerade beschäftigt.")
    if snapshot.active_trades_of(actor.id):
        return rejected("Du handelst bereits.")
    if snapshot.active_trades_of(target.id):
        return rejected(f"{target.name} handelt gerade mit jemand anderem.")

    builder = ResultBuilder(actor_id=actor.id)
    trade_id = new_id(rng)
    builder.changes.trades_create.append(
        TradeCreate(
            id=trade_id,
            initiator_id=actor.id,
            partner_id=target.id,
            room_id=actor.current_room_id,
            expires_at=now + timedelta(seconds=rules.trade.request_ttl_seconds),
            created_at=now,
        )
    )
    builder.to_actor(
        REQUESTED, f"Du bittest {target.name} um einen Handel.", trade_id=str(trade_id)
    )
    builder.to_player(
        target.id, REQUESTED, f"{actor.name} möchte mit dir handeln.", trade_id=str(trade_id)
    )
    return builder.build()


def resolve_trade_accept(
    snapshot: TradeActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    trade_id: UUID,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("trade_accept", actor):
        return denied
    trade = snapshot.trade(trade_id)
    if trade is None or not trade.involves(actor.id) or trade.status != "requested":
        return rejected(NO_TRADE)
    initiator = snapshot.player(trade.initiator_id)
    if actor.id == trade.initiator_id:
        partner = snapshot.player(trade.partner_id)
        return rejected(f"Warte, bis {partner.name if partner else 'die andere Seite'} annimmt.")
    if trade.expires_at <= now:
        return rejected("Die Anfrage ist abgelaufen.")
    if not _available(initiator, trade.room_id) or not _available(actor, trade.room_id):
        return rejected("Ihr müsst beide hier und frei sein, um zu handeln.")
    assert initiator is not None

    builder = ResultBuilder(actor_id=actor.id)
    builder.changes.trades.append(TradeUpdate(trade_id=trade.id, status="open", revision=0))
    builder.to_actor(OPENED, f"Du handelst mit {initiator.name}.", trade_id=str(trade.id))
    builder.to_player(
        initiator.id, OPENED, f"{actor.name} nimmt deinen Handel an.", trade_id=str(trade.id)
    )
    return builder.build()


# --- offers ------------------------------------------------------------------------------------


def _open_trade(snapshot: TradeActionSnapshot, trade_id: UUID) -> TradeSnapshot | str:
    trade = snapshot.trade(trade_id)
    if trade is None or not trade.involves(snapshot.actor.id):
        return NO_TRADE
    if trade.status != "open":
        return "Der Handel ist nicht offen."
    return trade


def _offer_changed(
    builder: ResultBuilder,
    snapshot: TradeActionSnapshot,
    trade: TradeSnapshot,
    text: str,
    **fields: Any,
) -> None:
    """Any change: new revision, both confirmations reset, the other side is told."""
    trade.revision += 1
    trade.initiator_confirmed = trade.partner_confirmed = False
    builder.changes.trades.append(
        TradeUpdate.model_validate(
            {
                "trade_id": trade.id,
                "revision": trade.revision,
                "initiator_confirmed": False,
                "partner_confirmed": False,
                **fields,
            }
        )
    )
    actor = snapshot.actor
    builder.to_player(
        trade.other(actor.id),
        CHANGED,
        f"{actor.name} ändert das Angebot: {text}",
        trade_id=str(trade.id),
        revision=trade.revision,
    )
    builder.to_actor(CHANGED, f"Dein Angebot: {text}", trade_id=str(trade.id))


def resolve_trade_offer_item(
    snapshot: TradeActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    trade_id: UUID,
    inventory_item_id: UUID,
    quantity: int,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("trade_offer_item", actor):
        return denied
    trade = _open_trade(snapshot, trade_id)
    if isinstance(trade, str):
        return rejected(trade)
    entry = actor.item(inventory_item_id)
    if entry is None:
        return rejected(NOT_OWNED)
    if entry.equipped_slot is not None:
        return rejected("Lege den Gegenstand zuerst ab.")
    item = content.get_item(entry.item_id)
    if not item.tradable:
        return rejected(f"{item.name} lässt sich nicht handeln.")
    if quantity < 1 or quantity > entry.quantity:
        return rejected("So viele hast du nicht.")
    mine = trade.offers_of(actor.id)
    existing = next((o for o in mine if o.inventory_item_id == entry.id), None)
    if existing is None and len(mine) >= rules.trade.max_offer_entries:
        return rejected("Mehr passt nicht in dein Angebot.")

    builder = ResultBuilder(actor_id=actor.id)
    builder.changes.trade_offers_upsert.append(
        TradeOfferUpsert(
            trade_id=trade.id, player_id=actor.id, inventory_item_id=entry.id, quantity=quantity
        )
    )
    _offer_changed(builder, snapshot, trade, f"{_label(quantity, item.name)} angeboten.")
    return builder.build()


def resolve_trade_remove_item(
    snapshot: TradeActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    trade_id: UUID,
    inventory_item_id: UUID,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("trade_remove_item", actor):
        return denied
    trade = _open_trade(snapshot, trade_id)
    if isinstance(trade, str):
        return rejected(trade)
    offer = next(
        (o for o in trade.offers_of(actor.id) if o.inventory_item_id == inventory_item_id), None
    )
    if offer is None:
        return rejected("Das ist nicht in deinem Angebot.")
    entry = actor.item(inventory_item_id)
    name = content.get_item(entry.item_id).name if entry else "Gegenstand"

    builder = ResultBuilder(actor_id=actor.id)
    builder.changes.trade_offers_delete.append(
        TradeOfferDelete(trade_id=trade.id, inventory_item_id=inventory_item_id)
    )
    _offer_changed(builder, snapshot, trade, f"{name} zurückgenommen.")
    return builder.build()


def resolve_trade_offer_gold(
    snapshot: TradeActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    trade_id: UUID,
    amount: int,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("trade_offer_gold", actor):
        return denied
    trade = _open_trade(snapshot, trade_id)
    if isinstance(trade, str):
        return rejected(trade)
    if amount < 0 or amount > actor.gold:
        return rejected(f"Du hast nur {actor.gold} Gold.")
    if amount == trade.gold_of(actor.id):
        return rejected("Das bietest du bereits.")
    field = "initiator_gold" if actor.id == trade.initiator_id else "partner_gold"
    setattr(trade, field, amount)

    builder = ResultBuilder(actor_id=actor.id)
    _offer_changed(builder, snapshot, trade, f"{amount} Gold.", **{field: amount})
    return builder.build()


# --- confirm & execute -------------------------------------------------------------------------


def resolve_trade_confirm(
    snapshot: TradeActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    trade_id: UUID,
    revision: int,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("trade_confirm", actor):
        return denied
    trade = _open_trade(snapshot, trade_id)
    if isinstance(trade, str):
        return rejected(trade)
    if revision != trade.revision:
        return rejected("Das Angebot hat sich geändert. Prüfe es und bestätige erneut.")
    mine = "initiator_confirmed" if actor.id == trade.initiator_id else "partner_confirmed"
    theirs = "partner_confirmed" if mine == "initiator_confirmed" else "initiator_confirmed"
    if getattr(trade, mine):
        return rejected("Du hast bereits bestätigt.")
    if not trade.offers and trade.initiator_gold == 0 and trade.partner_gold == 0:
        return rejected("Es gibt noch nichts zu handeln. Legt zuerst etwas ins Angebot.")

    builder = ResultBuilder(actor_id=actor.id)
    if not getattr(trade, theirs):
        setattr(trade, mine, True)
        builder.changes.trades.append(
            TradeUpdate.model_validate({"trade_id": trade.id, mine: True})
        )
        builder.to_actor(CONFIRMED, "Du bestätigst den Handel.", trade_id=str(trade.id))
        builder.to_player(
            trade.other(actor.id),
            CONFIRMED,
            f"{actor.name} bestätigt den Handel.",
            trade_id=str(trade.id),
        )
        return builder.build()

    problem = _execute(builder, snapshot, trade, content, rules, now, rng)
    if problem is not None:
        # nothing moves; both have to look again
        builder = ResultBuilder(actor_id=actor.id)
        trade.initiator_confirmed = trade.partner_confirmed = False
        builder.changes.trades.append(
            TradeUpdate(trade_id=trade.id, initiator_confirmed=False, partner_confirmed=False)
        )
        text = f"Der Handel kann nicht ausgeführt werden: {problem}"
        builder.to_actor(CHANGED, text, trade_id=str(trade.id))
        builder.to_player(trade.other(actor.id), CHANGED, text, trade_id=str(trade.id))
    return builder.build()


def _execute(
    builder: ResultBuilder,
    snapshot: TradeActionSnapshot,
    trade: TradeSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
) -> str | None:
    """Both confirmed the current revision: check everything, then move items and gold.
    Returns the reason if it cannot be done (then ``builder`` must be discarded)."""
    sides = [snapshot.player(trade.initiator_id), snapshot.player(trade.partner_id)]
    if any(not _available(p, trade.room_id) for p in sides):
        return "Ihr müsst beide hier und frei sein."
    giver_a, giver_b = sides
    assert giver_a is not None and giver_b is not None

    # ownership, quantities, gold
    for player in (giver_a, giver_b):
        if trade.gold_of(player.id) > player.gold:
            return f"{player.name} hat nicht mehr so viel Gold."
        for offer in trade.offers_of(player.id):
            entry = player.item(offer.inventory_item_id)
            if entry is None or entry.quantity < offer.quantity or entry.equipped_slot is not None:
                return f"{player.name} hat ein angebotenes Item nicht mehr."

    # capacity on both sides, simulated on copies
    for giver, receiver in ((giver_a, giver_b), (giver_b, giver_a)):
        inventory = [i.model_copy() for i in receiver.inventory]
        for offer in trade.offers_of(receiver.id):  # what the receiver gives away frees space
            entry = next(i for i in inventory if i.id == offer.inventory_item_id)
            entry.quantity -= offer.quantity
        inventory = [i for i in inventory if i.quantity > 0]
        capacity = calculate_inventory_capacity(rules.inventory.slots, inventory, content.items)
        for offer in trade.offers_of(giver.id):
            entry = giver.item(offer.inventory_item_id)
            assert entry is not None
            item = content.get_item(entry.item_id)
            added, _ = add_item_to_inventory(
                inventory, item, offer.quantity, capacity, lambda: new_id(rng)
            )
            if added < offer.quantity:
                return f"{receiver.name} hat nicht genug Platz."

    # all good: offers go first (offered items are protected in the database), then the goods
    builder.changes.trade_offers_delete.extend(
        TradeOfferDelete(trade_id=trade.id, inventory_item_id=o.inventory_item_id)
        for o in trade.offers
    )
    moved: dict[UUID, list[tuple[str, int]]] = {giver_a.id: [], giver_b.id: []}
    taken: dict[UUID, list[tuple[InventoryItemState, int]]] = {giver_a.id: [], giver_b.id: []}
    for player in (giver_a, giver_b):
        for offer in trade.offers_of(player.id):
            entry = player.item(offer.inventory_item_id)
            assert entry is not None
            taken[player.id].append((entry.model_copy(), offer.quantity))
            moved[player.id].append((entry.item_id, offer.quantity))
            remove_quantity(builder, player, entry, offer.quantity)
    for giver, receiver in ((giver_a, giver_b), (giver_b, giver_a)):
        capacity = calculate_inventory_capacity(
            rules.inventory.slots, receiver.inventory, content.items
        )
        for entry, quantity in taken[giver.id]:
            item = content.get_item(entry.item_id)
            _, touched = add_item_to_inventory(
                receiver.inventory, item, quantity, capacity, lambda: new_id(rng)
            )
            builder.upsert_inventory(receiver.id, touched)

    gold_a, gold_b = trade.initiator_gold, trade.partner_gold
    giver_a.gold += gold_b - gold_a
    giver_b.gold += gold_a - gold_b
    builder.update_player(giver_a.id, gold=giver_a.gold)
    builder.update_player(giver_b.id, gold=giver_b.gold)
    trade.status = "completed"
    builder.changes.trades.append(
        TradeUpdate(
            trade_id=trade.id, status="completed", initiator_confirmed=True, partner_confirmed=True
        )
    )

    def side(player: PlayerSnapshot) -> dict[str, Any]:
        return {
            "player_id": str(player.id),
            "name": player.name,
            "gold": trade.gold_of(player.id),
            "items": [{"item_id": i, "quantity": q} for i, q in moved[player.id]],
        }

    builder.changes.trade_logs.append(
        TradeLogCreate(
            trade_id=trade.id,
            completed_at=now,
            summary={
                "room_id": trade.room_id,
                "initiator": side(giver_a),
                "partner": side(giver_b),
            },
        )
    )

    def describe(items: list[tuple[str, int]], gold: int) -> str:
        parts = [_label(q, content.get_item(i).name) for i, q in items]
        if gold:
            parts.append(f"{gold} Gold")
        return ", ".join(parts) or "nichts"

    for me, other in ((giver_a, giver_b), (giver_b, giver_a)):
        text = (
            f"Handel mit {other.name} abgeschlossen. Du erhältst "
            f"{describe(moved[other.id], trade.gold_of(other.id))} und gibst "
            f"{describe(moved[me.id], trade.gold_of(me.id))}."
        )
        builder.to_player(me.id, COMPLETED, text, trade_id=str(trade.id))
    return None


# --- cancel ------------------------------------------------------------------------------------


def _cancel(builder: ResultBuilder, snapshot: TradeActionSnapshot, trade: TradeSnapshot) -> None:
    trade.status = "cancelled"
    builder.changes.trades.append(TradeUpdate(trade_id=trade.id, status="cancelled"))
    builder.changes.trade_offers_delete.extend(
        TradeOfferDelete(trade_id=trade.id, inventory_item_id=o.inventory_item_id)
        for o in trade.offers
    )


def resolve_trade_cancel(
    snapshot: TradeActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    trade_id: UUID,
) -> ActionResult:
    actor = snapshot.actor
    trade = snapshot.trade(trade_id)
    if trade is None or not trade.involves(actor.id) or not trade.active:
        return rejected(NO_TRADE)
    declined = trade.status == "requested" and actor.id == trade.partner_id
    builder = ResultBuilder(actor_id=actor.id)
    _cancel(builder, snapshot, trade)
    builder.to_actor(CANCELLED, "Du brichst den Handel ab.", trade_id=str(trade.id))
    builder.to_player(
        trade.other(actor.id),
        CANCELLED,
        f"{actor.name} {'lehnt den Handel ab' if declined else 'bricht den Handel ab'}.",
        trade_id=str(trade.id),
    )
    return builder.build()


def resolve_trade_check(
    snapshot: TradeActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
) -> ActionResult:
    """After something happened to the actor (moved, fight, death, logout, lost connection) and
    from the world ticker: active trades whose traders are no longer both here and free end."""
    builder = ResultBuilder()
    for trade in snapshot.active_trades_of(snapshot.actor.id):
        expired = trade.status == "requested" and trade.expires_at <= now
        sides = (snapshot.player(trade.initiator_id), snapshot.player(trade.partner_id))
        if not expired and all(_available(p, trade.room_id) for p in sides):
            continue
        _cancel(builder, snapshot, trade)
        text = "Die Handelsanfrage ist abgelaufen." if expired else "Der Handel wurde abgebrochen."
        for player in sides:
            if player is not None:
                builder.to_player(player.id, CANCELLED, text, trade_id=str(trade.id))
    return builder.build()
