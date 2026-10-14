"""Quests (ARCHITECTURE.md, "Quests"): accepting, turning in and abandoning, plus progress.

A quest is offered by a merchant (its giver) and accepted and turned in where that merchant
stands. Its steps are done in order:

- kill steps count kills as they happen (``count_kill``, called from ``rewards.resolve_kill``):
  every member of the credited party in the room gets the kill, rare variants count for their
  base monster;
- visit steps are done by entering the room (``count_visit``, called from ``resolve_move``),
  or at once if the character already stands there when the step begins;
- a delivery can only be the last step: it counts the backpack on turn-in and takes the items.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from datetime import datetime
from typing import Final

from app.content.registry import ContentRegistry
from app.content.schemas import (
    DeliverObjective,
    KillObjective,
    Objective,
    QuestTemplate,
    Rules,
    VisitObjective,
)
from app.engine.achievements import grant
from app.engine.events import ActionResult, QuestDelete, ResultBuilder, rejected
from app.engine.materials import (
    count_available,
    give_all_or_nothing,
    quantity_text,
    take_items,
)
from app.engine.permissions import check_state
from app.engine.progression import apply_xp
from app.engine.state import ActionSnapshot, PlayerSnapshot, QuestState

MAX_ACTIVE_QUESTS: Final = 10
UNKNOWN: Final = "Diesen Auftrag gibt es nicht."


def _giver_here(quest: QuestTemplate, actor: PlayerSnapshot, content: ContentRegistry) -> bool:
    return content.merchants[quest.giver_id].room_id == actor.current_room_id


def why_not_offered(
    quest: QuestTemplate, actor: PlayerSnapshot, content: ContentRegistry
) -> str | None:
    """None if the actor may accept the quest now (giver presence aside)."""
    state = actor.quest(quest.id)
    if state is not None and state.status == "active":
        return "Diesen Auftrag hast du schon."
    if state is not None and not quest.repeatable:
        return "Diesen Auftrag hast du bereits erledigt."
    if actor.level < quest.min_level:
        return f"Dafür bist du noch nicht erfahren genug (ab Stufe {quest.min_level})."
    for required in quest.requires:
        done = actor.quest(required)
        if done is None or done.times_completed == 0:
            return f"Erledige zuerst „{content.get_quest(required).name}“."
    return None


def quests_offered_here(actor: PlayerSnapshot, content: ContentRegistry) -> list[QuestTemplate]:
    """Quests of the givers in the actor's room that the actor may accept now."""
    return [
        quest
        for quest in content.quests.values()
        if _giver_here(quest, actor, content) and why_not_offered(quest, actor, content) is None
    ]


def step_text(step: Objective, content: ContentRegistry) -> str:
    """What a step asks for, e.g. "Besiege 4× Hungriger Wolf"."""
    if isinstance(step, DeliverObjective):
        return f"Bringe {step.quantity}× {content.get_item(step.item_id).name}"
    if isinstance(step, KillObjective):
        return f"Besiege {step.count}× {content.get_monster(step.monster_id).name}"
    return f"Gehe zu: {content.get_room(step.room_id).name}"


def current_step(quest: QuestTemplate, state: QuestState) -> Objective | None:
    """The step being worked on; None once all kill and visit steps are done."""
    return quest.steps[state.step] if state.step < len(quest.steps) else None


def objective_progress(
    quest: QuestTemplate, state: QuestState, actor: PlayerSnapshot
) -> tuple[int, int]:
    """(done, needed) of the current step for display and turn-in; deliveries count the
    backpack right now. (1, 1) once every step is done."""
    step = current_step(quest, state)
    if step is None:
        return 1, 1
    if isinstance(step, DeliverObjective):
        have = count_available(actor, step.item_id)
        return min(have, step.quantity), step.quantity
    if isinstance(step, KillObjective):
        return min(state.progress, step.count), step.count
    return 0, 1  # a visit step that is still ahead


