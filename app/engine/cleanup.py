"""World upkeep decided for the ticker (ARCHITECTURE.md, "Respawn & Aufräumen"): spawn items that
are due again and floor items that expired. The ticker only loads, locks and applies."""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.content.registry import ContentRegistry
from app.engine.events import (
    ActionResult,
    FloorItemCreate,
    ItemSpawnUpdate,
    ResultBuilder,
    RoomAudience,
)
from app.engine.state import FloorItemSnapshot, new_id


@dataclass(frozen=True)
class ItemSpawnSnapshot:
    room_id: str
    item_id: str
    next_spawn_at: datetime | None


def expired_floor_items(floor_items: Iterable[FloorItemSnapshot], now: datetime) -> list[UUID]:
    """Dropped and looted items whose time is up (spawn items never expire)."""
    return [f.id for f in floor_items if not f.visible_at(now)]


def respawn_items(
    room_id: str,
    spawn_states: Iterable[ItemSpawnSnapshot],
    floor_items: Iterable[FloorItemSnapshot],
    content: ContentRegistry,
    now: datetime,
    rng: random.Random,
) -> ActionResult:
    """Due spawn items of one room come back with the quantity of their rule.

    A spawn item that is still lying there (e.g. created again by a re-seed) is not doubled; the
    due date is cleared either way. Rules removed from the content are only cleared.
    """
    room = content.get_room(room_id)
    rules = {rule.item_id: rule for rule in room.items}
    lying = {f.item_id for f in floor_items if f.source == "spawn" and f.room_id == room_id}
    builder = ResultBuilder()
    for state in spawn_states:
        if state.room_id != room_id or state.next_spawn_at is None or state.next_spawn_at > now:
            continue
        builder.changes.item_spawns.append(
            ItemSpawnUpdate(room_id=room_id, item_id=state.item_id, next_spawn_at=None)
        )
        rule = rules.get(state.item_id)
        if rule is None or state.item_id in lying:
            continue
        item = content.get_item(rule.item_id)
        floor_id = new_id(rng)
        builder.changes.floor_items_create.append(
            FloorItemCreate(
                id=floor_id,
                room_id=room_id,
                item_id=item.id,
                quantity=rule.quantity,
                source="spawn",
            )
        )
        builder.event(
            "floor.spawned",
            RoomAudience(room_id=room_id),
            f"Hier liegt jetzt {item.name}."
            if rule.quantity == 1
            else (f"Hier liegen jetzt {rule.quantity}x {item.name}."),
            floor_item_id=str(floor_id),
            item_id=item.id,
        )
    return builder.build()
