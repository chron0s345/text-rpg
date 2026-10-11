import random
from datetime import timedelta
from typing import Any
from uuid import UUID

from app.content.registry import ContentRegistry
from app.engine.events import RoomAudience, StateChanges
from app.engine.respawn import respawn_due_items, respawn_due_monsters, respawn_monsters
from app.engine.state import ActionSnapshot, ItemSpawnSnapshot
from tests.engine.factories import NOW, actor_texts, make_monster, make_player, monster_update


def test_due_monster_comes_back_fresh(content: ContentRegistry) -> None:
    actor = make_player()
    due = make_monster(
        hp=0,
        alive=False,
        respawn_at=NOW,
        last_combat_at=NOW - timedelta(minutes=1),
        tagged_by_party="solo:x",
    )
    snapshot = ActionSnapshot(actor=actor, monsters=[due])

    result = respawn_due_monsters(snapshot, content, NOW)

    assert monster_update(result, due.id) == {
        "monster_instance_id": due.id,
        "alive": True,
        "hp": 10,
        "respawn_at": None,
        "last_combat_at": None,
        "tagged_by_party": None,
    }
    assert due.alive and due.hp == 10  # the snapshot is updated for the following action
    assert actor_texts(result) == ["Übungspuppe taucht auf."]
    [room] = [e for e in result.events if e.audience.kind == "room"]
    assert room.exclude_player_ids == [actor.id]


def test_not_yet_due_or_alive_monsters_are_untouched(content: ContentRegistry) -> None:
    later = make_monster(hp=0, alive=False, respawn_at=NOW + timedelta(seconds=1))
    alive = make_monster()
    result = respawn_due_monsters(
        ActionSnapshot(actor=make_player(), monsters=[later, alive]), content, NOW
    )
    assert result.changes.monsters == []
    assert result.events == []


def test_ticker_respawn_has_no_actor(content: ContentRegistry) -> None:
    due = make_monster(hp=0, alive=False, respawn_at=NOW - timedelta(seconds=1))

    result = respawn_monsters([due], content, NOW)

    assert monster_update(result, due.id)["alive"] is True
    [event] = result.events
    assert (event.audience.kind, event.exclude_player_ids) == ("room", [])
    assert event.text == "Übungspuppe taucht auf."


# --- spawn items ---

CAMP = "goblin_camp"  # content rule: 2x bread, respawn after 120 s


def spawn(room: str = CAMP, item: str = "bread", **fields: Any) -> ItemSpawnSnapshot:
    return ItemSpawnSnapshot(room_id=room, item_id=item, **fields)


def test_due_item_spawn_reappears_and_clears_its_timer(content: ContentRegistry) -> None:
    result = respawn_due_items([spawn(next_spawn_at=NOW)], set(), content, NOW, random.Random(1))

    [created] = result.changes.floor_items_create
    assert (created.room_id, created.item_id, created.quantity, created.source) == (
        CAMP,
        "bread",
        2,
        "spawn",
    )
    assert created.expires_at is None  # spawn items never expire
    assert [s.model_dump() for s in result.changes.item_spawns] == [
        {"room_id": CAMP, "item_id": "bread", "next_spawn_at": None}
    ]
    [event] = result.events
    assert (event.kind, event.audience, event.text) == (
        "floor.spawned",
        RoomAudience(room_id=CAMP),
        "2x Frisches Brot liegt jetzt hier.",
    )
    assert event.data["floor_item_id"] == str(created.id)


def test_item_spawn_ids_are_deterministic(content: ContentRegistry) -> None:
    def created_id() -> UUID:
        result = respawn_due_items(
            [spawn(next_spawn_at=NOW)], set(), content, NOW, random.Random(5)
        )
        return result.changes.floor_items_create[0].id

    assert created_id() == created_id()


def test_item_spawns_not_due_are_untouched(content: ContentRegistry) -> None:
    result = respawn_due_items(
        [spawn(next_spawn_at=NOW + timedelta(seconds=1)), spawn(item="other")],
        set(),
        content,
        NOW,
        random.Random(1),
    )
    assert result.changes == StateChanges()
    assert result.events == []


def test_item_still_lying_is_not_spawned_twice(content: ContentRegistry) -> None:
    result = respawn_due_items(
        [spawn(next_spawn_at=NOW)], {(CAMP, "bread")}, content, NOW, random.Random(1)
    )
    assert result.changes.floor_items_create == []
    assert [s.next_spawn_at for s in result.changes.item_spawns] == [None]
    assert result.events == []


def test_spawn_rule_removed_from_content_only_clears_the_timer(
    content: ContentRegistry,
) -> None:
    result = respawn_due_items(
        [spawn(item="healing_potion", next_spawn_at=NOW), spawn(room="gone", next_spawn_at=NOW)],
        set(),
        content,
        NOW,
        random.Random(1),
    )
    assert result.changes.floor_items_create == []
    assert len(result.changes.item_spawns) == 2