def ready_to_turn_in(quest: QuestTemplate, state: QuestState, actor: PlayerSnapshot) -> bool:
    """All steps done, or only the closing delivery left and the items are at hand."""
    step = current_step(quest, state)
    if step is None:
        return True
    if not isinstance(step, DeliverObjective):
        return False
    done, needed = objective_progress(quest, state, actor)
    return done >= needed


def _advance(
    builder: ResultBuilder,
    player: PlayerSnapshot,
    quest: QuestTemplate,
    state: QuestState,
    content: ContentRegistry,
    done_text: str,
) -> None:
    """Finishes the current step and tells the player ``done_text`` plus what is next; a
    following visit step for the room the player stands in is done right away."""
    state.step += 1
    state.progress = 0
    while (step := current_step(quest, state)) is not None and (
        isinstance(step, VisitObjective) and step.room_id == player.current_room_id
    ):
        state.step += 1
    builder.upsert_quest(player.id, state)
    step = current_step(quest, state)
    giver = content.merchants[quest.giver_id].name
    if step is None:
        text = f"{done_text} – zurück zu {giver}!"
    elif isinstance(step, DeliverObjective):
        text = f"{done_text} – jetzt: {step_text(step, content)} zu {giver}."
    else:
        text = f"{done_text} – weiter: {step_text(step, content)}."
    builder.to_player(player.id, "quest.progress", text, quest_id=quest.id)


