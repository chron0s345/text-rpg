from __future__ import annotations

import random
from dataclasses import dataclass
from uuid import UUID

from app.content.schemas import MonsterTemplate


@dataclass
class RolledLootItem:
    item_id: str
    quantity: int


def roll_monster_loot(
    monster: MonsterTemplate,
    rng: random.Random,
) -> list[RolledLootItem]:
    """Rolls drops according to monster loot table chances."""
    results: list[RolledLootItem] = []
    for entry in monster.loot_table:
        if rng.random() < entry.chance:
            qty = rng.randint(entry.min_qty, entry.max_qty)
            if qty > 0:
                results.append(RolledLootItem(item_id=entry.item_id, quantity=qty))
    return results


def roll_monster_gold(
    monster: MonsterTemplate,
    rng: random.Random,
) -> int:
    """Rolls gold drop within min..max reward bounds."""
    min_g, max_g = monster.gold_reward
    if max_g <= 0:
        return 0
    return rng.randint(min_g, max_g)


def distribute_gold(
    total_gold: int,
    recipient_ids: list[UUID],
    cursor: int = 0,
) -> dict[UUID, int]:
    """Distributes gold evenly among room party members.

    Any remaining coins are distributed round-robin starting at cursor.
    """
    if total_gold <= 0 or not recipient_ids:
        return {pid: 0 for pid in recipient_ids}

    count = len(recipient_ids)
    base_share = total_gold // count
    remainder = total_gold % count

    distribution: dict[UUID, int] = {pid: base_share for pid in recipient_ids}

    for i in range(remainder):
        target_index = (cursor + i) % count
        target_id = recipient_ids[target_index]
        distribution[target_id] += 1

    return distribution


def distribute_xp(
    total_xp: int,
    damage_by_player: dict[UUID, int],
) -> dict[UUID, int]:
    """Distributes XP proportionally based on damage dealt by each participant."""
    if total_xp <= 0 or not damage_by_player:
        return {}

    total_damage = sum(damage_by_player.values())
    if total_damage <= 0:
        # Equal split if 0 damage recorded (fallback)
        per_player = total_xp // len(damage_by_player)
        return {pid: per_player for pid in damage_by_player}

    result: dict[UUID, int] = {}
    for pid, dmg in damage_by_player.items():
        share = int(total_xp * (dmg / total_damage))
        result[pid] = share

    return result
