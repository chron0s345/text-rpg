"""Skill cooldowns (engine/skills.py), counted in the character's combat rounds.

Every round of the character in a fight (an attack, a skill, a potion, a flee attempt) counts
one down; a fresh fight starts with every skill ready.
"""

from __future__ import annotations

from app.engine.events import ResultBuilder
from app.engine.state import PlayerSnapshot


def tick_cooldowns(builder: ResultBuilder, player: PlayerSnapshot) -> None:
    if not player.skill_cooldowns:
        return
    player.skill_cooldowns = {s: n - 1 for s, n in player.skill_cooldowns.items() if n > 1}
    builder.update_player(player.id, skill_cooldowns=player.skill_cooldowns)


def reset_cooldowns(builder: ResultBuilder, player: PlayerSnapshot) -> None:
    """A fresh fight: every skill ready, no taunt left over from the last one."""
    if player.skill_cooldowns:
        player.skill_cooldowns = {}
        builder.update_player(player.id, skill_cooldowns={})
    if player.guarding:
        player.guarding = 0
        builder.update_player(player.id, guarding=0)
