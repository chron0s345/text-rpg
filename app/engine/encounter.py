"""Building blocks shared by combat, flee and ambush: damage, combat contact, monster strikes."""

from __future__ import annotations

import random
from datetime import datetime

from app.content.registry import ContentRegistry
from app.content.schemas import CombatRules, MonsterTemplate, Rules
from app.engine.death import apply_player_death
from app.engine.events import ResultBuilder, RoomAudience
from app.engine.group_status import report_hp
from app.engine.regeneration import calculate_current_monster_hp
from app.engine.state import ActionSnapshot, MonsterSnapshot, PlayerSnapshot
from app.engine.stats import stats_of


def calculate_damage(attack: int, defense: int, combat: CombatRules, rng: random.Random) -> int:
    """``attack - defense ± damage_variance``, never below ``min_damage``."""
    variance = rng.randint(-combat.damage_variance, combat.damage_variance)
    return max(combat.min_damage, attack - defense + variance)


def touch_monster(
    builder: ResultBuilder, monster: MonsterSnapshot, template: MonsterTemplate, now: datetime
) -> None:
    """Combat contact: apply lazy regeneration, then reset the regeneration clock."""
    monster.hp = calculate_current_monster_hp(template, monster.hp, monster.last_combat_at, now)
    monster.last_combat_at = now
    builder.update_monster(monster.id, hp=monster.hp, last_combat_at=now)


def enter_combat(builder: ResultBuilder, player: PlayerSnapshot, monster: MonsterSnapshot) -> None:
    player.state = "in_combat"
    player.combat_target_id = monster.id
    builder.update_player(player.id, state="in_combat", combat_target_id=monster.id)


def leave_combat(builder: ResultBuilder, player: PlayerSnapshot) -> None:
    player.state = "exploring"
    player.combat_target_id = None
    builder.update_player(player.id, state="exploring", combat_target_id=None)


def monster_strikes(
    builder: ResultBuilder,
    snapshot: ActionSnapshot,
    player: PlayerSnapshot,
    template: MonsterTemplate,
    content: ContentRegistry,
    rules: Rules,
    rng: random.Random,
) -> None:
    """The monster hits ``player``; HP <= 0 kills the player (with loss rolls)."""
    damage = calculate_damage(
        template.attack, stats_of(player, content.items).defense, rules.combat, rng
    )
    player.hp = max(0, player.hp - damage)
    builder.update_player(player.id, hp=player.hp)
    builder.to_player(
        player.id,
        "combat.hit_taken",
        f"{template.name} trifft dich für {damage} Schaden.",
        damage=damage,
        hp=player.hp,
    )
    builder.event(
        "combat.hit_taken",
        RoomAudience(room_id=player.current_room_id),
        f"{template.name} trifft {player.name} für {damage} Schaden.",
        exclude=[player.id],
    )
    report_hp(builder, player)
    if player.hp <= 0:
        apply_player_death(builder, snapshot, player, content, rules, rng)
