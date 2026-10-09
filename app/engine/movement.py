from __future__ import annotations

import random
from dataclasses import dataclass
from uuid import UUID

from app.content.schemas import Direction, MonsterTemplate, RoomTemplate
from app.engine.ambush import check_enter_ambush


@dataclass(frozen=True)
class MoveResult:
    success: bool
    error_message: str | None
    new_room_id: str | None
    checkpoint_updated: bool
    ambushed: bool
    ambushing_monster_instance_id: UUID | None


def resolve_movement(
    current_room: RoomTemplate,
    direction: Direction,
    destination_room: RoomTemplate | None,
    current_checkpoint_room_id: str,
    player_level: int,
    room_monsters: list[tuple[UUID, MonsterTemplate]],
    rng: random.Random,
) -> MoveResult:
    """Calculates room transition, checkpoint updates, and entry ambush checks.

    Pure function adhering to ARCHITECTURE.md specs.
    """
    if direction not in current_room.exits:
        return MoveResult(
            success=False,
            error_message=f"Es gibt keinen Weg nach {direction}.",
            new_room_id=None,
            checkpoint_updated=False,
            ambushed=False,
            ambushing_monster_instance_id=None,
        )

    exit_target_id = current_room.exits[direction]
    if destination_room is None or destination_room.id != exit_target_id:
        return MoveResult(
            success=False,
            error_message="Zielraum nicht gefunden.",
            new_room_id=None,
            checkpoint_updated=False,
            ambushed=False,
            ambushing_monster_instance_id=None,
        )

    # Checkpoint logic
    checkpoint_updated = False
    if destination_room.checkpoint and destination_room.id != current_checkpoint_room_id:
        checkpoint_updated = True

    # Ambush logic on entry
    ambush_result = check_enter_ambush(
        player_level=player_level,
        is_safe_room=destination_room.safe,
        available_monsters=room_monsters,
        rng=rng,
    )

    return MoveResult(
        success=True,
        error_message=None,
        new_room_id=destination_room.id,
        checkpoint_updated=checkpoint_updated,
        ambushed=ambush_result.ambushed,
        ambushing_monster_instance_id=ambush_result.ambushing_monster_instance_id,
    )
