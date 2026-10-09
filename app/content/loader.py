from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import yaml

from app.content.registry import ContentRegistry
from app.content.schemas import (
    ItemTemplate,
    MerchantTemplate,
    MonsterTemplate,
    RoomTemplate,
    Rules,
)

logger = logging.getLogger(__name__)


class ContentValidationError(Exception):
    """Raised when game content validation fails."""

    pass


def load_yaml_file(path: Path) -> Any:
    if not path.is_file():
        raise ContentValidationError(f"Required content file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_content(data_dir: Path) -> ContentRegistry:
    data_dir = Path(data_dir)

    rooms_raw = load_yaml_file(data_dir / "rooms.yaml")
    monsters_raw = load_yaml_file(data_dir / "monsters.yaml")
    items_raw = load_yaml_file(data_dir / "items.yaml")
    merchants_raw = load_yaml_file(data_dir / "merchants.yaml")
    rules_raw = load_yaml_file(data_dir / "rules.yaml")

    # Parse and check duplicate IDs
    rooms: dict[str, RoomTemplate] = {}
    for r_data in rooms_raw if isinstance(rooms_raw, list) else []:
        room = RoomTemplate.model_validate(r_data)
        if room.id in rooms:
            raise ContentValidationError(f"Duplicate room ID: '{room.id}'")
        rooms[room.id] = room

    monsters: dict[str, MonsterTemplate] = {}
    for m_data in monsters_raw if isinstance(monsters_raw, list) else []:
        monster = MonsterTemplate.model_validate(m_data)
        if monster.id in monsters:
            raise ContentValidationError(f"Duplicate monster ID: '{monster.id}'")
        if monster.gold_reward[0] > monster.gold_reward[1]:
            min_g, max_g = monster.gold_reward
            raise ContentValidationError(
                f"Monster '{monster.id}' gold_reward min ({min_g}) > max ({max_g})"
            )
        for loot in monster.loot_table:
            if loot.min_qty > loot.max_qty:
                raise ContentValidationError(
                    f"Monster '{monster.id}' loot entry '{loot.item_id}' min_qty > max_qty"
                )
        monsters[monster.id] = monster

    items: dict[str, ItemTemplate] = {}
    for i_data in items_raw if isinstance(items_raw, list) else []:
        item = ItemTemplate.model_validate(i_data)
        if item.id in items:
            raise ContentValidationError(f"Duplicate item ID: '{item.id}'")
        items[item.id] = item

    merchants: dict[str, MerchantTemplate] = {}
    for merc_data in merchants_raw if isinstance(merchants_raw, list) else []:
        merchant = MerchantTemplate.model_validate(merc_data)
        if merchant.id in merchants:
            raise ContentValidationError(f"Duplicate merchant ID: '{merchant.id}'")
        merchants[merchant.id] = merchant

    rules = Rules.model_validate(rules_raw if isinstance(rules_raw, dict) else {})
    if rules.death.gold_loss_percent[0] > rules.death.gold_loss_percent[1]:
        raise ContentValidationError(
            f"rules.death.gold_loss_percent min > max: {rules.death.gold_loss_percent}"
        )

    # Perform Cross-Validations as specified in ARCHITECTURE.md
    validate_content(rooms, monsters, items, merchants, rules)

    return ContentRegistry(
        rooms=rooms,
        monsters=monsters,
        items=items,
        merchants=merchants,
        rules=rules,
    )


def validate_content(
    rooms: dict[str, RoomTemplate],
    monsters: dict[str, MonsterTemplate],
    items: dict[str, ItemTemplate],
    merchants: dict[str, MerchantTemplate],
    rules: Rules,
) -> None:
    # 1. Bank room exists
    if not any(r.bank for r in rooms.values()):
        raise ContentValidationError("No room with bank: true found in rooms.yaml")

    # 2. start_room_id and town_room_id validity
    for target_name, target_id in [
        ("start_room_id", rules.start_room_id),
        ("town_room_id", rules.town_room_id),
    ]:
        if target_id not in rooms:
            raise ContentValidationError(
                f"Rules {target_name} '{target_id}' does not exist in rooms.yaml"
            )
        room = rooms[target_id]
        if not room.safe:
            raise ContentValidationError(f"Rules {target_name} '{target_id}' must be safe: true")
        if not room.checkpoint:
            raise ContentValidationError(
                f"Rules {target_name} '{target_id}' must be checkpoint: true"
            )

    # 3. Item slot & stack & bag rules
    for item in items.values():
        if item.kind in ("weapon", "armor") and item.slot is None:
            raise ContentValidationError(f"Item '{item.id}' of kind '{item.kind}' must have a slot")
        if item.kind == "bag" and item.slot != "bag":
            raise ContentValidationError(f"Item '{item.id}' of kind 'bag' must have slot 'bag'")
        if item.kind in ("consumable", "misc") and item.slot is not None:
            raise ContentValidationError(
                f"Item '{item.id}' of kind '{item.kind}' cannot have an equip slot"
            )

        if not item.stackable and item.max_stack > 1:
            raise ContentValidationError(f"Non-stackable item '{item.id}' has max_stack > 1")

        if item.kind == "bag":
            if item.bag_slots is None or item.bag_slots <= 0:
                raise ContentValidationError(
                    f"Bag item '{item.id}' must specify positive bag_slots"
                )
        else:
            if item.bag_slots is not None:
                raise ContentValidationError(f"Non-bag item '{item.id}' cannot have bag_slots")

    # 4. Room validations
    for room in rooms.values():
        if room.safe and len(room.spawns) > 0:
            raise ContentValidationError(f"Safe room '{room.id}' cannot have monster spawns")

        for direction, exit_room_id in room.exits.items():
            if exit_room_id not in rooms:
                raise ContentValidationError(
                    f"Room '{room.id}' exit '{direction}' points to unknown room '{exit_room_id}'"
                )

        for spawn in room.spawns:
            if spawn.monster_id not in monsters:
                raise ContentValidationError(
                    f"Room '{room.id}' spawns unknown monster '{spawn.monster_id}'"
                )

        for item_spawn in room.items:
            if item_spawn.item_id not in items:
                raise ContentValidationError(
                    f"Room '{room.id}' spawns unknown item '{item_spawn.item_id}'"
                )

    # 5. Monster validations
    for monster in monsters.values():
        for loot in monster.loot_table:
            if loot.item_id not in items:
                raise ContentValidationError(
                    f"Monster '{monster.id}' loot table references unknown item '{loot.item_id}'"
                )

    # 6. Merchant validations & Infinite Money Loop check
    for merchant in merchants.values():
        if merchant.room_id not in rooms:
            raise ContentValidationError(
                f"Merchant '{merchant.id}' is in unknown room '{merchant.room_id}'"
            )

        for offer in merchant.sells:
            if offer.item_id not in items:
                raise ContentValidationError(
                    f"Merchant '{merchant.id}' sells unknown item '{offer.item_id}'"
                )

    # Money printing check:
    # Check if any merchant sells an item cheaper than any merchant is willing to buy it.
    for merchant in merchants.values():
        for offer in merchant.sells:
            item = items[offer.item_id]
            buy_price = (
                offer.price
                if offer.price is not None
                else int(item.value * rules.merchant.buy_ratio)
            )

            if not item.sellable:
                continue

            for other_merchant in merchants.values():
                # Can other_merchant buy this item?
                if other_merchant.buys == "listed" and item.kind not in other_merchant.buys_kinds:
                    continue

                sell_ratio = (
                    other_merchant.sell_ratio
                    if other_merchant.sell_ratio is not None
                    else rules.merchant.sell_ratio
                )
                sell_revenue = math.floor(item.value * sell_ratio)

                if buy_price < sell_revenue:
                    raise ContentValidationError(
                        f"Infinite money loop detected! Player can buy '{item.id}' "
                        f"from '{merchant.id}' for {buy_price}g "
                        f"and sell to '{other_merchant.id}' for {sell_revenue}g."
                    )

    # Reachability warnings (non-fatal, logged)
    check_reachability_warnings(rooms, rules.start_room_id)


def check_reachability_warnings(rooms: dict[str, RoomTemplate], start_room_id: str) -> None:
    # 1. Unreachable rooms from start_room_id
    visited: set[str] = set()
    queue = [start_room_id]
    visited.add(start_room_id)
    while queue:
        current = queue.pop(0)
        for dest in rooms[current].exits.values():
            if dest in rooms and dest not in visited:
                visited.add(dest)
                queue.append(dest)

    unreachable = set(rooms.keys()) - visited
    if unreachable:
        logger.warning(f"Unreachable rooms from start room '{start_room_id}': {unreachable}")

    # 2. Rooms with monsters from which no checkpoint is reachable
    checkpoints = {r_id for r_id, r in rooms.items() if r.checkpoint}
    for r_id, r in rooms.items():
        if len(r.spawns) > 0:
            # Check if any checkpoint is reachable from r_id
            seen: set[str] = {r_id}
            q = [r_id]
            can_reach_cp = False
            while q:
                curr = q.pop(0)
                if curr in checkpoints:
                    can_reach_cp = True
                    break
                for dest in rooms[curr].exits.values():
                    if dest in rooms and dest not in seen:
                        seen.add(dest)
                        q.append(dest)
            if not can_reach_cp:
                logger.warning(
                    f"Room '{r_id}' has monster spawns but no checkpoint is reachable from it"
                )
