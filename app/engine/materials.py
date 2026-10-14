"""Items a character hands over (quest deliveries, crafting ingredients) and items they receive
all-or-nothing (quest rewards, crafted results).

Only free backpack items count: not equipped, not offered in an open trade.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.events import ResultBuilder
from app.engine.inventory import add_item_to_inventory, calculate_inventory_capacity
from app.engine.state import InventoryItemState, PlayerSnapshot, new_id


def available_items(actor: PlayerSnapshot, item_id: str) -> list[InventoryItemState]:
    return [
        i
        for i in actor.inventory
        if i.item_id == item_id and i.equipped_slot is None and i.id not in actor.locked_item_ids
    ]


def count_available(actor: PlayerSnapshot, item_id: str) -> int:
    return sum(i.quantity for i in available_items(actor, item_id))


def take_items(builder: ResultBuilder, actor: PlayerSnapshot, item_id: str, quantity: int) -> None:
    """Removes ``quantity`` of ``item_id`` from free, unlocked stacks (caller checked enough)."""
    remaining = quantity
    for entry in available_items(actor, item_id):
        if remaining == 0:
            break
        taken = min(entry.quantity, remaining)
        remaining -= taken
        entry.quantity -= taken
        if entry.quantity == 0:
            actor.inventory = [i for i in actor.inventory if i.id != entry.id]
            builder.changes.inventory_delete.append(entry.id)
        else:
            builder.upsert_inventory(actor.id, [entry])


def give_all_or_nothing(
    builder: ResultBuilder,
    actor: PlayerSnapshot,
    items: Sequence[tuple[str, int]],
    content: ContentRegistry,
    rules: Rules,
    rng: random.Random,
) -> bool:
    """Adds every (item_id, quantity) to the backpack, or nothing if they do not all fit."""
    capacity = calculate_inventory_capacity(rules.inventory.slots, actor.inventory, content.items)
    trial = [i.model_copy() for i in actor.inventory]
    for item_id, quantity in items:
        added, _ = add_item_to_inventory(
            trial, content.get_item(item_id), quantity, capacity, lambda: new_id(rng)
        )
        if added < quantity:
            return False
    for item_id, quantity in items:
        _, touched = add_item_to_inventory(
            actor.inventory, content.get_item(item_id), quantity, capacity, lambda: new_id(rng)
        )
        builder.upsert_inventory(actor.id, touched)
    return True


def quantity_text(content: ContentRegistry, item_id: str, quantity: int) -> str:
    name = content.get_item(item_id).name
    return f"{quantity}× {name}" if quantity > 1 else name
