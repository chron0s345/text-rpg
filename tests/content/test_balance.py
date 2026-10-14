"""Balancing guard rails for all monsters, computed from the real data.

A fight is estimated without randomness: the player strikes first, the monster strikes back
after every hit it survives, damage is ``attack - defense`` (at least ``combat.min_damage``).
The player has the level of the monster and wears, per slot, the best item worth at most the
gear budget of the monster's tier: what one can afford by the time one gets there.

- Normal monsters can be beaten alone.
- Bosses cannot (a group or potions are needed), but a full group can.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.content.loader import load_content
from app.content.schemas import MonsterTemplate

# (lowest level of the tier, gear budget per item in gold)
TIERS = [(1, 0), (6, 100), (11, 300)]
BOSSES = {
    "cave_troll",
    "grave_lord",
    "sea_witch",
    "corrupted_warden",
    "spider_queen",
    "ice_drake",
    "ignaros",
    "kraken",
    "morvath",
}

CONTENT = load_content(Path(__file__).resolve().parents[2] / "data")


def gear_budget(level: int) -> int:
    return max(budget for lowest, budget in TIERS if level >= lowest)


def gear_bonus(stat: str, budget: int) -> int:
    """Per slot the best value of ``stat`` among affordable items (a slight overestimate when
    the best attack and the best defense of a slot sit on different items)."""
    best: dict[str, int] = {}
    for item in CONTENT.items.values():
        if item.slot is None or item.slot == "bag" or item.value > budget:
            continue
        amount = sum(e.amount for e in item.effects if e.type == "stat" and e.stat == stat)
        best[item.slot] = max(best.get(item.slot, 0), amount)
    return sum(best.values())


def damage_taken(monster: MonsterTemplate) -> tuple[int, int]:
    """(damage the player takes until the monster dies, the player's max HP)."""
    rules = CONTENT.rules
    level = rules.level_rule(monster.level)
    budget = gear_budget(monster.level)
    attack = level.attack + gear_bonus("attack", budget)
    defense = level.defense + gear_bonus("defense", budget)
    minimum = rules.combat.min_damage
    hits_needed = -(-monster.max_hp // max(minimum, attack - monster.defense))
    per_hit = max(minimum, monster.attack - defense)
    return (hits_needed - 1) * per_hit, level.max_hp + gear_bonus("max_hp", budget)


MONSTERS = sorted(CONTENT.monsters.values(), key=lambda m: (m.level, m.id))


def test_every_level_has_monsters_and_bosses_exist() -> None:
    top = max(rule.level for rule in CONTENT.rules.levels)
    assert {m.level for m in MONSTERS} == set(range(1, top + 1))
    assert BOSSES <= set(CONTENT.monsters)


@pytest.mark.parametrize("monster", MONSTERS, ids=lambda m: m.id)
def test_monsters_can_be_beaten_and_bosses_need_help(monster: MonsterTemplate) -> None:
    taken, max_hp = damage_taken(monster)
    if monster.id in BOSSES:
        assert max_hp < taken < CONTENT.rules.max_group_size * max_hp
    else:
        assert taken < max_hp
        assert taken > 0, "a monster that cannot hurt its own level is no threat"


def test_rare_variants_are_worth_the_luck() -> None:
    """A rare form is stronger than its base and rewards more: more XP and guaranteed loot."""
    variants = [m for m in CONTENT.monsters.values() if m.rare_variant is not None]
    assert len(variants) >= 5
    for base in variants:
        assert base.rare_variant is not None
        rare = CONTENT.monsters[base.rare_variant.monster_id]
        assert rare.max_hp > base.max_hp, rare.id
        assert rare.xp_reward > base.xp_reward, rare.id
        assert any(entry.chance == 1.0 for entry in rare.loot_table), rare.id
        assert base.rare_variant.chance <= 0.1, "rare means rare"
