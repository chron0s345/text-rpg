"""Skills (data/skills.yaml): active abilities of a class, used instead of an attack.

An offensive skill strikes like an attack (same checks: ``combat.engage``; it may open a
fight), with its own multiplier, number of hits, armor piercing, stun and life drain; a healing
skill heals a share of the max HP, also outside a fight. ``group`` skills heal every group
member in the room as well; ``taunt`` makes the monster's strikes at the user's group members
in the same fight hit the user instead (engine/encounter.py ``guard_for``). Using one is a
combat round: the other cooldowns tick, the skill's own starts, and the monster strikes back
unless it is stunned or dead. Cooldowns count combat rounds (engine/cooldowns.py); every fight
starts with all ready.
"""

from __future__ import annotations

import random
from collections.abc import Callable
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


def _recipients(snapshot: ActionSnapshot, skill: SkillTemplate) -> list[PlayerSnapshot]:
    """Who a skill's healing reaches: the actor, and with ``group`` every living member of
    the actor's group in the room."""
    actor = snapshot.actor
    if not skill.group or actor.group_id is None:
        return [actor]
    members = [
        p
        for p in snapshot.room_players
        if p.group_id == actor.group_id and p.state != "dead" and p.hp > 0
    ]
    return [actor, *members]


def _heal(
    builder: ResultBuilder,
    healer: PlayerSnapshot,
    target: PlayerSnapshot,
    amount: Callable[[int], int],
    content: ContentRegistry,
    skill: SkillTemplate,
    own_text: str,
) -> int:
    """Heals ``target`` by ``amount(its max HP)``, at most to its max HP; ``own_text`` is the
    healer's line when it heals itself ({healed} is filled in)."""
    max_hp = stats_of(target, content.items).max_hp
    healed = min(max(0, amount(max_hp)), max_hp - target.hp)
    if healed <= 0:
        return 0
    target.hp += healed
    builder.update_player(target.id, hp=target.hp)
    if target.id == healer.id:
        text = own_text.format(healed=healed)
        builder.to_actor("skill.heal", text, healed=healed, skill_id=skill.id)
    else:
        builder.to_player(
            target.id,
            "skill.heal",
            f"{healer.name} heilt dich mit {skill.name} um {healed} HP.",
            healed=healed,
            skill_id=skill.id,
        )
        builder.to_actor(
            "skill.heal_other", f"{skill.name} heilt {target.name} um {healed} HP.", healed=healed
        )
    report_hp(builder, target)
    return healed


def _percent(percent: int) -> Callable[[int], int]:
    return lambda max_hp: (max_hp * percent + 50) // 100


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
        recipients = _recipients(snapshot, skill)
        if all(p.hp >= stats_of(p, content.items).max_hp for p in recipients):
            return rejected(
                "Niemand ist verletzt." if len(recipients) > 1 else "Du bist unverletzt."
            )
        builder = ResultBuilder(actor_id=actor.id)
        for member in recipients:
            _heal(
                builder,
                actor,
                member,
                _percent(skill.heal),
                content,
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
    drained = (dealt * skill.drain + 50) // 100
    own = f"{skill.name} heilt dich um {{healed}} HP."
    for member in _recipients(snapshot, skill):
        if skill.drain:
            _heal(builder, actor, member, lambda _: drained, content, skill, own)
        if skill.heal:
            _heal(builder, actor, member, _percent(skill.heal), content, skill, own)
    if skill.taunt and monster.hp > 0:
        actor.guarding = skill.taunt
        builder.update_player(actor.id, guarding=actor.guarding)
        builder.to_actor(
            "combat.taunt",
            f"{template.name} wendet sich gegen dich: Du fängst die nächsten "
            f"{skill.taunt} Schläge gegen deine Gruppe ab.",
        )
        builder.event(
            "combat.taunt",
            RoomAudience(room_id=monster.room_id),
            f"{actor.name} lenkt {template.name} auf sich.",
            exclude=[actor.id],
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
