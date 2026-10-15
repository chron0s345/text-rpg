"""Achievements (ARCHITECTURE.md, "Erfolge"): earned once per character, never lost, some come
with a title the character may show after their name.

``grant`` checks every achievement a character has not earned yet against their current state
(level, completed quests, kill tally, worn sets). It runs wherever one of these can change:
kill credit, quest turn-in, equipping, login (catches up on achievements added to the content
later). ``resolve_set_title`` picks the shown title.
"""

from __future__ import annotations

import random
from datetime import datetime

from app.content.registry import ContentRegistry
from app.content.schemas import (
    AchievementTemplate,
    KillCondition,
    LevelCondition,
    QuestCondition,
    QuestCountCondition,
    RareKillCondition,
    Rules,
    SetCondition,
    SpeciesCondition,
)
from app.engine.events import ActionResult, ResultBuilder, rejected
from app.engine.permissions import check_state
from app.engine.state import ActionSnapshot, PlayerSnapshot
from app.engine.stats import worn_sets


def completed_quests(player: PlayerSnapshot) -> set[str]:
    return {q.quest_id for q in player.quests if q.times_completed > 0}


def kills_of(player: PlayerSnapshot, monster_id: str, content: ContentRegistry) -> int:
    """Kill credits for this monster, its rare variant included."""
    return sum(n for m, n in player.kills.items() if content.base_monster_id(m) == monster_id)


def species_killed(player: PlayerSnapshot, content: ContentRegistry) -> set[str]:
    """Base monsters the character has kill credit for (a rare variant counts as its base)."""
    return {content.base_monster_id(m) for m, n in player.kills.items() if n > 0}


def met(achievement: AchievementTemplate, player: PlayerSnapshot, content: ContentRegistry) -> bool:
    condition = achievement.condition
    match condition:
        case LevelCondition():
            return player.level >= condition.level
        case QuestCondition():
            return condition.quest_id in completed_quests(player)
        case QuestCountCondition():
            done = len(completed_quests(player) & content.quests.keys())
            return done >= (condition.count or len(content.quests))
        case KillCondition():
            return kills_of(player, condition.monster_id, content) >= condition.count
        case SpeciesCondition():
            every = {content.base_monster_id(m) for m in content.monsters}
            return len(species_killed(player, content) & every) >= (condition.count or len(every))
        case RareKillCondition():
            return any(content.base_monster_id(m) != m for m, n in player.kills.items() if n)
        case SetCondition():
            return any(
                count == len(item_set.items)
                and (condition.set_id is None or condition.set_id == item_set.id)
                for item_set, count in worn_sets(player.inventory, content.items)
            )
    return False  # pragma: no cover - the match is exhaustive


def grant(builder: ResultBuilder, player: PlayerSnapshot, content: ContentRegistry) -> None:
    """Awards every achievement ``player`` now meets and has not earned yet (mutates
    ``player``), one log line each."""
    earned = [
        a
        for a in content.achievements.values()
        if a.id not in player.achievements and met(a, player, content)
    ]
    if not earned:
        return
    player.achievements = [*player.achievements, *(a.id for a in earned)]
    builder.update_player(player.id, achievements=player.achievements)
    for achievement in earned:
        text = f"Erfolg errungen: {achievement.name} – {achievement.description}"
        if achievement.title:
            text += f" Neuer Titel: „{achievement.title}“."
        builder.to_player(player.id, "achievement.earned", text, achievement_id=achievement.id)


def title_of(player: PlayerSnapshot, content: ContentRegistry) -> str | None:
    """The title the character shows, if any (an achievement removed from the content shows
    none)."""
    if player.title is None:
        return None
    achievement = content.achievements.get(player.title)
    return achievement.title if achievement is not None else None


def resolve_set_title(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    achievement_id: str | None,
) -> ActionResult:
    """Shows the title of an earned achievement after the name; ``None`` shows none."""
    actor = snapshot.actor
    if denied := check_state("set_title", actor):
        return denied
    builder = ResultBuilder(actor_id=actor.id)
    if achievement_id is None:
        if actor.title is None:
            return rejected("Du führst keinen Titel.")
        actor.title = None
        builder.update_player(actor.id, title=None)
        builder.to_actor("player.title", "Du legst deinen Titel ab.")
        return builder.build()
    achievement = content.achievements.get(achievement_id)
    if achievement is None or achievement.id not in actor.achievements:
        return rejected("Diesen Erfolg hast du nicht errungen.")
    if achievement.title is None:
        return rejected("Dieser Erfolg bringt keinen Titel.")
    if actor.title == achievement.id:
        return rejected("Diesen Titel führst du bereits.")
    actor.title = achievement.id
    builder.update_player(actor.id, title=achievement.id)
    builder.to_actor("player.title", f"Du führst jetzt den Titel „{achievement.title}“.")
    return builder.build()
