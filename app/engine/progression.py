from __future__ import annotations

from dataclasses import dataclass

from app.content.schemas import ClassTemplate, ItemTemplate, LevelRule, Rules
from app.engine.classes import base_stats
from app.engine.events import ResultBuilder
from app.engine.state import PlayerSnapshot
from app.engine.stats import stats_of


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
    player_class: ClassTemplate | None = None,
) -> LevelUpResult:
    """Checks if player qualifies for level ups and determines new base stats (the level
    table's, scaled by the class)."""
    levels_sorted = sorted(rules.levels, key=lambda rule: rule.level)
    achieved_level_rule: LevelRule | None = None

    for rule in levels_sorted:
        if current_xp >= rule.xp_required and rule.level > current_level:
            achieved_level_rule = rule

    if achieved_level_rule is not None and achieved_level_rule.level > current_level:
        stats = base_stats(achieved_level_rule, player_class)
        return LevelUpResult(
            new_level=achieved_level_rule.level,
            leveled_up=True,
            new_base_max_hp=stats.max_hp,
            new_base_attack=stats.attack,
            new_base_defense=stats.defense,
        )

    return LevelUpResult(
        new_level=current_level,
        leveled_up=False,
        new_base_max_hp=base_max_hp,
        new_base_attack=base_attack,
        new_base_defense=base_defense,
    )


def apply_xp(
    builder: ResultBuilder,
    player: PlayerSnapshot,
    amount: int,
    rules: Rules,
    item_templates: dict[str, ItemTemplate],
    player_class: ClassTemplate | None = None,
) -> None:
    """Grants XP (mutates ``player``), applies level-ups and reports both to the player.

    A level-up fully heals the player to the new effective max HP (incl. equipment); the new
    base stats are the level table's scaled by ``player_class``.
    """
    if amount <= 0:
        return
    player.xp += amount
    progression = calculate_level_progression(
        player.xp,
        player.level,
        player.base_max_hp,
        player.base_attack,
        player.base_defense,
        rules,
        player_class,
    )
    builder.to_player(player.id, "xp.gained", f"Du erhältst {amount} Erfahrung.", amount=amount)
    builder.update_player(player.id, xp=player.xp)
    if not progression.leveled_up:
        return

    player.level = progression.new_level
    player.base_max_hp = progression.new_base_max_hp
    player.base_attack = progression.new_base_attack
    player.base_defense = progression.new_base_defense
    player.hp = stats_of(player, item_templates).max_hp
    builder.update_player(
        player.id,
        hp=player.hp,
        level=player.level,
        base_max_hp=player.base_max_hp,
        base_attack=player.base_attack,
        base_defense=player.base_defense,
    )
    builder.to_player(
        player.id, "player.level_up", f"Du hast Stufe {player.level} erreicht!", level=player.level
    )
