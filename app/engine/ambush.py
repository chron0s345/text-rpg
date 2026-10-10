"""Monster ambushes (ARCHITECTURE.md, "Überfälle").

- On entering a room: ``chance_on_enter`` per eligible monster (``movement.resolve_move``).
- While idling in a room: ``chance_per_tick``, rolled by the world ticker (``resolve_idle_ambush``).
- Never in safe rooms, never against dead or fighting players, never against players more than
  ``max_level_difference`` levels above the monster. A monster that is already fighting does not
  ambush anybody, so there is at most one ambush per monster at a time.
- On success the monster strikes first, the victim is ``in_combat`` and the monster is tagged by
  the victim's party.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import datetime

from app.content.registry import ContentRegistry
from app.content.schemas import Ambush, MonsterTemplate, Rules
from app.engine.encounter import enter_combat, monster_strikes, touch_monster
from app.engine.events import ActionResult, GroupAudience, ResultBuilder, RoomAudience
from app.engine.participation import upsert_participation
from app.engine.state import ActionSnapshot, MonsterSnapshot, PlayerSnapshot


def ambush_candidates(
    snapshot: ActionSnapshot, victim: PlayerSnapshot, content: ContentRegistry
) -> list[tuple[MonsterSnapshot, MonsterTemplate]]:
    """Monsters in the victim's room that may ambush the victim right now.

    Precondition (not visible in the snapshot): the victim is online. The world ticker must only
    roll for online characters (ARCHITECTURE.md: "nie gegen tote oder offline Charaktere").
    """
    if victim.state != "exploring" or content.get_room(victim.current_room_id).safe:
        return []
    fighting = {p.monster_instance_id for p in snapshot.participations}
    candidates: list[tuple[MonsterSnapshot, MonsterTemplate]] = []
    for monster in snapshot.monsters:
        if not monster.alive or monster.room_id != victim.current_room_id or monster.id in fighting:
            continue
        template = content.get_monster(monster.monster_id)
        ambush = template.ambush
        if ambush is None:
            continue
        if (
            ambush.max_level_difference is not None
            and victim.level - template.level > ambush.max_level_difference
        ):
            continue
        candidates.append((monster, template))
    return candidates


def roll_ambush(
    candidates: list[tuple[MonsterSnapshot, MonsterTemplate]],
    chance: Callable[[Ambush], float],
    rng: random.Random,
) -> tuple[MonsterSnapshot, MonsterTemplate] | None:
    """Rolls each candidate in order; the first success ambushes."""
    for monster, template in candidates:
        assert template.ambush is not None
        if rng.random() < chance(template.ambush):
            return monster, template
    return None


def apply_ambush(
    builder: ResultBuilder,
    snapshot: ActionSnapshot,
    victim: PlayerSnapshot,
    monster: MonsterSnapshot,
    template: MonsterTemplate,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
) -> None:
    touch_monster(builder, monster, template, now)
    monster.tagged_by_party = victim.party_key
    builder.update_monster(monster.id, tagged_by_party=victim.party_key)
    # Zero-damage participation: the victim's party holds the tag while the victim fights.
    upsert_participation(builder, snapshot, victim, monster.id, 0)
    enter_combat(builder, victim, monster)

    builder.to_player(victim.id, "ambush", f"{template.name} überfällt dich!")
    builder.event(
        "ambush",
        RoomAudience(room_id=victim.current_room_id),
        f"{template.name} überfällt {victim.name}!",
        exclude=[victim.id],
        monster_instance_id=str(monster.id),
        player_id=str(victim.id),
    )
    if victim.group_id is not None:
        builder.event(
            "group.member_attacked",
            GroupAudience(group_id=victim.group_id),
            f"{template.name} überfällt {victim.name}!",
            exclude=[victim.id],
            player_id=str(victim.id),
            room_id=victim.current_room_id,
        )
    monster_strikes(builder, snapshot, victim, template, content, rules, rng)


def resolve_idle_ambush(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
) -> ActionResult:
    """World-ticker roll for ``snapshot.actor`` idling in a room (``chance_per_tick``).

    Not a player action: no actor audience, the victim is informed via its player channel.
    """
    builder = ResultBuilder()
    victim = snapshot.actor
    hit = roll_ambush(
        ambush_candidates(snapshot, victim, content), lambda a: a.chance_per_tick, rng
    )
    if hit is not None:
        apply_ambush(builder, snapshot, victim, *hit, content, rules, now, rng)
    return builder.build()
