from __future__ import annotations

from collections.abc import Callable, Sequence
from uuid import UUID

from app.content.schemas import ItemTemplate
from app.engine.state import InventoryItemState

IdFactory = Callable[[], UUID]


def item_label(quantity: int, item: ItemTemplate) -> str:
    """Player-facing name of a stack: "3x Brot", or just the name for a single item."""
    return f"{quantity}x {item.name}" if quantity > 1 else item.name


def calculate_inventory_capacity(
    base_slots: int,
    items: Sequence[InventoryItemState],
    item_templates: dict[str, ItemTemplate],
) -> int:
    """capacity = inventory.slots + Σ bag_slots of equipped bags (derived, never stored)."""
    extra_slots = 0
    for it in items:
        if it.equipped_slot == "bag":
            template = item_templates.get(it.item_id)
            if template and template.kind == "bag" and template.bag_slots:
                extra_slots += template.bag_slots
    return base_slots + extra_slots


def get_occupied_slots(items: Sequence[InventoryItemState]) -> int:
    """Every unequipped entry occupies one slot, regardless of quantity."""
    return sum(1 for it in items if it.equipped_slot is None)


def can_add_quantity(
    items: Sequence[InventoryItemState],
    template: ItemTemplate,
    desired_quantity: int,
    capacity: int,
) -> int:
    """How many units of ``template`` fit. Partial fits allowed; stacks are topped up first."""
    if desired_quantity <= 0:
        return 0

    available_slots = max(0, capacity - get_occupied_slots(items))
    remaining = desired_quantity
    accepted = 0

    if not template.stackable:
        return min(remaining, available_slots)

    for it in items:
        if it.equipped_slot is None and it.item_id == template.id:
            space = template.max_stack - it.quantity
            if space > 0:
                take = min(remaining, space)
                accepted += take
                remaining -= take
                if remaining == 0:
                    return accepted

    accepted += min(remaining, available_slots * template.max_stack)
    return accepted


def add_item_to_inventory(
    items: list[InventoryItemState],
    template: ItemTemplate,
    quantity: int,
    capacity: int,
    id_factory: IdFactory,
) -> tuple[int, list[InventoryItemState]]:
    """Adds as much as fits (mutates ``items``).

    Returns ``(added_quantity, touched_entries)``; touched entries are new or modified stacks
    the service has to upsert.
    """
    can_fit = can_add_quantity(items, template, quantity, capacity)
    if can_fit <= 0:
        return 0, []

    remaining = can_fit
    touched: list[InventoryItemState] = []

    if template.stackable:
        for it in items:
            if remaining == 0:
                break
            if it.equipped_slot is None and it.item_id == template.id:
                space = template.max_stack - it.quantity
                if space > 0:
                    fill = min(remaining, space)
                    it.quantity += fill
                    remaining -= fill
                    touched.append(it)

    while remaining > 0:
        stack_qty = min(remaining, template.max_stack) if template.stackable else 1
        entry = InventoryItemState(id=id_factory(), item_id=template.id, quantity=stack_qty)
        items.append(entry)
        touched.append(entry)
        remaining -= stack_qty

    return can_fit, touched


def can_unequip_item(items: Sequence[InventoryItemState], capacity: int) -> bool:
    """Unequipping a regular item needs one free slot."""
    return get_occupied_slots(items) + 1 <= capacity


def can_unequip_bag(
    items: Sequence[InventoryItemState],
    bag_template: ItemTemplate,
    base_slots: int,
    item_templates: dict[str, ItemTemplate],
) -> bool:
    """A bag can be unequipped only if everything (incl. the bag itself) still fits afterwards."""
    current_capacity = calculate_inventory_capacity(base_slots, items, item_templates)
    new_capacity = current_capacity - (bag_template.bag_slots or 0)
    return get_occupied_slots(items) + 1 <= new_capacity
