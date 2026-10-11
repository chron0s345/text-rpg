"""Logging in, logging out and losing the connection (ARCHITECTURE.md, "Einloggen, Ausloggen &
Verbindungsabbruch").

- login: online; a dead character can only wake up at its checkpoint.
- logout, not in combat: trade cancelled, back to town, leaves the group, offline.
- logout in combat: needs ``confirm_death``; then death with loss rolls, offline.
- connection lost: ``disconnected_at = now``, trade cancelled, still in the world.
- grace period over (world ticker): like logout; in combat death with loss rolls, offline.

A dead character stays where it died (``respawn`` takes it to its checkpoint), so only exploring
characters are moved to town.
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Literal

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.death import apply_player_death
from app.engine.events import ActionResult, ResultBuilder, RoomAudience
from app.engine.groups import leave_group
from app.engine.state import ActionSnapshot, PlayerSnapshot

LogoutReason = Literal["logout", "disconnect"]

CONFIRM_DEATH_KIND = "logout.confirm_death"
CONFIRM_DEATH = (
    "Du bist im Kampf. Wenn du dich jetzt ausloggst, stirbt dein Charakter. Trotzdem ausloggen?"
)


def cancel_trades(builder: ResultBuilder, snapshot: ActionSnapshot, player: PlayerSnapshot) -> None:
    """Cancels the player's active trades (requires ``snapshot.trades`` to be loaded)."""
    for trade in snapshot.trades:
        if player.id not in (trade.initiator_id, trade.partner_id):
            continue
        builder.changes.trades_cancel.append(trade.id)
        builder.to_player(
            trade.other(player.id),
            "trade.cancelled",
            f"Der Handel mit {player.name} wurde abgebrochen.",
            trade_id=str(trade.id),
        )
        builder.to_player(
            player.id, "trade.cancelled", "Dein Handel wurde abgebrochen.", trade_id=str(trade.id)
        )
    snapshot.trades = [
        t for t in snapshot.trades if player.id not in (t.initiator_id, t.partner_id)
    ]
    player.locked_item_ids = []


def resolve_login(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    *,
    already_online: bool = False,
) -> ActionResult:
    """The character enters the world. ``already_online``: it was still online (another tab,
    or back within the disconnect grace period); the room is not told again."""
    actor = snapshot.actor
    builder = ResultBuilder(actor_id=actor.id)
    builder.update_player(actor.id, online=True, disconnected_at=None)
    if actor.state == "dead":
        text = f"Willkommen zurück, {actor.name}. Du bist tot."
    else:
        room = content.get_room(actor.current_room_id)
        text = f"Willkommen zurück, {actor.name}. Du bist in {room.name}."
    builder.to_actor("player.login", text, dead=actor.state == "dead")
    if not already_online:
        builder.event(
            "player.login",
            RoomAudience(room_id=actor.current_room_id),
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
    *,
    confirm_death: bool = False,
    reason: LogoutReason = "logout",
) -> ActionResult:
    """Logout by the player, or forced when the disconnect grace period is over.

    Requires ``snapshot.trades`` and the group with all members locked. A forced logout has no
    actor (world ticker): the character is told via its player channel and log.
    """
    actor = snapshot.actor
    forced = reason == "disconnect"
    builder = ResultBuilder(actor_id=None if forced else actor.id)
    if actor.state == "in_combat" and not forced and not confirm_death:
        builder.to_actor(CONFIRM_DEATH_KIND, CONFIRM_DEATH)
        return builder.build()

    old_room = actor.current_room_id
    cancel_trades(builder, snapshot, actor)
    if actor.state == "in_combat":
        apply_player_death(builder, snapshot, actor, content, rules, rng)
    elif actor.state == "exploring" and actor.current_room_id != rules.town_room_id:
        actor.current_room_id = rules.town_room_id
        builder.update_player(actor.id, current_room_id=rules.town_room_id)
    leave_group(builder, snapshot, actor)
    builder.update_player(actor.id, online=False, disconnected_at=None)

    text = (
        "Deine Verbindung ist abgerissen. Du wurdest ausgeloggt."
        if forced
        else "Du hast dich ausgeloggt."
    )
    builder.to_player(actor.id, "player.logout", text)
    builder.event(
        "player.logout",
        RoomAudience(room_id=old_room),
        f"{actor.name} verlässt die Welt.",
        exclude=[actor.id],
        player_id=str(actor.id),
    )
    return builder.build()


def resolve_disconnect(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
) -> ActionResult:
    """The connection was lost: the grace period starts, an active trade is cancelled. The
    character stays in the world and can still be attacked."""
    actor = snapshot.actor
    builder = ResultBuilder()  # nobody to answer: the connection is gone
    cancel_trades(builder, snapshot, actor)
    builder.update_player(actor.id, disconnected_at=now)
    return builder.build()


def disconnect_expired(disconnected_at: datetime | None, now: datetime, rules: Rules) -> bool:
    """Whether the grace period (``disconnect_grace_seconds``) of a lost connection is over."""
    if disconnected_at is None:
        return False
    return (now - disconnected_at).total_seconds() >= rules.timings.disconnect_grace_seconds
