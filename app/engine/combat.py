from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class CombatRoundResult:
    player_damage: int
    monster_damage: int
    new_monster_hp: int
    new_player_hp: int
    monster_died: bool
    player_died: bool


def calculate_damage(attack: int, defense: int, rng: random.Random) -> int:
    """Calculates combat damage with slight RNG variance."""
    base = max(1, attack - defense)
    # Variance of -1..+1 damage if base > 2
    variance = rng.randint(-1, 1) if base > 2 else 0
    return max(1, base + variance)


def resolve_combat_round(
    player_hp: int,
    player_attack: int,
    player_defense: int,
    monster_hp: int,
    monster_attack: int,
    monster_defense: int,
    rng: random.Random,
) -> CombatRoundResult:
    """Simulates a single combat round between a player and a monster.

    1. Player strikes monster first.
    2. If monster dies (hp <= 0), it cannot retaliate.
    3. If monster survives, monster counter-attacks the acting player.
    """
    player_damage = calculate_damage(player_attack, monster_defense, rng)
    new_monster_hp = max(0, monster_hp - player_damage)

    if new_monster_hp <= 0:
        return CombatRoundResult(
            player_damage=player_damage,
            monster_damage=0,
            new_monster_hp=0,
            new_player_hp=player_hp,
            monster_died=True,
            player_died=False,
        )

    # Monster counter-attack
    monster_damage = calculate_damage(monster_attack, player_defense, rng)
    new_player_hp = max(0, player_hp - monster_damage)

    return CombatRoundResult(
        player_damage=player_damage,
        monster_damage=monster_damage,
        new_monster_hp=new_monster_hp,
        new_player_hp=new_player_hp,
        monster_died=False,
        player_died=new_player_hp <= 0,
    )


@dataclass(frozen=True)
class FleeResult:
    success: bool
    damage_taken: int
    new_player_hp: int
    player_died: bool


def resolve_flee(
    player_hp: int,
    player_defense: int,
    monster_attack: int,
    rng: random.Random,
    flee_chance: float = 0.5,
) -> FleeResult:
    """Resolves a player's attempt to flee from active combat.

    If successful, combat ends without retaliation.
    If failed, the monster counter-attacks the fleeing player.
    """
    if rng.random() < flee_chance:
        return FleeResult(
            success=True,
            damage_taken=0,
            new_player_hp=player_hp,
            player_died=False,
        )

    # Monster free counter-attack on failed flee
    damage = calculate_damage(monster_attack, player_defense, rng)
    new_hp = max(0, player_hp - damage)
    return FleeResult(
        success=False,
        damage_taken=damage,
        new_player_hp=new_hp,
        player_died=new_hp <= 0,
    )
