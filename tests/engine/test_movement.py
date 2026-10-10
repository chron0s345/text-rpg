import random
from uuid import uuid4

from app.content.schemas import Ambush, MonsterTemplate, RoomTemplate
from app.engine.movement import resolve_movement


def test_movement_valid_exit() -> None:
    rng = random.Random(42)
    room_a = RoomTemplate(
        id="room_a",
        name="Raum A",
        description="",
        exits={"north": "room_b"},
    )
    room_b = RoomTemplate(
        id="room_b",
        name="Raum B",
        description="",
        checkpoint=True,
    )

    res = resolve_movement(
        current_room=room_a,
        direction="north",
        destination_room=room_b,
        current_checkpoint_room_id="room_a",
        player_level=1,
        room_monsters=[],
        rng=rng,
    )
    assert res.success is True
    assert res.new_room_id == "room_b"
    assert res.checkpoint_updated is True
    assert res.ambushed is False


def test_movement_invalid_direction() -> None:
    rng = random.Random(42)
    room_a = RoomTemplate(
        id="room_a",
        name="Raum A",
        description="",
        exits={"north": "room_b"},
    )

    res = resolve_movement(
        current_room=room_a,
        direction="south",  # No south exit
        destination_room=None,
        current_checkpoint_room_id="room_a",
        player_level=1,
        room_monsters=[],
        rng=rng,
    )
    assert res.success is False
    assert res.error_message is not None
    assert "keinen Weg" in res.error_message


def test_movement_with_ambush() -> None:
    rng = random.Random(42)
    room_a = RoomTemplate(
        id="room_a",
        name="Raum A",
        description="",
        exits={"east": "forest"},
    )
    forest = RoomTemplate(
        id="forest",
        name="Wald",
        description="",
        safe=False,
    )
    monster_inst_id = uuid4()
    monster = MonsterTemplate(
        id="goblin",
        name="Goblin",
        max_hp=10,
        attack=4,
        defense=1,
        xp_reward=10,
        respawn_seconds=30,
        ambush=Ambush(chance_on_enter=1.0, chance_per_tick=0.5),
    )

    res = resolve_movement(
        current_room=room_a,
        direction="east",
        destination_room=forest,
        current_checkpoint_room_id="room_a",
        player_level=1,
        room_monsters=[(monster_inst_id, monster)],
        rng=rng,
    )
    assert res.success is True
    assert res.ambushed is True
    assert res.ambushing_monster_instance_id == monster_inst_id