def resolve_quest_accept(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    quest_id: str,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("quest_accept", actor):
        return denied
    quest = content.quests.get(quest_id)
    if quest is None:
        return rejected(UNKNOWN)
    giver = content.merchants[quest.giver_id]
    if not _giver_here(quest, actor, content):
        return rejected(f"{giver.name} ist nicht hier.")
    if reason := why_not_offered(quest, actor, content):
        return rejected(reason)
    # rows of quests removed from the content are invisible and cannot be abandoned: ignore them
    open_quests = sum(
        1 for q in actor.quests if q.status == "active" and q.quest_id in content.quests
    )
    if open_quests >= MAX_ACTIVE_QUESTS:
        return rejected(f"Du hast schon {MAX_ACTIVE_QUESTS} offene Aufträge.")

    previous = actor.quest(quest.id)
    state = QuestState(
        quest_id=quest.id,
        status="active",
        step=0,
        progress=0,
        times_completed=previous.times_completed if previous else 0,
    )
    actor.quests = [q for q in actor.quests if q.quest_id != quest.id] + [state]
    builder = ResultBuilder(actor_id=actor.id)
    builder.upsert_quest(actor.id, state)
    builder.to_actor(
        "quest.accepted",
        f"Auftrag angenommen: {quest.name}. {giver.name}: „{quest.description}“",
        quest_id=quest.id,
    )
    first = quest.steps[0]
    if isinstance(first, VisitObjective) and first.room_id == actor.current_room_id:
        state.step = -1  # _advance counts up to the first step and finishes it right away
        here = content.get_room(first.room_id).name
        _advance(builder, actor, quest, state, content, f"{quest.name}: {here} – schon da")
    return builder.build()


def resolve_quest_abandon(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    quest_id: str,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("quest_abandon", actor):
        return denied
    state = actor.quest(quest_id)
    quest = content.quests.get(quest_id)
    if state is None or state.status != "active" or quest is None:
        return rejected("Diesen Auftrag hast du nicht.")
    builder = ResultBuilder(actor_id=actor.id)
    if state.times_completed > 0:  # a repeatable quest done before: back to "completed"
        restored = state.model_copy(update={"status": "completed", "step": 0, "progress": 0})
        actor.quests = [restored if q.quest_id == quest_id else q for q in actor.quests]
        builder.upsert_quest(actor.id, restored)
    else:
        actor.quests = [q for q in actor.quests if q.quest_id != quest_id]
        builder.changes.quests_delete.append(QuestDelete(player_id=actor.id, quest_id=quest_id))
    builder.to_actor("quest.abandoned", f"Auftrag abgebrochen: {quest.name}.", quest_id=quest_id)
    return builder.build()


def resolve_quest_turn_in(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    quest_id: str,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("quest_turn_in", actor):
        return denied
    quest = content.quests.get(quest_id)
    state = actor.quest(quest_id)
    if quest is None or state is None or state.status != "active":
        return rejected("Diesen Auftrag hast du nicht.")
    giver = content.merchants[quest.giver_id]
    if not _giver_here(quest, actor, content):
        return rejected(f"Den Auftrag gibst du bei {giver.name} ab.")
    if not ready_to_turn_in(quest, state, actor):
        step = current_step(quest, state)
        assert step is not None  # ready_to_turn_in is True without a step
        done, needed = objective_progress(quest, state, actor)
        if isinstance(step, VisitObjective):
            return rejected(f"{quest.name}: erst noch – {step_text(step, content)}.")
        prefix = f"Schritt {state.step + 1}/{len(quest.steps)}, " if len(quest.steps) > 1 else ""
        return rejected(f"{quest.name}: {prefix}erst {done}/{needed}.")

    builder = ResultBuilder(actor_id=actor.id)
    last = current_step(quest, state)
    if isinstance(last, DeliverObjective):
        take_items(builder, actor, last.item_id, last.quantity)

    # reward items must fit completely, otherwise nothing happens
    rewards_in = [(r.item_id, r.quantity) for r in quest.reward.items]
    if not give_all_or_nothing(builder, actor, rewards_in, content, rules, rng):
        return rejected("Für die Belohnung ist kein Platz im Inventar.")

    finished = state.model_copy(
        update={
            "status": "completed",
            "step": 0,
            "progress": 0,
            "times_completed": state.times_completed + 1,
        }
    )
    actor.quests = [finished if q.quest_id == quest_id else q for q in actor.quests]
    builder.upsert_quest(actor.id, finished)
    builder.to_actor(
        "quest.completed",
        f"Auftrag erledigt: {quest.name}. {giver.name}: „{quest.completion_text}“",
        quest_id=quest.id,
    )
    if quest.reward.gold:
        actor.gold += quest.reward.gold
        builder.update_player(actor.id, gold=actor.gold)
    rewards = [f"{quest.reward.gold} Gold"] if quest.reward.gold else []
    rewards += [quantity_text(content, r.item_id, r.quantity) for r in quest.reward.items]
    if rewards:
        builder.to_actor("quest.reward", "Belohnung: " + ", ".join(rewards) + ".")
    if quest.reward.xp:
        apply_xp(builder, actor, quest.reward.xp, rules, content.items)
    grant(builder, actor, content)
    return builder.build()


def count_kill(
    builder: ResultBuilder,
    recipients: Iterable[PlayerSnapshot],
    monster_id: str,
    content: ContentRegistry,
) -> None:
    """Every credited party member whose current quest step is to kill this monster (or the
    base of this rare variant) gets one kill, up to the step's count."""
    base = content.base_monster_id(monster_id)
    for player in recipients:
        for state in player.quests:
            quest = content.quests.get(state.quest_id)
            if state.status != "active" or quest is None:
                continue
            step = current_step(quest, state)
            if not isinstance(step, KillObjective) or step.monster_id != base:
                continue
            if state.progress >= step.count:
                continue
            state.progress += 1
            text = f"{quest.name}: {state.progress}/{step.count}"
            if state.progress >= step.count:
                _advance(builder, player, quest, state, content, text)
                continue
            builder.upsert_quest(player.id, state)
            builder.to_player(player.id, "quest.progress", text, quest_id=quest.id)


def count_visit(builder: ResultBuilder, player: PlayerSnapshot, content: ContentRegistry) -> None:
    """The player just entered their current room: visit steps for it are done."""
    for state in player.quests:
        quest = content.quests.get(state.quest_id)
        if state.status != "active" or quest is None:
            continue
        step = current_step(quest, state)
        if isinstance(step, VisitObjective) and step.room_id == player.current_room_id:
            room = content.get_room(step.room_id).name
            _advance(builder, player, quest, state, content, f"{quest.name}: {room} erreicht")
