"""Character classes (data/classes.yaml): the same levels for everyone, other base stats.

A class scales the level table's max HP, attack and defense by its bonus percentages, at
character creation, on every level-up and when an adventurer picks a class.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.content.schemas import ClassTemplate, LevelRule


@dataclass(frozen=True)
class BaseStats:
    max_hp: int
    attack: int
    defense: int


def _scaled(value: int, percent: int) -> int:
    """``value`` plus ``percent`` of it, rounded half up, never below 1."""
    return max(1, (value * (100 + percent) + 50) // 100)


def base_stats(rule: LevelRule, player_class: ClassTemplate | None) -> BaseStats:
    if player_class is None:
        return BaseStats(rule.max_hp, rule.attack, rule.defense)
    bonus = player_class.bonus
    return BaseStats(
        _scaled(rule.max_hp, bonus.max_hp),
        _scaled(rule.attack, bonus.attack),
        _scaled(rule.defense, bonus.defense),
    )


def bonus_text(player_class: ClassTemplate) -> str:
    """ "+15 % Leben · −10 % Angriff · +20 % Verteidigung" (unchanged stats left out)."""
    names = (("max_hp", "Leben"), ("attack", "Angriff"), ("defense", "Verteidigung"))
    parts = []
    for field, label in names:
        percent = getattr(player_class.bonus, field)
        if percent:
            parts.append(f"{'+' if percent > 0 else '−'}{abs(percent)} % {label}")
    return " · ".join(parts) or "ausgeglichen"
