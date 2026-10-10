"""Simple actions without their own module: ``say``, ``look``, ``respawn``."""

from __future__ import annotations

import random
from datetime import datetime

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.events import ActionResult, ResultBuilder, RoomAudience, rejected
from app.engine.group_status import report_hp, report_location
from app.engine.movement import DIRECTION_NAMES
from app.engine.permissions import check_state
from app.engine.regeneration import calculate_current_monster_hp
from app.engine.state import ActionSnapshot
from app.engine.stats import stats_of


def resolve_say(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    text: str,
) -> ActionResult:
    """Room chat. ``text`` is plain text; escaping happens when rendering (Jinja autoescape)."""
    actor = snapshot.actor
    if denied := check_state("say", actor):
        return denied
    message = " ".join(text.split())
    if not message:
        return rejected("Was möchtest du sagen?")

    builder = ResultBuilder(actor_id=actor.id)
    builder.to_actor("chat.say", f"Du sagst: {message}", message=message)
    builder.event(
        "chat.say",
        RoomAudience(room_id=actor.current_room_id),
        f"{actor.name} sagt: {message}",
        exclude=[actor.id],
        player_id=str(actor.id),
        message=message,
    )
    return builder.build()


def resolve_look(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
) -> ActionResult:
    """Describes the actor's room. Monster HP includes regeneration (display only, not stored)."""
    actor = snapshot.actor
    if denied := check_state("look", actor):
        return denied
    room = content.get_room(actor.current_room_id)

    monsters = []
    for monster in snapshot.monsters:
        if not monster.alive or monster.room_id != room.id:
            continue
        template = content.get_monster(monster.monster_id)
        hp = calculate_current_monster_hp(template, monster.hp, monster.last_combat_at, now)
        monsters.append(
            {"id": str(monster.id), "name": template.name, "hp": hp, "max_hp": template.max_hp}
        )
    players = [
        {"id": str(p.id), "name": p.name, "state": p.state}
        for p in snapshot.room_players
        if p.current_room_id == room.id
    ]
    merchants = [{"id": m.id, "name": m.name} for m in content.merchants_in_room(room.id)]
    floor_items = []
    for floor in snapshot.floor_items:
        if floor.room_id != room.id or not floor.visible_at(now):
            continue
        item = content.get_item(floor.item_id)
        floor_items.append(
            {"id": str(floor.id), "item_id": item.id, "name": item.name, "quantity": floor.quantity}
        )

    lines = [room.name, room.description]
    if room.exits:
        lines.append("Ausgänge: " + ", ".join(DIRECTION_NAMES[d] for d in room.exits))
    if monsters:
        lines.append("Gegner: " + ", ".join(f"{m['name']} ({m['hp']} HP)" for m in monsters))
    if players:
        lines.append("Hier sind: " + ", ".join(str(p["name"]) for p in players))
    if merchants:
        lines.append("Händler: " + ", ".join(m["name"] for m in merchants))
    if floor_items:
        lines.append(
            "Am Boden: "
            + ", ".join(
                f"{f['quantity']}x {f['name']}" if f["quantity"] != 1 else str(f["name"])
                for f in floor_items
            )
        )

    builder = ResultBuilder(actor_id=actor.id)
    builder.to_actor(
        "room.look",
        "\n".join(lines),
        room_id=room.id,
        name=room.name,
        description=room.description,
        exits=dict(room.exits),
        safe=room.safe,
        bank=room.bank,
        monsters=monsters,
        players=players,
        merchants=merchants,
        floor_items=floor_items,
    )
    return builder.build()


def resolve_respawn(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
) -> ActionResult:
    """Dead -> wake up at ``checkpoint_room_id`` with full HP (effective max incl. equipment).

    The snapshot describes the checkpoint room. No ambush on waking up.
    """
    actor = snapshot.actor
    if denied := check_state("respawn", actor):
        return denied
    checkpoint = content.get_room(actor.checkpoint_room_id)

    builder = ResultBuilder(actor_id=actor.id)
    death_room_id = actor.current_room_id
    actor.hp = stats_of(actor, content.items).max_hp
    actor.state = "exploring"
    actor.combat_target_id = None
    actor.current_room_id = checkpoint.id
    builder.update_player(
        actor.id,
        hp=actor.hp,
        state="exploring",
        combat_target_id=None,
        current_room_id=checkpoint.id,
    )
    builder.to_actor(
        "player.respawned", f"Du erwachst in {checkpoint.name}.", room_id=checkpoint.id
    )
    builder.event(
        "player.respawned",
        RoomAudience(room_id=checkpoint.id),
        f"{actor.name} erwacht.",
        exclude=[actor.id],
        player_id=str(actor.id),
    )
    if death_room_id != checkpoint.id:
        builder.event(
            "player.left",
            RoomAudience(room_id=death_room_id),
            f"Der leblose Körper von {actor.name} verblasst.",
            exclude=[actor.id],
            player_id=str(actor.id),
        )
    report_hp(builder, actor)
    report_location(builder, actor, checkpoint.name)
    return builder.build()
