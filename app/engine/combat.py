"""Combat actions ``attack`` and ``flee`` (ARCHITECTURE.md, "Kampf").

Fights are bound to the monster instance; several players may attack the same monster. Every
``attack`` is one round: the player strikes, then the monster strikes back at *this* player.
"""

from __future__ import annotations

import random
from datetime import datetime
from uuid import UUID

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.encounter import (
    calculate_damage,
    enter_combat,
    leave_combat,
    monster_strikes,
    touch_monster,
)
from app.engine.events import ActionResult, ResultBuilder, RoomAudience, rejected
from app.engine.participation import remove_from_combat, upsert_participation
from app.engine.permissions import check_state
from app.engine.rewards import resolve_kill
from app.engine.state import ActionSnapshot
from app.engine.stats import stats_of

__all__ = ["calculate_damage", "resolve_attack", "resolve_flee"]


def resolve_attack(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    target_id: UUID,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("attack", actor):
        return denied
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
    touch_monster(builder, monster, template, now)

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

    enter_combat(builder, actor, monster)
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
        monster_strikes(builder, snapshot, actor, monster, template, content, rules, rng)
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
    if denied := check_state("flee", actor):
        return denied

    builder = ResultBuilder(actor_id=actor.id)
    monster = snapshot.monster(actor.combat_target_id) if actor.combat_target_id else None
    if monster is None or not monster.alive:
        # Stale combat state (e.g. the monster is gone): just end the fight.
        if actor.combat_target_id is not None:
            remove_from_combat(builder, snapshot, actor, actor.combat_target_id)
        leave_combat(builder, actor)
        builder.to_actor("combat.ended", "Du bist nicht mehr im Kampf.")
        return builder.build()

    template = content.get_monster(monster.monster_id)
    if rng.random() < rules.combat.flee_chance:
        remove_from_combat(builder, snapshot, actor, monster.id)
        leave_combat(builder, actor)
        builder.to_actor("combat.fled", f"Du entkommst {template.name}.")
        builder.event(
            "combat.fled",
            RoomAudience(room_id=monster.room_id),
            f"{actor.name} flieht vor {template.name}.",
            exclude=[actor.id],
        )
        return builder.build()

    touch_monster(builder, monster, template, now)
    builder.to_actor("combat.flee_failed", "Die Flucht misslingt!")
    monster_strikes(builder, snapshot, actor, monster, template, content, rules, rng)
    return builder.build()
