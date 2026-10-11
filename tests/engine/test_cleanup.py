from datetime import timedelta
from typing import Any
from uuid import uuid4

from app.content.registry import ContentRegistry
from app.engine.cleanup import expire_floor_items
from app.engine.state import FloorItemSnapshot
from tests.engine.factories import NOW, ROOM


def floor(**fields: Any) -> FloorItemSnapshot:
    data: dict[str, Any] = {
        "id": uuid4(),
        "room_id": ROOM,
        "item_id": "goblin_ear",
        "quantity": 3,
        "source": "drop",
        "expires_at": NOW,
    }
    return FloorItemSnapshot.model_validate({**data, **fields})


def test_expired_items_are_deleted_with_a_room_event(content: ContentRegistry) -> None:
    expired = floor()

    result = expire_floor_items([expired], content, NOW)

    assert result.changes.floor_items_delete == [expired.id]
    [event] = result.events
    assert (event.kind, event.audience.kind, event.text) == (
        "floor.expired",
        "room",
        "3x Goblinohr zerfällt zu Staub.",
    )
    assert event.data == {"floor_item_id": str(expired.id)}


def test_items_not_yet_expired_and_spawn_items_stay(content: ContentRegistry) -> None:
    later = floor(expires_at=NOW + timedelta(seconds=1), source="loot")
    spawned = floor(source="spawn", expires_at=None)

    result = expire_floor_items([later, spawned], content, NOW)

    assert result.changes.floor_items_delete == []
    assert result.events == []
