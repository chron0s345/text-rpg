"""Balancing guard rails for the high-level content (levels 6–10), computed from the real data.

A fight is estimated without randomness: the player strikes first, the monster strikes back
after every hit it survives, damage is ``attack - defense`` (at least ``combat.min_damage``).
The player has the level of the monster and wears, per slot, the best item worth at most
``GEAR_BUDGET`` gold: what one can afford after a few fights in the new areas.

- Normal monsters can be beaten alone.
- Bosses cannot (a group or potions are needed), but a full group can.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.content.loader import load_content
from app.content.registry import ContentRegistry
from app.content.schemas import MonsterTemplate

GEAR_BUDGET = 100
BOSSES = {"spider_queen", "ice_drake"}
HIGH_LEVEL = 6

CONTENT = load_content(Path(__file__).resolve().parents[2] / "data")


def gear_bonus(content: ContentRegistry, stat: str) -> int:
    best: dict[str, int] = {}
    for item in content.items.values():
        if item.slot is None or item.slot == "bag" or item.value > GEAR_BUDGET:
            continue
        amount = sum(e.amount for e in item.effects if e.type == "stat" and e.stat == stat)
        best[item.slot] = max(best.get(item.slot, 0), amount)
    return sum(best.values())


def damage_taken(monster: MonsterTemplate) -> tuple[int, int]:
    """(damage the player takes until the monster dies, the player's max HP)."""
    rules = CONTENT.rules
    level = rules.level_rule(monster.level)
    attack = level.attack + gear_bonus(CONTENT, "attack")
    defense = level.defense + gear_bonus(CONTENT, "defense")
    minimum = rules.combat.min_damage
    hits_needed = -(-monster.max_hp // max(minimum, attack - monster.defense))
    per_hit = max(minimum, monster.attack - defense)
    return (hits_needed - 1) * per_hit, level.max_hp


HIGH_LEVEL_MONSTERS = sorted(
    (m for m in CONTENT.monsters.values() if m.level >= HIGH_LEVEL), key=lambda m: m.id
)


def test_the_high_level_content_exists() -> None:
    assert len(HIGH_LEVEL_MONSTERS) >= 5
    assert BOSSES <= {m.id for m in HIGH_LEVEL_MONSTERS}
    assert max(rule.level for rule in CONTENT.rules.levels) >= max(
        m.level for m in HIGH_LEVEL_MONSTERS
    )


@pytest.mark.parametrize("monster", HIGH_LEVEL_MONSTERS, ids=lambda m: m.id)
def test_monsters_can_be_beaten_and_bosses_need_help(monster: MonsterTemplate) -> None:
    taken, max_hp = damage_taken(monster)
    if monster.id in BOSSES:
        assert max_hp < taken < CONTENT.rules.max_group_size * max_hp
    else:
        assert taken < max_hp
        assert taken > 0, "a monster that cannot hurt its own level is no threat"
