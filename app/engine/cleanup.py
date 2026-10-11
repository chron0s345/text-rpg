"""Expired floor items (ARCHITECTURE.md, "Respawn & Aufräumen").

Dropped and looted items get ``expires_at`` and are deleted by the world ticker afterwards.
Displays filter them out immediately (``FloorItemSnapshot.visible_at``), even before the ticker
runs; the deletion sends a room event so open room views drop the item.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.content.registry import ContentRegistry
from app.engine.events import ActionResult, ResultBuilder, RoomAudience
from app.engine.inventory import item_label
from app.engine.state import FloorItemSnapshot


def expire_floor_items(
    floor_items: Sequence[FloorItemSnapshot], content: ContentRegistry, now: datetime
) -> ActionResult:
    """Deletes every floor item whose ``expires_at`` has passed; spawn items never expire."""
    builder = ResultBuilder()
    for floor in floor_items:
        if floor.visible_at(now):
            continue
        builder.changes.floor_items_delete.append(floor.id)
        builder.event(
            "floor.expired",
            RoomAudience(room_id=floor.room_id),
            f"{item_label(floor.quantity, content.get_item(floor.item_id))} zerfällt zu Staub.",
            floor_item_id=str(floor.id),
        )
    return builder.build()
