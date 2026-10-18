"""Actions ``move`` and ``travel`` (ARCHITECTURE.md, "Charakterzustände", "Tod & Checkpoint",
"Überfälle", "Reisen").

The snapshot describes the *destination* room: ``room_players``, ``monsters`` and
``participations`` belong to the room behind the chosen exit.
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Final

from app.content.registry import ContentRegistry
from app.content.schemas import Direction, RoomTemplate, Rules
from app.engine.ambush import ambush_candidates, apply_ambush, roll_ambush
from app.engine.events import ActionResult, ResultBuilder, RoomAudience, rejected
from app.engine.group_status import report_location
from app.engine.permissions import check_state
from app.engine.quests import count_visit
from app.engine.state import ActionSnapshot, PlayerSnapshot

DIRECTION_NAMES: Final[dict[Direction, str]] = {
    "north": "Norden",
    "south": "Süden",
    "east": "Osten",
    "west": "Westen",
    "up": "oben",
    "down": "unten",
}


def resolve_move(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    direction: Direction,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("move", actor):
        return denied
    origin = content.get_room(actor.current_room_id)
    destination_id = origin.exits.get(direction)
    if destination_id is None:
        return rejected(f"Es gibt keinen Weg nach {DIRECTION_NAMES[direction]}.")
    destination = content.get_room(destination_id)

    builder = ResultBuilder(actor_id=actor.id)
    actor.current_room_id = destination.id
    builder.update_player(actor.id, current_room_id=destination.id)
    builder.event(
        "move.leave",
        RoomAudience(room_id=origin.id),
        f"{actor.name} geht nach {DIRECTION_NAMES[direction]}.",
        exclude=[actor.id],
        player_id=str(actor.id),
    )
    builder.event(
        "move.enter",
        RoomAudience(room_id=destination.id),
        f"{actor.name} kommt herein.",
        exclude=[actor.id],
        player_id=str(actor.id),
    )
    builder.to_actor(
        "move.enter",
        f"Du gehst nach {DIRECTION_NAMES[direction]}: {destination.name}.",
        room_id=destination.id,
    )

    _arrive(builder, actor, destination, content)

    hit = roll_ambush(ambush_candidates(snapshot, actor, content), lambda a: a.chance_on_enter, rng)
    if hit is not None:
        apply_ambush(builder, snapshot, actor, *hit, content, rules, now, rng)
    return builder.build()


def _arrive(
    builder: ResultBuilder,
    actor: PlayerSnapshot,
    destination: RoomTemplate,
    content: ContentRegistry,
) -> None:
    """What every arrival brings: the group sees where you are, a visit step counts, a
    checkpoint binds."""
    report_location(builder, actor, destination.name)
    count_visit(builder, actor, content)
    if destination.checkpoint and actor.checkpoint_room_id != destination.id:
        actor.checkpoint_room_id = destination.id
        builder.update_player(actor.id, checkpoint_room_id=destination.id)
        builder.to_actor(
            "checkpoint.bound",
            "Du fühlst dich mit diesem Ort verbunden.",
            room_id=destination.id,
        )


def resolve_travel(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    room_id: str,
) -> ActionResult:
    """A paid ride along a ``RoomTemplate.travel`` route (ARCHITECTURE.md, "Reisen"). Both ends
    are safe rooms, so nobody lies in wait on arrival. The snapshot describes the destination."""
    actor = snapshot.actor
    if denied := check_state("travel", actor):
        return denied
    origin = content.get_room(actor.current_room_id)
    route = next((r for r in origin.travel if r.room_id == room_id), None)
    if route is None:
        return rejected("Von hier aus fährt nichts dorthin.")
    destination = content.get_room(route.room_id)
    if actor.level < route.min_level:
        return rejected(
            f"Die Fahrt nach {destination.name} gibt es erst ab Stufe {route.min_level}."
        )
    if actor.gold < route.gold:
        return rejected(f"Die Fahrt kostet {route.gold} Gold, du hast nur {actor.gold}.")

    builder = ResultBuilder(actor_id=actor.id)
    actor.gold -= route.gold
    actor.current_room_id = destination.id
    builder.update_player(actor.id, gold=actor.gold, current_room_id=destination.id)
    builder.event(
        "move.leave",
        RoomAudience(room_id=origin.id),
        f"{actor.name} reist ab.",
        exclude=[actor.id],
        player_id=str(actor.id),
    )
    builder.event(
        "move.enter",
        RoomAudience(room_id=destination.id),
        f"{actor.name} kommt an.",
        exclude=[actor.id],
        player_id=str(actor.id),
    )
    cost = f" für {route.gold} Gold" if route.gold else ""
    builder.to_actor(
        "move.travel",
        f"Du nimmst {route.by} nach {destination.name}{cost}.",
        room_id=destination.id,
        gold=route.gold,
    )
    _arrive(builder, actor, destination, content)
    return builder.build()
