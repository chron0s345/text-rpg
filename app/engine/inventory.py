from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
from uuid import UUID, uuid4

from app.content.schemas import ItemTemplate


@dataclass
class InventoryItemState:
    id: UUID
    item_id: str
    quantity: int
    equipped_slot: str | None = None


def calculate_inventory_capacity(
    base_slots: int,
    items: Sequence[InventoryItemState],
    item_templates: dict[str, ItemTemplate],
) -> int:
    """Computes dynamic player inventory capacity.

    capacity = base_slots + sum(bag_slots of equipped bags)
    """
    extra_slots = 0
    for it in items:
        if it.equipped_slot == "bag":
            template = item_templates.get(it.item_id)
            if template and template.kind == "bag" and template.bag_slots:
                extra_slots += template.bag_slots
    return base_slots + extra_slots


def get_occupied_slots(items: Sequence[InventoryItemState]) -> int:
    """Returns number of slots occupied by unequipped inventory items."""
    return sum(1 for it in items if it.equipped_slot is None)


def can_add_quantity(
    items: Sequence[InventoryItemState],
    template: ItemTemplate,
    desired_quantity: int,
    capacity: int,
) -> int:
    """Calculates how many units of template item can be added into the inventory.

    Supports partial fit. Stackable items fill existing non-full stacks first.
    """
    if desired_quantity <= 0:
        return 0

    available_slots = max(0, capacity - get_occupied_slots(items))
    accepted = 0
    remaining = desired_quantity

    if template.stackable:
        # 1. Fill existing unequipped stacks of this item
        for it in items:
            if it.equipped_slot is None and it.item_id == template.id:
                space_in_stack = template.max_stack - it.quantity
                if space_in_stack > 0:
                    add_to_stack = min(remaining, space_in_stack)
                    accepted += add_to_stack
                    remaining -= add_to_stack
                    if remaining == 0:
                        return accepted

        # 2. Fill new slots
        slots_needed = (remaining + template.max_stack - 1) // template.max_stack
        usable_slots = min(available_slots, slots_needed)
        can_fit_in_new_slots = min(remaining, usable_slots * template.max_stack)
        accepted += can_fit_in_new_slots
        return accepted
    else:
        # Non-stackable: 1 item per slot
        can_fit = min(remaining, available_slots)
        return can_fit


def add_item_to_inventory(
    items: list[InventoryItemState],
    template: ItemTemplate,
    quantity: int,
    capacity: int,
) -> int:
    """Mutates items list by adding as much of the item as fits.

    Returns the number of units actually added.
    """
    can_fit = can_add_quantity(items, template, quantity, capacity)
    if can_fit <= 0:
        return 0

    remaining_to_add = can_fit

    if template.stackable:
        # Fill existing stacks
        for it in items:
            if it.equipped_slot is None and it.item_id == template.id:
                space = template.max_stack - it.quantity
                if space > 0:
                    fill = min(remaining_to_add, space)
                    it.quantity += fill
                    remaining_to_add -= fill
                    if remaining_to_add == 0:
                        break

        # Create new stacks
        while remaining_to_add > 0:
            stack_qty = min(remaining_to_add, template.max_stack)
            items.append(
                InventoryItemState(
                    id=uuid4(),
                    item_id=template.id,
                    quantity=stack_qty,
                    equipped_slot=None,
                )
            )
            remaining_to_add -= stack_qty
    else:
        # Non-stackable: each is a separate item
        for _ in range(remaining_to_add):
            items.append(
                InventoryItemState(
                    id=uuid4(),
                    item_id=template.id,
                    quantity=1,
                    equipped_slot=None,
                )
            )

    return can_fit


def can_unequip_item(
    items: Sequence[InventoryItemState],
    capacity: int,
) -> bool:
    """Checks if there is space in inventory to unequip a standard item (occupies 1 slot)."""
    return get_occupied_slots(items) + 1 <= capacity


def can_unequip_bag(
    items: Sequence[InventoryItemState],
    bag_template: ItemTemplate,
    base_slots: int,
    item_templates: dict[str, ItemTemplate],
) -> bool:
    """Checks if a bag can be unequipped.

    The unequipped bag will occupy 1 slot, and inventory loses the bag's extra slots.
    """
    current_capacity = calculate_inventory_capacity(base_slots, items, item_templates)
    bag_bonus = bag_template.bag_slots or 0
    new_capacity = current_capacity - bag_bonus
    # Bag itself will become an unequipped inventory item (+1 occupied slot)
    new_occupied_slots = get_occupied_slots(items) + 1
    return new_occupied_slots <= new_capacity
