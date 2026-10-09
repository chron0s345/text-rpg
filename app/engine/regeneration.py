from __future__ import annotations

import math
from datetime import datetime

from app.content.schemas import MonsterTemplate


def calculate_current_monster_hp(
    monster: MonsterTemplate,
    stored_hp: int,
    last_combat_at: datetime | None,
    now: datetime,
) -> int:
    """Computes the lazy monster HP regeneration outside of combat.

    Formula from ARCHITECTURE.md:
    min(max_hp, hp + regen.hp_per_second * max(0, now - last_combat_at - delay))
    """
    if stored_hp <= 0:
        return 0

    if monster.regen is None or last_combat_at is None:
        return min(monster.max_hp, stored_hp)

    elapsed_seconds = (now - last_combat_at).total_seconds()
    if elapsed_seconds <= 0:
        return min(monster.max_hp, stored_hp)

    effective_regen_time = max(0.0, elapsed_seconds - monster.regen.delay_seconds)
    if effective_regen_time <= 0:
        return min(monster.max_hp, stored_hp)

    hp_gain = math.floor(effective_regen_time * monster.regen.hp_per_second)
    return min(monster.max_hp, stored_hp + hp_gain)
