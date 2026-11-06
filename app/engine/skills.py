"""Skills (data/skills.yaml): active abilities of a class, used instead of an attack.

An offensive skill strikes like an attack (same checks: ``combat.engage``; it may open a
fight), with its own multiplier, number of hits, armor piercing, stun and life drain; a healing
skill heals a share of the max HP, also outside a fight. Using one is a combat round: the other
cooldowns tick, the skill's own starts, and the monster strikes back unless it is stunned or
dead. Cooldowns count combat rounds (engine/cooldowns.py); every fight starts with all ready.
"""

from __future__ import annotations

import random
from datetime import datetime
from uuid import UUID

from app.content.registry import ContentRegistry
from app.content.schemas import Rules, SkillTemplate
from app.engine.combat import Engagement, engage, hit_monster
from app.engine.cooldowns import tick_cooldowns
from app.engine.encounter import calculate_damage, monster_strikes, touch_monster
from app.engine.events import ActionResult, ResultBuilder, RoomAudience, rejected
from app.engine.group_status import report_hp
from app.engine.permissions import check_state
from app.engine.rewards import resolve_kill
from app.engine.state import ActionSnapshot, PlayerSnapshot
from app.engine.stats import stats_of


def rounds_until_ready(player: PlayerSnapshot, skill: SkillTemplate) -> int:
    """0 when ready. Outside a fight an offensive skill always is: using it opens a new fight,
    which starts with every skill ready. A healing skill keeps its cooldown until then (no
    endless healing on the road)."""
    if skill.offensive and player.state != "in_combat":
        return 0
    return player.skill_cooldowns.get(skill.id, 0)


def rounds_text(rounds: int) -> str:
    return "1 Runde" if rounds == 1 else f"{rounds} Runden"


def _heal(
    builder: ResultBuilder,
    actor: PlayerSnapshot,
    amount: int,
    max_hp: int,
    skill: SkillTemplate,
    text: str,
) -> int:
    healed = min(max(0, amount), max_hp - actor.hp)
    if healed <= 0:
        return 0
    actor.hp += healed
    builder.update_player(actor.id, hp=actor.hp)
    builder.to_actor("skill.heal", text.format(healed=healed), healed=healed, skill_id=skill.id)
    report_hp(builder, actor)
    return healed


def _start_cooldown(builder: ResultBuilder, actor: PlayerSnapshot, skill: SkillTemplate) -> None:
    tick_cooldowns(builder, actor)
    actor.skill_cooldowns = {**actor.skill_cooldowns, skill.id: skill.cooldown}
    builder.update_player(actor.id, skill_cooldowns=actor.skill_cooldowns)


def resolve_use_skill(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    skill_id: str,
    target_id: UUID | None = None,
) -> ActionResult:
    """Uses one of the actor's skills; offensive ones on ``target_id`` (default: the current
    opponent)."""
    actor = snapshot.actor
    if denied := check_state("use_skill", actor):
        return denied
    skill = content.skills.get(skill_id)
    if skill is None or skill.class_id != actor.class_id:
        return rejected("Diese Fähigkeit beherrschst du nicht.")
    if actor.level < skill.level:
        return rejected(f"{skill.name} lernst du erst auf Stufe {skill.level}.")
    if left := rounds_until_ready(actor, skill):
        return rejected(f"{skill.name} ist erst in {rounds_text(left)} wieder bereit.")
    stats = stats_of(actor, content.items)

    if not skill.offensive:
        if actor.hp >= stats.max_hp:
            return rejected("Du bist unverletzt.")
        builder = ResultBuilder(actor_id=actor.id)
        amount = (stats.max_hp * skill.heal + 50) // 100
        _heal(
            builder,
            actor,
            amount,
            stats.max_hp,
            skill,
            f"Du setzt {skill.name} ein und heilst dich um {{healed}} HP.",
        )
        monster = snapshot.monster(actor.combat_target_id) if actor.combat_target_id else None
        _start_cooldown(builder, actor, skill)
        if actor.state == "in_combat" and monster is not None and monster.alive:
            template = content.get_monster(monster.monster_id)
            touch_monster(builder, monster, template, now)
            monster_strikes(builder, snapshot, actor, monster, template, content, rules, rng)
        return builder.build()

    target = target_id or (actor.combat_target_id if actor.state == "in_combat" else None)
    if target is None:
        return rejected(f"Gegen wen willst du {skill.name} einsetzen?")
    engaged = engage(snapshot, content, "use_skill", target, now)
    if not isinstance(engaged, Engagement):
        return engaged
    builder, monster, template = engaged.builder, engaged.monster, engaged.template
    dealt = 0
    for _ in range(skill.hits):
        if monster.hp <= 0:
            break
        defense = 0 if skill.pierce else template.defense
        plain = calculate_damage(stats.attack, defense, rules.combat, rng)
        damage = max(rules.combat.min_damage, int(plain * skill.damage + 0.5))
        dealt += hit_monster(builder, snapshot, monster, template, damage, skill.name)
    if skill.drain:
        _heal(
            builder,
            actor,
            (dealt * skill.drain + 50) // 100,
            stats.max_hp,
            skill,
            f"{skill.name} heilt dich um {{healed}} HP.",
        )
    if skill.heal:
        _heal(
            builder,
            actor,
            (stats.max_hp * skill.heal + 50) // 100,
            stats.max_hp,
            skill,
            f"{skill.name} heilt dich um {{healed}} HP.",
        )
    _start_cooldown(builder, actor, skill)

    if monster.hp <= 0:
        resolve_kill(builder, snapshot, monster, template, actor, content, rules, now, rng)
    elif skill.stun:
        builder.to_actor(
            "combat.stunned", f"{template.name} taumelt und schlägt in dieser Runde nicht zurück."
        )
        builder.event(
            "combat.stunned",
            RoomAudience(room_id=monster.room_id),
            f"{template.name} taumelt.",
            exclude=[actor.id],
        )
    else:
        monster_strikes(builder, snapshot, actor, monster, template, content, rules, rng)
    return builder.build()
