"""Builders and result accessors for engine tests."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from app.engine.events import ActionResult
from app.engine.state import MonsterSnapshot, PlayerSnapshot

NOW = datetime(2026, 1, 1, 12, 0, 0)
ROOM = "dark_forest"


def make_player(
    name: str = "Anna",
    *,
    group_id: UUID | None = None,
    hp: int = 20,
    gold: int = 0,
    xp: int = 0,
    room: str = ROOM,
) -> PlayerSnapshot:
    return PlayerSnapshot(
        id=uuid4(),
        account_id=uuid4(),
        name=name,
        hp=hp,
        base_max_hp=20,
        base_attack=5,
        base_defense=2,
        xp=xp,
        level=1,
        gold=gold,
        current_room_id=room,
        checkpoint_room_id="town_square",
        state="exploring",
        group_id=group_id,
    )


def make_monster(monster_id: str = "dummy", hp: int = 10, **fields: Any) -> MonsterSnapshot:
    return MonsterSnapshot.model_validate(
        {
            "id": uuid4(),
            "monster_id": monster_id,
            "room_id": ROOM,
            "hp": hp,
            "alive": True,
            **fields,
        }
    )


def fight(player: PlayerSnapshot, monster: MonsterSnapshot) -> None:
    player.state = "in_combat"
    player.combat_target_id = monster.id


def player_update(result: ActionResult, player_id: UUID) -> dict[str, Any]:
    return next(
        u.model_dump(exclude_unset=True) for u in result.changes.players if u.player_id == player_id
    )


def monster_update(result: ActionResult, monster_id: UUID) -> dict[str, Any]:
    return next(
        u.model_dump(exclude_unset=True)
        for u in result.changes.monsters
        if u.monster_instance_id == monster_id
    )


def actor_texts(result: ActionResult) -> list[str]:
    return [e.text for e in result.events if e.audience.kind == "actor"]
