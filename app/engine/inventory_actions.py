"""Inventory actions: equip, unequip, use_item, pick_up, drop, inventory.

See ARCHITECTURE.md, "Inventar". Capacity rules live in ``inventory.py``; every way into the
inventory uses ``add_item_to_inventory``.
Equipping/unequipping is checked by simulating the resulting inventory, so swaps, split stacks and
bags (which change the capacity themselves) all follow one rule: occupied <= capacity afterwards.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from uuid import UUID

from app.content.registry import ContentRegistry
from app.content.schemas import Heal, ItemTemplate, Rules
from app.engine.encounter import monster_strikes, touch_monster
from app.engine.events import (
    ActionResult,
    FloorItemCreate,
    FloorItemUpdate,
    ItemSpawnUpdate,
    ResultBuilder,
    RoomAudience,
    rejected,
)
from app.engine.group_status import report_hp
from app.engine.inventory import (
    add_item_to_inventory,
    calculate_inventory_capacity,
    get_occupied_slots,
)
from app.engine.permissions import check_state
from app.engine.state import (
    ActionSnapshot,
    FloorItemSnapshot,
    InventoryItemState,
    PlayerSnapshot,
    new_id,
)
from app.engine.stats import stats_of

NOT_OWNED = "Diesen Gegenstand hast du nicht."
LOCKED = "Dieser Gegenstand ist im Handel angeboten."


def _label(quantity: int, item: ItemTemplate) -> str:
    return f"{quantity}x {item.name}" if quantity > 1 else item.name


def _fits(inventory: list[InventoryItemState], content: ContentRegistry, rules: Rules) -> bool:
    capacity = calculate_inventory_capacity(rules.inventory.slots, inventory, content.items)
    return get_occupied_slots(inventory) <= capacity


def _cap_hp(builder: ResultBuilder, player: PlayerSnapshot, content: ContentRegistry) -> None:
    """Losing max HP (e.g. taking off an amulet) never leaves HP above the new maximum."""
    max_hp = stats_of(player, content.items).max_hp
    if player.hp > max_hp:
        player.hp = max_hp
        builder.update_player(player.id, hp=max_hp)
        report_hp(builder, player)


def _remove_quantity(
    builder: ResultBuilder, player: PlayerSnapshot, entry: InventoryItemState, quantity: int
) -> None:
    entry.quantity -= quantity
    if entry.quantity <= 0:
        player.inventory = [i for i in player.inventory if i.id != entry.id]
        builder.changes.inventory_delete.append(entry.id)
    else:
        builder.upsert_inventory(player.id, [entry])


# --- equip / unequip ----------------------------------------------------------------------


def resolve_equip(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    inventory_item_id: UUID,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("equip", actor):
        return denied
    entry = actor.item(inventory_item_id)
    if entry is None:
        return rejected(NOT_OWNED)
    if entry.equipped_slot is not None:
        return rejected("Das trägst du bereits.")
    if entry.id in actor.locked_item_ids:
        return rejected(LOCKED)
    item = content.get_item(entry.item_id)
    slot = item.slot
    if slot is None:
        return rejected("Das kann man nicht ausrüsten.")

    after = [i.model_copy() for i in actor.inventory]
    source = next(i for i in after if i.id == entry.id)
    if source.quantity > 1:  # equip one piece of a stack
        source.quantity -= 1
        equipped = InventoryItemState(
            id=new_id(rng), item_id=item.id, quantity=1, equipped_slot=slot
        )
        after.append(equipped)
    else:
        source.equipped_slot = slot
        equipped = source

    replaced: InventoryItemState | None = None
    if slot == "bag":
        bags = sum(1 for i in after if i.equipped_slot == "bag")
        if bags > rules.inventory.bag_slots:
            return rejected("Alle Taschenplätze sind belegt.")
    else:
        replaced = next((i for i in after if i.equipped_slot == slot and i is not equipped), None)
        if replaced is not None:
            replaced.equipped_slot = None
    if not _fits(after, content, rules):
        return rejected("Kein Platz im Inventar.")

    builder = ResultBuilder(actor_id=actor.id)
    actor.inventory = after
    builder.upsert_inventory(actor.id, [i for i in (source, equipped, replaced) if i is not None])
    if replaced is not None:
        old = content.get_item(replaced.item_id)
        builder.to_actor("inventory.unequipped", f"Du legst {old.name} ab.", item_id=old.id)
    builder.to_actor("inventory.equipped", f"Du rüstest {item.name} aus.", item_id=item.id)
    _cap_hp(builder, actor, content)
    return builder.build()


def resolve_unequip(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    inventory_item_id: UUID,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("unequip", actor):
        return denied
    entry = actor.item(inventory_item_id)
    if entry is None:
        return rejected(NOT_OWNED)
    if entry.equipped_slot is None:
        return rejected("Das trägst du nicht.")
    item = content.get_item(entry.item_id)

    after = [i.model_copy() for i in actor.inventory]
    target = next(i for i in after if i.id == entry.id)
    target.equipped_slot = None
    # a piece split off a stack (see equip) goes back onto a stack with room
    stack = next(
        (
            i
            for i in after
            if item.stackable
            and i is not target
            and i.item_id == item.id
            and i.equipped_slot is None
            and i.id not in actor.locked_item_ids
            and i.quantity + target.quantity <= item.max_stack
        ),
        None,
    )
    if stack is not None:
        stack.quantity += target.quantity
        after = [i for i in after if i is not target]
    if not _fits(after, content, rules):
        if item.kind == "bag":
            return rejected("Leere die Tasche zuerst.")
        return rejected("Kein Platz im Inventar.")

    builder = ResultBuilder(actor_id=actor.id)
    actor.inventory = after
    if stack is not None:
        builder.upsert_inventory(actor.id, [stack])
        builder.changes.inventory_delete.append(target.id)
    else:
        builder.upsert_inventory(actor.id, [target])
    builder.to_actor("inventory.unequipped", f"Du legst {item.name} ab.", item_id=item.id)
    _cap_hp(builder, actor, content)
    return builder.build()


# --- use -----------------------------------------------------------------------------------


def resolve_use_item(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    inventory_item_id: UUID,
) -> ActionResult:
    """Consumables with ``heal`` effects. In combat, using an item costs the round: the monster
    strikes back afterwards."""
    actor = snapshot.actor
    if denied := check_state("use_item", actor):
        return denied
    entry = actor.item(inventory_item_id)
    if entry is None:
        return rejected(NOT_OWNED)
    if entry.id in actor.locked_item_ids:
        return rejected(LOCKED)
    item = content.get_item(entry.item_id)
    heal = sum(e.amount for e in item.effects if isinstance(e, Heal))
    if item.kind != "consumable" or entry.equipped_slot is not None or heal <= 0:
        return rejected("Das kann man nicht benutzen.")
    max_hp = stats_of(actor, content.items).max_hp
    if actor.hp >= max_hp:
        return rejected("Du bist bereits unverletzt.")

    builder = ResultBuilder(actor_id=actor.id)
    healed = min(heal, max_hp - actor.hp)
    actor.hp += healed
    builder.update_player(actor.id, hp=actor.hp)
    _remove_quantity(builder, actor, entry, 1)
    builder.to_actor(
        "inventory.used",
        f"Du benutzt {item.name} und erhältst {healed} HP.",
        item_id=item.id,
        healed=healed,
        hp=actor.hp,
    )
    report_hp(builder, actor)

    monster = snapshot.monster(actor.combat_target_id) if actor.combat_target_id else None
    if actor.state == "in_combat" and monster is not None and monster.alive:
        template = content.get_monster(monster.monster_id)
        touch_monster(builder, monster, template, now)
        monster_strikes(builder, snapshot, actor, template, content, rules, rng)
    return builder.build()


# --- floor: pick up / drop -----------------------------------------------------------------


def resolve_pick_up(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    floor_item_id: UUID,
    quantity: int | None = None,
) -> ActionResult:
    """Picks up as much as fits (partial amounts allowed, the rest stays on the floor).

    Items dropped by any character of the same account cannot be picked up (no transfer between
    own characters, ARCHITECTURE.md "Schutz vor Item-Transfer").
    """
    actor = snapshot.actor
    if denied := check_state("pick_up", actor):
        return denied
    floor = next(
        (
            f
            for f in snapshot.floor_items
            if f.id == floor_item_id and f.room_id == actor.current_room_id and f.visible_at(now)
        ),
        None,
    )
    if floor is None:
        return rejected("Das liegt hier nicht.")
    if floor.dropped_by_account_id is not None and floor.dropped_by_account_id == actor.account_id:
        return rejected("Diesen Gegenstand hat einer deiner Charaktere fallen gelassen.")
    if quantity is not None and quantity < 1:
        return rejected("Ungültige Menge.")
    item = content.get_item(floor.item_id)
    wanted = floor.quantity if quantity is None else min(quantity, floor.quantity)

    capacity = calculate_inventory_capacity(rules.inventory.slots, actor.inventory, content.items)
    added, touched = add_item_to_inventory(
        actor.inventory, item, wanted, capacity, lambda: new_id(rng)
    )
    if added <= 0:
        return rejected("Dein Inventar ist voll.")

    builder = ResultBuilder(actor_id=actor.id)
    builder.upsert_inventory(actor.id, touched)
    remaining = floor.quantity - added
    if remaining > 0:
        floor.quantity = remaining
        builder.changes.floor_items_update.append(FloorItemUpdate(id=floor.id, quantity=remaining))
    else:
        snapshot.floor_items = [f for f in snapshot.floor_items if f.id != floor.id]
        builder.changes.floor_items_delete.append(floor.id)
        if floor.source == "spawn":
            room = content.get_room(floor.room_id)
            rule = next((r for r in room.items if r.item_id == floor.item_id), None)
            if rule is not None and rule.respawn_seconds is not None:
                builder.changes.item_spawns.append(
                    ItemSpawnUpdate(
                        room_id=room.id,
                        item_id=item.id,
                        next_spawn_at=now + timedelta(seconds=rule.respawn_seconds),
                    )
                )

    text = f"Du hebst {_label(added, item)} auf."
    if added < wanted:
        text += f" Kein Platz für {wanted - added} weitere."
    builder.to_actor("inventory.picked_up", text, item_id=item.id, quantity=added)
    builder.event(
        "floor.picked_up",
        RoomAudience(room_id=floor.room_id),
        f"{actor.name} hebt {_label(added, item)} auf.",
        exclude=[actor.id],
        floor_item_id=str(floor.id),
        remaining=remaining,
    )
    return builder.build()


def resolve_drop(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    inventory_item_id: UUID,
    quantity: int | None = None,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("drop", actor):
        return denied
    entry = actor.item(inventory_item_id)
    if entry is None:
        return rejected(NOT_OWNED)
    if entry.equipped_slot is not None:
        return rejected("Lege den Gegenstand zuerst ab.")
    if entry.id in actor.locked_item_ids:
        return rejected(LOCKED)
    amount = entry.quantity if quantity is None else quantity
    if amount < 1 or amount > entry.quantity:
        return rejected("So viele hast du nicht.")
    item = content.get_item(entry.item_id)

    builder = ResultBuilder(actor_id=actor.id)
    _remove_quantity(builder, actor, entry, amount)
    created = FloorItemCreate(
        id=new_id(rng),
        room_id=actor.current_room_id,
        item_id=item.id,
        quantity=amount,
        source="drop",
        dropped_by_account_id=actor.account_id,
        expires_at=now + timedelta(seconds=rules.timings.floor_item_ttl_seconds),
    )
    builder.changes.floor_items_create.append(created)
    snapshot.floor_items.append(FloorItemSnapshot.model_validate(created.model_dump()))
    builder.to_actor(
        "inventory.dropped", f"Du lässt {_label(amount, item)} fallen.", item_id=item.id
    )
    builder.event(
        "floor.dropped",
        RoomAudience(room_id=actor.current_room_id),
        f"{actor.name} lässt {_label(amount, item)} fallen.",
        exclude=[actor.id],
        floor_item_id=str(created.id),
        item_id=item.id,
        quantity=amount,
    )
    return builder.build()


# --- view ----------------------------------------------------------------------------------


def resolve_inventory(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
) -> ActionResult:
    """Inventory view incl. occupied/free slots (e.g. "14/32") and bag slots."""
    actor = snapshot.actor
    if denied := check_state("inventory", actor):
        return denied
    capacity = calculate_inventory_capacity(rules.inventory.slots, actor.inventory, content.items)
    used = get_occupied_slots(actor.inventory)
    bags = sum(1 for i in actor.inventory if i.equipped_slot == "bag")
    entries = []
    for entry in actor.inventory:
        item = content.get_item(entry.item_id)
        entries.append(
            {
                "id": str(entry.id),
                "item_id": item.id,
                "name": item.name,
                "icon": item.icon,
                "quantity": entry.quantity,
                "equipped_slot": entry.equipped_slot,
                "locked": entry.id in actor.locked_item_ids,
            }
        )

    builder = ResultBuilder(actor_id=actor.id)
    builder.to_actor(
        "inventory.view",
        f"Inventar: {used}/{capacity} Plätze, Taschen {bags}/{rules.inventory.bag_slots}, "
        f"{actor.gold} Gold.",
        used=used,
        capacity=capacity,
        bags=bags,
        bag_slots=rules.inventory.bag_slots,
        gold=actor.gold,
        items=entries,
    )
    return builder.build()
