"""Building blocks shared by combat, flee and ambush: damage, combat contact, monster strikes
(with the monster's abilities) and the fight estimate behind "inspect monster"."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime

from app.content.registry import ContentRegistry
from app.content.schemas import CombatRules, CrushingBlow, LifeDrain, MonsterTemplate, Rules
from app.engine.death import apply_player_death
from app.engine.events import ResultBuilder, RoomAudience
from app.engine.group_status import report_hp
from app.engine.regeneration import calculate_current_monster_hp
from app.engine.state import ActionSnapshot, MonsterSnapshot, PlayerSnapshot
from app.engine.stats import stats_of


def crushing_blow(template: MonsterTemplate) -> CrushingBlow | None:
    return next((a for a in template.abilities if isinstance(a, CrushingBlow)), None)


def life_drain(template: MonsterTemplate) -> LifeDrain | None:
    return next((a for a in template.abilities if isinstance(a, LifeDrain)), None)


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
    monster: MonsterSnapshot,
    template: MonsterTemplate,
    content: ContentRegistry,
    rules: Rules,
    rng: random.Random,
) -> None:
    """The monster hits ``player``; HP <= 0 kills the player (with loss rolls).

    Abilities: a crushing blow (rolled per strike) multiplies the damage; life drain heals the
    monster by a share of the damage dealt."""
    damage = calculate_damage(
        template.attack, stats_of(player, content.items).defense, rules.combat, rng
    )
    crush = crushing_blow(template)
    crushed = crush is not None and rng.random() < crush.chance
    if crush is not None and crushed:
        damage = int(damage * crush.multiplier)
    dealt = min(damage, player.hp)
    player.hp -= dealt
    builder.update_player(player.id, hp=player.hp)
    blow = "holt zu einem Wuchtschlag aus und trifft" if crushed else "trifft"
    builder.to_player(
        player.id,
        "combat.hit_taken",
        f"{template.name} {blow} dich für {damage} Schaden{'!' if crushed else '.'}",
        damage=damage,
        hp=player.hp,
        crushing_blow=crushed,
    )
    builder.event(
        "combat.hit_taken",
        RoomAudience(room_id=player.current_room_id),
        f"{template.name} {blow} {player.name} für {damage} Schaden{'!' if crushed else '.'}",
        exclude=[player.id],
    )
    report_hp(builder, player)
    drain = life_drain(template)
    healed = min(template.max_hp - monster.hp, int(dealt * drain.share)) if drain else 0
    if healed > 0:
        monster.hp += healed
        builder.update_monster(monster.id, hp=monster.hp)
        builder.to_player(
            player.id,
            "combat.drain",
            f"{template.name} saugt dir Lebenskraft ab und heilt sich um {healed} HP.",
            healed=healed,
        )
        builder.event(
            "combat.drain",
            RoomAudience(room_id=player.current_room_id),
            f"{template.name} saugt {player.name} Lebenskraft ab und heilt sich um {healed} HP.",
            exclude=[player.id],
            monster_instance_id=str(monster.id),
            monster_hp=monster.hp,
        )
    if player.hp <= 0:
        apply_player_death(builder, snapshot, player, content, rules, rng)


@dataclass(frozen=True)
class FightEstimate:
    """An even fight without variance, the player striking first; crushing blows count with
    their average, life drain with its average heal per strike."""

    my_hit: int
    its_hit: int  # a plain strike, without a crushing blow
    hits: int | None  # the player's hits until the monster falls; None = it heals faster
    taken: int | None  # HP the player loses meanwhile; None as for ``hits``


def estimate_fight(
    attack: int, defense: int, monster_hp: int, template: MonsterTemplate, combat: CombatRules
) -> FightEstimate:
    my_hit = max(combat.min_damage, attack - template.defense)
    its_hit = max(combat.min_damage, template.attack - defense)
    crush = crushing_blow(template)
    average = its_hit * (1 + crush.chance * (crush.multiplier - 1)) if crush else its_hit
    drain = life_drain(template)
    heal = average * drain.share if drain else 0.0
    if monster_hp <= my_hit:
        return FightEstimate(my_hit, its_hit, 1, 0)
    if my_hit <= heal:
        return FightEstimate(my_hit, its_hit, None, None)
    strikes = math.ceil((monster_hp - my_hit) / (my_hit - heal))  # one after each hit but the last
    return FightEstimate(my_hit, its_hit, strikes + 1, round(strikes * average))
