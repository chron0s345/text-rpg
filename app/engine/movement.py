"""Action ``move`` (ARCHITECTURE.md, "Charakterzustände", "Tod & Checkpoint", "Überfälle").

The snapshot describes the *destination* room: ``room_players``, ``monsters`` and
``participations`` belong to the room behind the chosen exit.
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Final

from app.content.registry import ContentRegistry
from app.content.schemas import Direction, Rules
from app.engine.ambush import ambush_candidates, apply_ambush, roll_ambush
from app.engine.events import ActionResult, ResultBuilder, RoomAudience, rejected
from app.engine.permissions import check_state
from app.engine.state import ActionSnapshot

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

    if destination.checkpoint and actor.checkpoint_room_id != destination.id:
        actor.checkpoint_room_id = destination.id
        builder.update_player(actor.id, checkpoint_room_id=destination.id)
        builder.to_actor(
            "checkpoint.bound",
            "Du fühlst dich mit diesem Ort verbunden.",
            room_id=destination.id,
        )

    hit = roll_ambush(ambush_candidates(snapshot, actor, content), lambda a: a.chance_on_enter, rng)
    if hit is not None:
        apply_ambush(builder, snapshot, actor, *hit, content, rules, now, rng)
    return builder.build()
