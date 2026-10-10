"""Logging a character in and out (ARCHITECTURE.md, "Einloggen, Ausloggen & Verbindungsabbruch").

``online``/``disconnected_at`` are written through ``PlayerUpdate`` like any other change, so
login and logout go through the same lock -> engine -> apply -> commit -> publish path as actions.

A lost connection is handled by the world ticker: once ``disconnect_grace_seconds`` have passed
(``connection_expired``), the character is logged out like ``logout`` with ``confirm_death``
(``resolve_connection_lost``).

Not covered yet (later steps): leaving the group and cancelling a running trade on logout. Until
groups and trades exist, a character has neither.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Final

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.death import apply_player_death
from app.engine.events import ActionResult, ResultBuilder, RoomAudience
from app.engine.permissions import check_state
from app.engine.state import ActionSnapshot

LOGGED_IN: Final = "session.logged_in"
LOGGED_OUT: Final = "session.logged_out"
CONFIRM_REQUIRED: Final = "session.confirm_death"


def resolve_login(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    *,
    already_online: bool,
) -> ActionResult:
    """Character selected: online, connection deadline cleared.

    A character that is already online (second tab, reconnect within the grace period) simply
    continues; the room is only told when the character really enters the world.
    """
    actor = snapshot.actor
    room = content.get_room(actor.current_room_id)
    builder = ResultBuilder(actor_id=actor.id)
    builder.update_player(actor.id, online=True, disconnected_at=None)

    if actor.state == "dead":
        text = f"Willkommen, {actor.name}. Du bist tot – erwache am Checkpoint."
    else:
        text = f"Willkommen, {actor.name}. Du bist in {room.name}."
    builder.to_actor(LOGGED_IN, text, room_id=room.id, state=actor.state)
    if not already_online:
        builder.event(
            "player.entered_world",
            RoomAudience(room_id=room.id),
            f"{actor.name} betritt die Welt.",
            exclude=[actor.id],
            player_id=str(actor.id),
        )
    return builder.build()


def resolve_logout(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    confirm_death: bool,
) -> ActionResult:
    """Logout. Not in combat: back to ``town_room_id``. In combat: only with ``confirm_death``,
    then the character dies with loss rolls (no escaping a lost fight by logging out). Dead
    characters stay where they are and wake up at their checkpoint after the next login."""
    actor = snapshot.actor
    if denied := check_state("logout", actor):
        return denied
    builder = ResultBuilder(actor_id=actor.id)

    if actor.state == "in_combat" and not confirm_death:
        builder.to_actor(
            CONFIRM_REQUIRED,
            "Du bist im Kampf. Wenn du dich jetzt ausloggst, stirbt dein Charakter.",
        )
        return builder.build()

    origin = actor.current_room_id
    if actor.state == "in_combat":
        apply_player_death(builder, snapshot, actor, content, rules, rng)
    elif actor.state == "exploring" and origin != rules.town_room_id:
        actor.current_room_id = rules.town_room_id
        builder.update_player(actor.id, current_room_id=rules.town_room_id)

    builder.update_player(actor.id, online=False, disconnected_at=None)
    builder.to_actor(LOGGED_OUT, "Du verlässt die Welt.")
    builder.event(
        "player.left_world",
        RoomAudience(room_id=origin),
        f"{actor.name} verlässt die Welt.",
        exclude=[actor.id],
        player_id=str(actor.id),
    )
    return builder.build()


def connection_expired(disconnected_at: datetime | None, now: datetime, rules: Rules) -> bool:
    if disconnected_at is None:
        return False
    return now - disconnected_at >= timedelta(seconds=rules.timings.disconnect_grace_seconds)


def resolve_connection_lost(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
) -> ActionResult:
    """Grace period over: like a confirmed logout. Out of combat back to town; in combat the
    character dies with loss rolls (closing the tab never saves a lost fight)."""
    return resolve_logout(snapshot, content, rules, now, rng, confirm_death=True)
