"""Combat actions ``attack`` and ``flee`` (ARCHITECTURE.md, "Kampf").

Fights are bound to the monster instance; several players may attack the same monster. Every
``attack`` is one round: the player strikes, then the monster strikes back at *this* player.
"""

from __future__ import annotations

import random
from datetime import datetime
from uuid import UUID

from app.content.registry import ContentRegistry
from app.content.schemas import CombatRules, MonsterTemplate, Rules
from app.engine.death import apply_player_death
from app.engine.events import ActionResult, GroupAudience, ResultBuilder, RoomAudience, rejected
from app.engine.participation import remove_from_combat, upsert_participation
from app.engine.regeneration import calculate_current_monster_hp
from app.engine.rewards import resolve_kill
from app.engine.state import ActionSnapshot, MonsterSnapshot, PlayerSnapshot
from app.engine.stats import stats_of


def calculate_damage(attack: int, defense: int, combat: CombatRules, rng: random.Random) -> int:
    """``attack - defense ± damage_variance``, never below ``min_damage``."""
    variance = rng.randint(-combat.damage_variance, combat.damage_variance)
    return max(combat.min_damage, attack - defense + variance)


def resolve_attack(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    target_id: UUID,
) -> ActionResult:
    actor = snapshot.actor
    if actor.state == "dead":
        return rejected("Du bist tot.")
    if content.get_room(actor.current_room_id).safe:
        return rejected("Hier wird nicht gekämpft.")
    current = snapshot.monster(actor.combat_target_id) if actor.combat_target_id else None
    stale = actor.state == "in_combat" and (current is None or not current.alive)
    if actor.state == "in_combat" and not stale and actor.combat_target_id != target_id:
        return rejected("Du kämpfst bereits gegen einen anderen Gegner.")
    monster = snapshot.monster(target_id)
    if monster is None or not monster.alive or monster.room_id != actor.current_room_id:
        return rejected("Dieses Ziel gibt es hier nicht.")

    template = content.get_monster(monster.monster_id)
    builder = ResultBuilder(actor_id=actor.id)
    if stale and actor.combat_target_id is not None:
        # The previous opponent is gone (e.g. killed by others): end that fight first.
        remove_from_combat(builder, snapshot, actor, actor.combat_target_id)
    _touch_monster(builder, monster, template, now)

    damage = min(
        calculate_damage(
            stats_of(actor, content.items).attack, template.defense, rules.combat, rng
        ),
        monster.hp,
    )
    monster.hp -= damage
    builder.update_monster(monster.id, hp=monster.hp)
    if monster.tagged_by_party is None:
        monster.tagged_by_party = actor.party_key
        builder.update_monster(monster.id, tagged_by_party=actor.party_key)
    upsert_participation(builder, snapshot, actor, monster.id, damage)

    actor.state = "in_combat"
    actor.combat_target_id = monster.id
    builder.update_player(actor.id, state="in_combat", combat_target_id=monster.id)
    builder.to_actor(
        "combat.hit", f"Du triffst {template.name} für {damage} Schaden.", damage=damage
    )
    builder.event(
        "combat.hit",
        RoomAudience(room_id=monster.room_id),
        f"{actor.name} trifft {template.name} für {damage} Schaden.",
        exclude=[actor.id],
        monster_instance_id=str(monster.id),
        monster_hp=monster.hp,
    )

    if monster.hp <= 0:
        resolve_kill(builder, snapshot, monster, template, actor, content, rules, now, rng)
    else:
        _monster_strikes(builder, snapshot, actor, template, content, rules, rng)
    return builder.build()


def resolve_flee(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
) -> ActionResult:
    """Success (``combat.flee_chance``): combat ends without a counter-attack.
    Failure: the monster strikes the fleeing player."""
    actor = snapshot.actor
    if actor.state != "in_combat":
        return rejected("Du bist in keinem Kampf.")

    builder = ResultBuilder(actor_id=actor.id)
    monster = snapshot.monster(actor.combat_target_id) if actor.combat_target_id else None
    if monster is None or not monster.alive:
        # Stale combat state (e.g. the monster is gone): just end the fight.
        if actor.combat_target_id is not None:
            remove_from_combat(builder, snapshot, actor, actor.combat_target_id)
        _leave_combat(builder, actor)
        builder.to_actor("combat.ended", "Du bist nicht mehr im Kampf.")
        return builder.build()

    template = content.get_monster(monster.monster_id)
    if rng.random() < rules.combat.flee_chance:
        remove_from_combat(builder, snapshot, actor, monster.id)
        _leave_combat(builder, actor)
        builder.to_actor("combat.fled", f"Du entkommst {template.name}.")
        builder.event(
            "combat.fled",
            RoomAudience(room_id=monster.room_id),
            f"{actor.name} flieht vor {template.name}.",
            exclude=[actor.id],
        )
        return builder.build()

    _touch_monster(builder, monster, template, now)
    builder.to_actor("combat.flee_failed", "Die Flucht misslingt!")
    _monster_strikes(builder, snapshot, actor, template, content, rules, rng)
    return builder.build()


def _touch_monster(
    builder: ResultBuilder, monster: MonsterSnapshot, template: MonsterTemplate, now: datetime
) -> None:
    """Combat contact: apply lazy regeneration, then reset the regeneration clock."""
    monster.hp = calculate_current_monster_hp(template, monster.hp, monster.last_combat_at, now)
    monster.last_combat_at = now
    builder.update_monster(monster.id, hp=monster.hp, last_combat_at=now)


def _leave_combat(builder: ResultBuilder, player: PlayerSnapshot) -> None:
    player.state = "exploring"
    player.combat_target_id = None
    builder.update_player(player.id, state="exploring", combat_target_id=None)


def _monster_strikes(
    builder: ResultBuilder,
    snapshot: ActionSnapshot,
    player: PlayerSnapshot,
    template: MonsterTemplate,
    content: ContentRegistry,
    rules: Rules,
    rng: random.Random,
) -> None:
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
    if player.group_id is not None:
        builder.event(
            "group.member_hp",
            GroupAudience(group_id=player.group_id),
            f"{player.name}: {player.hp} HP",
            exclude=[player.id],
            player_id=str(player.id),
            hp=player.hp,
        )
    if player.hp <= 0:
        apply_player_death(builder, snapshot, player, content, rules, rng)
