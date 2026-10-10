from __future__ import annotations

import logging
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from app.content.registry import ContentRegistry
from app.content.schemas import (
    ItemTemplate,
    MerchantTemplate,
    MonsterTemplate,
    RoomTemplate,
    Rules,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ContentValidationError(Exception):
    """Raised when game content validation fails."""


def load_yaml_file(path: Path) -> Any:
    if not path.is_file():
        raise ContentValidationError(f"Required content file not found: {path}")
    with open(path, encoding="utf-8") as f:
        try:
            return yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ContentValidationError(f"Invalid YAML in {path}: {exc}") from exc


def _parse_list(path: Path, model: type[T], get_id: Callable[[T], str]) -> dict[str, T]:
    raw = load_yaml_file(path)
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ContentValidationError(f"{path.name} must contain a YAML list at top level")
    result: dict[str, T] = {}
    for index, entry in enumerate(raw):
        try:
            parsed = model.model_validate(entry)
        except ValidationError as exc:
            raise ContentValidationError(f"{path.name} entry #{index}: {exc}") from exc
        key = get_id(parsed)
        if key in result:
            raise ContentValidationError(f"Duplicate ID in {path.name}: '{key}'")
        result[key] = parsed
    return result


def load_content(data_dir: Path, icons_dir: Path | None = None) -> ContentRegistry:
    """Loads and validates all content. Aborts with ContentValidationError on any problem.

    If ``icons_dir`` is given, every ``ItemTemplate.icon`` must exist as ``<icon>.svg`` there.
    """
    data_dir = Path(data_dir)

    rooms = _parse_list(data_dir / "rooms.yaml", RoomTemplate, lambda r: r.id)
    monsters = _parse_list(data_dir / "monsters.yaml", MonsterTemplate, lambda m: m.id)
    items = _parse_list(data_dir / "items.yaml", ItemTemplate, lambda i: i.id)
    merchants = _parse_list(data_dir / "merchants.yaml", MerchantTemplate, lambda m: m.id)

    rules_raw = load_yaml_file(data_dir / "rules.yaml")
    if not isinstance(rules_raw, dict):
        raise ContentValidationError("rules.yaml must contain a YAML mapping at top level")
    try:
        rules = Rules.model_validate(rules_raw)
    except ValidationError as exc:
        raise ContentValidationError(f"rules.yaml: {exc}") from exc

    for monster in monsters.values():
        min_g, max_g = monster.gold_reward
        if min_g > max_g:
            raise ContentValidationError(
                f"Monster '{monster.id}' gold_reward min ({min_g}) > max ({max_g})"
            )
        for loot in monster.loot_table:
            if loot.min_qty > loot.max_qty:
                raise ContentValidationError(
                    f"Monster '{monster.id}' loot entry '{loot.item_id}' min_qty > max_qty"
                )

    validate_content(rooms, monsters, items, merchants, rules)
    if icons_dir is not None:
        validate_icons(items, Path(icons_dir))

    return ContentRegistry(
        rooms=rooms,
        monsters=monsters,
        items=items,
        merchants=merchants,
        rules=rules,
    )


def validate_icons(items: dict[str, ItemTemplate], icons_dir: Path) -> None:
    for item in items.values():
        if item.icon is not None and not (icons_dir / f"{item.icon}.svg").is_file():
            raise ContentValidationError(
                f"Item '{item.id}' references missing icon '{item.icon}.svg' in {icons_dir}"
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

        spawn_item_ids = [item_spawn.item_id for item_spawn in room.items]
        if len(spawn_item_ids) != len(set(spawn_item_ids)):
            # item spawn state is keyed by (room, item): one rule per item and room
            raise ContentValidationError(f"Room '{room.id}' has several spawn rules for one item")
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
        if merchant.buys == "listed" and not merchant.buys_kinds:
            raise ContentValidationError(
                f"Merchant '{merchant.id}' has buys: listed but no buys_kinds"
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
