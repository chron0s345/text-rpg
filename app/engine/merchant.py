"""Buying from and selling to NPC merchants (ARCHITECTURE.md, "Händler").

Merchants are content and stand in a fixed room; stock is unlimited. Sold items vanish (no
resale, so no detour for moving items between one's own characters). Prices: ``content/prices``.
"""

from __future__ import annotations

import random
from datetime import datetime
from uuid import UUID

from app.content.prices import buy_price, sell_price
from app.content.registry import ContentRegistry
from app.content.schemas import MerchantTemplate, Rules
from app.engine.events import ActionResult, ResultBuilder, rejected
from app.engine.gear import junk_for
from app.engine.inventory import (
    add_item_to_inventory,
    calculate_inventory_capacity,
    can_add_quantity,
)
from app.engine.inventory_actions import LOCKED, NOT_OWNED, remove_quantity
from app.engine.permissions import check_state
from app.engine.state import ActionSnapshot, new_id


def _merchant_here(
    snapshot: ActionSnapshot, content: ContentRegistry, merchant_id: str
) -> MerchantTemplate | None:
    return next(
        (
            m
            for m in content.merchants_in_room(snapshot.actor.current_room_id)
            if m.id == merchant_id
        ),
        None,
    )


def _label(quantity: int, name: str) -> str:
    return f"{quantity}x {name}" if quantity > 1 else name


def resolve_buy(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    merchant_id: str,
    item_id: str,
    quantity: int = 1,
) -> ActionResult:
    """All or nothing: the whole quantity must be affordable and fit into the inventory."""
    actor = snapshot.actor
    if denied := check_state("buy", actor):
        return denied
    merchant = _merchant_here(snapshot, content, merchant_id)
    if merchant is None:
        return rejected("Dieser Händler ist nicht hier.")
    offer = next((o for o in merchant.sells if o.item_id == item_id), None)
    if offer is None:
        return rejected(f"{merchant.name} verkauft das nicht.")
    if quantity < 1:
        return rejected("Ungültige Menge.")
    item = content.get_item(item_id)
    total = buy_price(offer, item, rules) * quantity
    if total > actor.gold:
        return rejected(f"Das kostet {total} Gold, du hast nur {actor.gold}.")
    capacity = calculate_inventory_capacity(rules.inventory.slots, actor.inventory, content.items)
    if can_add_quantity(actor.inventory, item, quantity, capacity) < quantity:
        return rejected("Dafür ist in deinem Inventar kein Platz.")

    builder = ResultBuilder(actor_id=actor.id)
    _, touched = add_item_to_inventory(
        actor.inventory, item, quantity, capacity, lambda: new_id(rng)
    )
    builder.upsert_inventory(actor.id, touched)
    actor.gold -= total
    builder.update_player(actor.id, gold=actor.gold)
    builder.to_actor(
        "merchant.bought",
        f"Du kaufst {_label(quantity, item.name)} für {total} Gold.",
        item_id=item.id,
        quantity=quantity,
        gold=total,
    )
    return builder.build()


def resolve_sell(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    merchant_id: str,
    inventory_item_id: UUID,
    quantity: int = 1,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("sell", actor):
        return denied
    merchant = _merchant_here(snapshot, content, merchant_id)
    if merchant is None:
        return rejected("Dieser Händler ist nicht hier.")
    entry = actor.item(inventory_item_id)
    if entry is None:
        return rejected(NOT_OWNED)
    if entry.equipped_slot is not None:
        return rejected("Lege den Gegenstand zuerst ab.")
    if entry.id in actor.locked_item_ids:
        return rejected(LOCKED)
    if quantity < 1 or quantity > entry.quantity:
        return rejected("So viele hast du nicht.")
    item = content.get_item(entry.item_id)
    price = sell_price(item, merchant, rules)
    if price is None:
        return rejected(f"{merchant.name} kauft das nicht.")

    builder = ResultBuilder(actor_id=actor.id)
    remove_quantity(builder, actor, entry, quantity)
    total = price * quantity
    actor.gold += total
    builder.update_player(actor.id, gold=actor.gold)
    builder.to_actor(
        "merchant.sold",
        f"Du verkaufst {_label(quantity, item.name)} für {total} Gold.",
        item_id=item.id,
        quantity=quantity,
        gold=total,
    )
    return builder.build()


def resolve_sell_junk(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    merchant_id: str,
) -> ActionResult:
    """Sells everything ``engine.gear.junk_for`` calls junk to this merchant, whole stacks, in
    one go."""
    actor = snapshot.actor
    if denied := check_state("sell_junk", actor):
        return denied
    merchant = _merchant_here(snapshot, content, merchant_id)
    if merchant is None:
        return rejected("Dieser Händler ist nicht hier.")
    junk = junk_for(actor, merchant, content, rules)
    if not junk:
        return rejected(f"Du hast keinen Ramsch, den {merchant.name} kauft.")

    builder = ResultBuilder(actor_id=actor.id)
    total = 0
    labels = []
    for j in junk:
        quantity = j.entry.quantity
        labels.append(_label(quantity, content.get_item(j.entry.item_id).name))
        total += j.price * quantity
        remove_quantity(builder, actor, j.entry, quantity)
    actor.gold += total
    builder.update_player(actor.id, gold=actor.gold)
    builder.to_actor(
        "merchant.sold",
        f"Du verkaufst {', '.join(labels)} für {total} Gold.",
        gold=total,
        quantity=sum(j.entry.quantity for j in junk),
    )
    return builder.build()
