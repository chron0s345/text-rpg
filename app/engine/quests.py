"""Quests (ARCHITECTURE.md, "Quests"): accepting, turning in and abandoning, plus kill progress.

A quest is offered by a merchant (its giver) and accepted and turned in where that merchant
stands. Delivery quests count the backpack on turn-in and take the items; kill quests count
kills as they happen (``count_kill``, called from ``rewards.resolve_kill``): every member of
the credited party in the room gets the kill, rare variants count for their base monster.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from datetime import datetime
from typing import Final

from app.content.registry import ContentRegistry
from app.content.schemas import DeliverObjective, QuestTemplate, Rules
from app.engine.events import ActionResult, QuestDelete, ResultBuilder, rejected
from app.engine.inventory import add_item_to_inventory, calculate_inventory_capacity
from app.engine.permissions import check_state
from app.engine.progression import apply_xp
from app.engine.state import (
    ActionSnapshot,
    InventoryItemState,
    PlayerSnapshot,
    QuestState,
    new_id,
)

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


def _available_items(actor: PlayerSnapshot, item_id: str) -> list[InventoryItemState]:
    return [
        i
        for i in actor.inventory
        if i.item_id == item_id and i.equipped_slot is None and i.id not in actor.locked_item_ids
    ]


def objective_progress(
    quest: QuestTemplate, state: QuestState, actor: PlayerSnapshot
) -> tuple[int, int]:
    """(done, needed) for display and turn-in; deliveries count the backpack right now."""
    objective = quest.objective
    if isinstance(objective, DeliverObjective):
        have = sum(i.quantity for i in _available_items(actor, objective.item_id))
        return min(have, objective.quantity), objective.quantity
    return min(state.progress, objective.count), objective.count


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
    if sum(1 for q in actor.quests if q.status == "active") >= MAX_ACTIVE_QUESTS:
        return rejected(f"Du hast schon {MAX_ACTIVE_QUESTS} offene Aufträge.")

    previous = actor.quest(quest.id)
    state = QuestState(
        quest_id=quest.id,
        status="active",
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
        restored = state.model_copy(update={"status": "completed", "progress": 0})
        actor.quests = [restored if q.quest_id == quest_id else q for q in actor.quests]
        builder.upsert_quest(actor.id, restored)
    else:
        actor.quests = [q for q in actor.quests if q.quest_id != quest_id]
        builder.changes.quests_delete.append(QuestDelete(player_id=actor.id, quest_id=quest_id))
    builder.to_actor("quest.abandoned", f"Auftrag abgebrochen: {quest.name}.", quest_id=quest_id)
    return builder.build()


def _take_items(builder: ResultBuilder, actor: PlayerSnapshot, item_id: str, quantity: int) -> None:
    """Removes ``quantity`` of ``item_id`` from free, unlocked stacks (caller checked enough)."""
    remaining = quantity
    for entry in _available_items(actor, item_id):
        if remaining == 0:
            break
        taken = min(entry.quantity, remaining)
        remaining -= taken
        entry.quantity -= taken
        if entry.quantity == 0:
            actor.inventory = [i for i in actor.inventory if i.id != entry.id]
            builder.changes.inventory_delete.append(entry.id)
        else:
            builder.upsert_inventory(actor.id, [entry])


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
    done, needed = objective_progress(quest, state, actor)
    if done < needed:
        return rejected(f"{quest.name}: erst {done}/{needed}.")

    builder = ResultBuilder(actor_id=actor.id)
    objective = quest.objective
    if isinstance(objective, DeliverObjective):
        _take_items(builder, actor, objective.item_id, objective.quantity)

    # reward items must fit completely, otherwise nothing happens
    capacity = calculate_inventory_capacity(rules.inventory.slots, actor.inventory, content.items)
    trial = [i.model_copy() for i in actor.inventory]
    for reward in quest.reward.items:
        added, _ = add_item_to_inventory(
            trial, content.get_item(reward.item_id), reward.quantity, capacity, lambda: new_id(rng)
        )
        if added < reward.quantity:
            return rejected("Für die Belohnung ist kein Platz im Inventar.")
    for reward in quest.reward.items:
        _, touched = add_item_to_inventory(
            actor.inventory,
            content.get_item(reward.item_id),
            reward.quantity,
            capacity,
            lambda: new_id(rng),
        )
        builder.upsert_inventory(actor.id, touched)

    finished = state.model_copy(
        update={"status": "completed", "progress": 0, "times_completed": state.times_completed + 1}
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
    rewards += [
        f"{r.quantity}× {content.get_item(r.item_id).name}"
        if r.quantity > 1
        else content.get_item(r.item_id).name
        for r in quest.reward.items
    ]
    if rewards:
        builder.to_actor("quest.reward", "Belohnung: " + ", ".join(rewards) + ".")
    if quest.reward.xp:
        apply_xp(builder, actor, quest.reward.xp, rules, content.items)
    return builder.build()


def count_kill(
    builder: ResultBuilder,
    recipients: Iterable[PlayerSnapshot],
    monster_id: str,
    content: ContentRegistry,
) -> None:
    """Every credited party member with an active kill quest for this monster (or the base of
    this rare variant) gets one kill, up to the quest's count."""
    base = content.base_monster_id(monster_id)
    for player in recipients:
        for state in player.quests:
            if state.status != "active":
                continue
            quest = content.quests.get(state.quest_id)
            if quest is None or quest.objective.type != "kill":
                continue
            if quest.objective.monster_id != base or state.progress >= quest.objective.count:
                continue
            state.progress += 1
            builder.upsert_quest(player.id, state)
            text = f"{quest.name}: {state.progress}/{quest.objective.count}"
            if state.progress >= quest.objective.count:
                text += f" – zurück zu {content.merchants[quest.giver_id].name}!"
            builder.to_player(player.id, "quest.progress", text, quest_id=quest.id)
