from __future__ import annotations

import random
from dataclasses import dataclass
from uuid import UUID

from app.content.schemas import MonsterTemplate


@dataclass(frozen=True)
class AmbushCheckResult:
    ambushed: bool
    ambushing_monster_instance_id: UUID | None
    ambushing_monster_template_id: str | None


def check_enter_ambush(
    player_level: int,
    is_safe_room: bool,
    available_monsters: list[tuple[UUID, MonsterTemplate]],  # list of (instance_id, template)
    rng: random.Random,
) -> AmbushCheckResult:
    """Checks if any non-combat monster ambushes the player upon room entry."""
    if is_safe_room or not available_monsters:
        return AmbushCheckResult(False, None, None)

    for inst_id, tmpl in available_monsters:
        if tmpl.ambush is None:
            continue

        if tmpl.ambush.max_level_difference is not None:
            # Monster doesn't attack players too far above its own level
            if player_level - tmpl.level > tmpl.ambush.max_level_difference:
                continue

        if rng.random() < tmpl.ambush.chance_on_enter:
            return AmbushCheckResult(
                ambushed=True,
                ambushing_monster_instance_id=inst_id,
                ambushing_monster_template_id=tmpl.id,
            )

    return AmbushCheckResult(False, None, None)
