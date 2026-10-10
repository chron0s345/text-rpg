from __future__ import annotations

import math
import random
from collections.abc import Mapping
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


def distribute_xp(total_xp: int, weights: Mapping[UUID, float]) -> dict[UUID, int]:
    """Splits ``total_xp`` proportionally to ``weights`` without losing points to rounding.

    Largest-remainder method: everyone gets the floor of their share, leftover points go to the
    largest fractional remainders (ties: insertion order). Non-positive weights get nothing.
    """
    positive = {pid: w for pid, w in weights.items() if w > 0}
    if total_xp <= 0 or not positive:
        return {pid: 0 for pid in weights}

    total_weight = sum(positive.values())
    exact = {pid: total_xp * w / total_weight for pid, w in positive.items()}
    result = {pid: 0 for pid in weights}
    for pid, share in exact.items():
        result[pid] = math.floor(share)

    leftover = total_xp - sum(result.values())
    by_remainder = sorted(exact, key=lambda pid: exact[pid] - math.floor(exact[pid]), reverse=True)
    for pid in by_remainder[:leftover]:
        result[pid] += 1
    return result
