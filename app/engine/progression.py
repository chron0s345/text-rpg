from __future__ import annotations

from dataclasses import dataclass

from app.content.schemas import LevelRule, Rules


@dataclass(frozen=True)
class LevelUpResult:
    new_level: int
    leveled_up: bool
    new_base_max_hp: int
    new_base_attack: int
    new_base_defense: int


def calculate_level_progression(
    current_xp: int,
    current_level: int,
    base_max_hp: int,
    base_attack: int,
    base_defense: int,
    rules: Rules,
) -> LevelUpResult:
    """Checks if player qualifies for level ups and determines new base stats."""
    levels_sorted = sorted(rules.levels, key=lambda rule: rule.level)
    achieved_level_rule: LevelRule | None = None

    for rule in levels_sorted:
        if current_xp >= rule.xp_required and rule.level > current_level:
            achieved_level_rule = rule

    if achieved_level_rule is not None and achieved_level_rule.level > current_level:
        return LevelUpResult(
            new_level=achieved_level_rule.level,
            leveled_up=True,
            new_base_max_hp=achieved_level_rule.max_hp,
            new_base_attack=achieved_level_rule.attack,
            new_base_defense=achieved_level_rule.defense,
        )

    return LevelUpResult(
        new_level=current_level,
        leveled_up=False,
        new_base_max_hp=base_max_hp,
        new_base_attack=base_attack,
        new_base_defense=base_defense,
    )
