"""World upkeep decisions: spawn items, expired floor items, ticker respawns, grace periods."""

import random
from datetime import timedelta
from uuid import uuid4

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.cleanup import ItemSpawnSnapshot, expired_floor_items, respawn_items
from app.engine.presence import connection_expired
from app.engine.respawn import respawn_monsters
from app.engine.state import FloorItemSnapshot
from tests.engine.factories import NOW, make_monster

CAMP = "goblin_camp"  # spawns 2x bread (respawn_seconds: 120)


def floor(source: str = "drop", item_id: str = "bread", **fields: object) -> FloorItemSnapshot:
    data = {"id": uuid4(), "room_id": CAMP, "item_id": item_id, "quantity": 1, "source": source}
    data.update(fields)
    return FloorItemSnapshot.model_validate(data)


def test_due_spawn_item_comes_back_with_rule_quantity(content: ContentRegistry) -> None:
    due = ItemSpawnSnapshot(CAMP, "bread", NOW - timedelta(seconds=1))
    result = respawn_items(CAMP, [due], [], content, NOW, random.Random(1))
    [created] = result.changes.floor_items_create
    assert (created.room_id, created.item_id, created.quantity, created.source) == (
        CAMP,
        "bread",
        2,
        "spawn",
    )
    assert created.expires_at is None  # spawn items never expire
    [spawn] = result.changes.item_spawns
    assert spawn.next_spawn_at is None
    [event] = result.events
    assert (event.kind, event.audience.kind) == ("floor.spawned", "room")
    assert event.text == "Hier liegen jetzt 2x Frisches Brot."


def test_spawn_item_not_due_or_already_lying(content: ContentRegistry) -> None:
    later = ItemSpawnSnapshot(CAMP, "bread", NOW + timedelta(seconds=1))
    idle = ItemSpawnSnapshot(CAMP, "bread", None)
    assert respawn_items(CAMP, [later, idle], [], content, NOW, random.Random(1)).changes == (
        respawn_items(CAMP, [], [], content, NOW, random.Random(1)).changes
    )

    due = ItemSpawnSnapshot(CAMP, "bread", NOW)
    lying = floor(source="spawn")
    result = respawn_items(CAMP, [due], [lying], content, NOW, random.Random(1))
    assert result.changes.floor_items_create == []  # never doubled
    assert [s.next_spawn_at for s in result.changes.item_spawns] == [None]


def test_spawn_rule_removed_from_content_is_only_cleared(content: ContentRegistry) -> None:
    due = ItemSpawnSnapshot(CAMP, "healing_potion", NOW)  # no such rule in this room
    result = respawn_items(CAMP, [due], [], content, NOW, random.Random(1))
    assert result.changes.floor_items_create == []
    assert len(result.changes.item_spawns) == 1


def test_expired_floor_items() -> None:
    old = floor(expires_at=NOW - timedelta(seconds=1))
    exact = floor(expires_at=NOW)
    fresh = floor(expires_at=NOW + timedelta(seconds=1))
    spawn = floor(source="spawn")
    assert set(expired_floor_items([old, exact, fresh, spawn], NOW)) == {old.id, exact.id}


def test_ticker_respawn_tells_only_the_room(content: ContentRegistry) -> None:
    dead = make_monster(hp=0, alive=False, respawn_at=NOW)
    waiting = make_monster(hp=0, alive=False, respawn_at=NOW + timedelta(seconds=5))
    result = respawn_monsters([dead, waiting], content, NOW, random.Random(1))
    assert [m.monster_instance_id for m in result.changes.monsters] == [dead.id]
    [event] = result.events
    assert (event.audience.kind, event.exclude_player_ids) == ("room", [])


def test_connection_expired(rules: Rules) -> None:
    grace = timedelta(seconds=rules.timings.disconnect_grace_seconds)
    assert not connection_expired(None, NOW, rules)
    assert not connection_expired(NOW - grace + timedelta(seconds=1), NOW, rules)
    assert connection_expired(NOW - grace, NOW, rules)
