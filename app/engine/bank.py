"""Bank storage (ARCHITECTURE.md, "Bank"): per character, ``bank.slots`` slots, same stacking
rules as the inventory, safe from death losses. Only in bank rooms and while exploring.

The snapshot holds the actor's bank (``ActionSnapshot.bank``, loaded and locked for these
actions only). Partial moves: as much as fits is moved, the rest stays where it was.
"""

from __future__ import annotations

import random
from datetime import datetime
from uuid import UUID

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.events import ActionResult, ResultBuilder, rejected
from app.engine.inventory import add_item_to_inventory, calculate_inventory_capacity
from app.engine.inventory_actions import LOCKED, NOT_OWNED, remove_quantity
from app.engine.permissions import check_state
from app.engine.state import ActionSnapshot, new_id

NO_BANK = "Hier gibt es keine Bank."


def _label(quantity: int, name: str) -> str:
    return f"{quantity}x {name}" if quantity > 1 else name


def resolve_bank_deposit(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    inventory_item_id: UUID,
    quantity: int = 1,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("bank_deposit", actor):
        return denied
    if not content.get_room(actor.current_room_id).bank:
        return rejected(NO_BANK)
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

    added, touched = add_item_to_inventory(
        snapshot.bank, item, quantity, rules.bank.slots, lambda: new_id(rng)
    )
    if added <= 0:
        return rejected("Deine Bank ist voll.")
    builder = ResultBuilder(actor_id=actor.id)
    builder.upsert_bank(actor.id, touched)
    remove_quantity(builder, actor, entry, added)
    text = f"Du lagerst {_label(added, item.name)} ein."
    if added < quantity:
        text += f" Kein Platz für {quantity - added} weitere."
    builder.to_actor("bank.deposited", text, item_id=item.id, quantity=added)
    return builder.build()


def resolve_bank_withdraw(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    bank_item_id: UUID,
    quantity: int = 1,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("bank_withdraw", actor):
        return denied
    if not content.get_room(actor.current_room_id).bank:
        return rejected(NO_BANK)
    stored = next((b for b in snapshot.bank if b.id == bank_item_id), None)
    if stored is None:
        return rejected("Das liegt nicht in deiner Bank.")
    if quantity < 1 or quantity > stored.quantity:
        return rejected("So viele hast du nicht eingelagert.")
    item = content.get_item(stored.item_id)

    capacity = calculate_inventory_capacity(rules.inventory.slots, actor.inventory, content.items)
    added, touched = add_item_to_inventory(
        actor.inventory, item, quantity, capacity, lambda: new_id(rng)
    )
    if added <= 0:
        return rejected("Dein Inventar ist voll.")
    builder = ResultBuilder(actor_id=actor.id)
    builder.upsert_inventory(actor.id, touched)
    stored.quantity -= added
    if stored.quantity <= 0:
        snapshot.bank = [b for b in snapshot.bank if b.id != stored.id]
        builder.changes.bank_delete.append(stored.id)
    else:
        builder.upsert_bank(actor.id, [stored])
    text = f"Du holst {_label(added, item.name)} aus der Bank."
    if added < quantity:
        text += f" Kein Platz für {quantity - added} weitere."
    builder.to_actor("bank.withdrawn", text, item_id=item.id, quantity=added)
    return builder.build()


def resolve_bank_deposit_materials(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
) -> ActionResult:
    """Stores every free stack of materials (``misc``: loot, crafting goods, trophies, finds) as
    far as the bank has room; gear, consumables and bags stay in the backpack."""
    actor = snapshot.actor
    if denied := check_state("bank_deposit_materials", actor):
        return denied
    if not content.get_room(actor.current_room_id).bank:
        return rejected(NO_BANK)
    materials = [
        entry
        for entry in actor.inventory
        if entry.equipped_slot is None
        and entry.id not in actor.locked_item_ids
        and content.get_item(entry.item_id).kind == "misc"
    ]
    if not materials:
        return rejected("Du hast kein Material dabei.")

    builder = ResultBuilder(actor_id=actor.id)
    stored: list[str] = []
    left = 0
    for entry in materials:
        item = content.get_item(entry.item_id)
        quantity = entry.quantity
        added, touched = add_item_to_inventory(
            snapshot.bank, item, quantity, rules.bank.slots, lambda: new_id(rng)
        )
        left += quantity - added
        if added <= 0:
            continue
        builder.upsert_bank(actor.id, touched)
        remove_quantity(builder, actor, entry, added)
        stored.append(_label(added, item.name))
    if not stored:
        return rejected("Deine Bank ist voll.")
    text = f"Du lagerst {', '.join(stored)} ein."
    if left:
        text += f" Kein Platz für {left} weitere."
    builder.to_actor("bank.deposited", text, quantity=len(stored))
    return builder.build()
