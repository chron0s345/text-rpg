"""Simple actions without their own module: chat (``say``, ``group_say``, ``whisper``),
``inspect``, ``inspect_player``, ``rest``, ``look``, ``respawn``."""

from __future__ import annotations

import random
from datetime import datetime
from uuid import UUID

from app.content.describe import SLOT_NAMES, item_summary, set_bonuses_text
from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.achievements import species_killed, title_of
from app.engine.events import (
    ActionResult,
    GroupAudience,
    PlayersAudience,
    ResultBuilder,
    RoomAudience,
    rejected,
)
from app.engine.group_status import report_hp, report_location
from app.engine.movement import DIRECTION_NAMES
from app.engine.permissions import check_state
from app.engine.quests import count_visit
from app.engine.regeneration import calculate_current_monster_hp
from app.engine.state import ActionSnapshot, PlayerRef
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


def resolve_group_say(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    text: str,
) -> ActionResult:
    """Group chat: reaches every member, in whatever room."""
    actor = snapshot.actor
    if denied := check_state("group_say", actor):
        return denied
    if actor.group_id is None:
        return rejected("Du bist in keiner Gruppe.")
    message = " ".join(text.split())
    if not message:
        return rejected("Was möchtest du sagen?")

    builder = ResultBuilder(actor_id=actor.id)
    builder.to_actor("chat.group", f"[Gruppe] Du: {message}", message=message)
    builder.event(
        "chat.group",
        GroupAudience(group_id=actor.group_id),
        f"[Gruppe] {actor.name}: {message}",
        exclude=[actor.id],
        player_id=str(actor.id),
        message=message,
    )
    return builder.build()


def resolve_whisper(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    recipient: PlayerRef | None,
    text: str,
) -> ActionResult:
    """A private message to one online character anywhere. ``recipient``: looked up by name
    by the caller, None if nobody of that name is online."""
    actor = snapshot.actor
    if denied := check_state("whisper", actor):
        return denied
    if recipient is None:
        return rejected("Niemand mit diesem Namen ist gerade im Spiel.")
    if recipient.id == actor.id:
        return rejected("Du murmelst etwas vor dich hin.")
    message = " ".join(text.split())
    if not message:
        return rejected("Was möchtest du flüstern?")

    builder = ResultBuilder(actor_id=actor.id)
    builder.to_actor(
        "chat.whisper", f"Du flüsterst {recipient.name} zu: {message}", message=message
    )
    builder.event(
        "chat.whisper",
        PlayersAudience(player_ids=[recipient.id]),
        f"{actor.name} flüstert dir zu: {message}",
        from_name=actor.name,
        message=message,
    )
    return builder.build()


def resolve_inspect(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    item_id: str,
) -> ActionResult:
    """Description and numbers of an item, into the actor's log (shop offers, the ground, the
    backpack: item texts are no secret). Lore items tell their story this way."""
    actor = snapshot.actor
    if denied := check_state("inspect", actor):
        return denied
    item = content.items.get(item_id)
    if item is None:
        return rejected("So etwas kennst du nicht.")
    summary = item_summary(item)
    text = f"{item.name}: {item.description}" + (f" ({summary})" if summary else "")
    if item.item_set is not None:
        pieces = ", ".join(content.get_item(i).name for i in item.item_set.items)
        text += f" Set-Teile: {pieces}. Boni: {set_bonuses_text(item.item_set)}."
    builder = ResultBuilder(actor_id=actor.id)
    builder.to_actor("item.inspected", text, item_id=item.id)
    return builder.build()


def resolve_inspect_player(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    player_id: UUID,
) -> ActionResult:
    """What anyone can see of a character in the same room: level, title, effective stats,
    worn gear, achievements and how many kinds of monsters they have slain. The one looked at
    notices."""
    actor = snapshot.actor
    if denied := check_state("inspect_player", actor):
        return denied
    target = snapshot.player(player_id)
    if target is None:
        return rejected("Diese Person ist nicht hier.")
    stats = stats_of(target, content.items)
    title = title_of(target, content)
    worn = {
        i.equipped_slot: content.get_item(i.item_id).name
        for i in target.inventory
        if i.equipped_slot
    }
    gear = [
        f"{label}: {worn[slot]}"
        for slot, label in SLOT_NAMES.items()
        if slot in worn and slot != "bag"
    ]
    earned = sum(1 for a in target.achievements if a in content.achievements)
    lines = [
        f"{target.name}{f' {title}' if title else ''} – Stufe {target.level}.",
        f"Angriff {stats.attack} · Verteidigung {stats.defense} · max. HP {stats.max_hp}.",
        "Trägt: " + ("; ".join(gear) if gear else "nichts Nennenswertes") + ".",
        f"Erfolge: {earned}/{len(content.achievements)} · "
        f"Bestiarium: {len(species_killed(target, content))} Arten.",
    ]
    builder = ResultBuilder(actor_id=actor.id)
    builder.to_actor("player.inspected", "\n".join(lines), player_id=str(target.id))
    if target.id != actor.id:
        builder.event(
            "player.inspected_by",
            PlayersAudience(player_ids=[target.id]),
            f"{actor.name} mustert dich.",
            player_id=str(actor.id),
        )
    return builder.build()


def resolve_rest(
    snapshot: ActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
) -> ActionResult:
    """Players do not regenerate; in a safe room (town, camps, refuges) resting heals fully,
    for free. Out in the wild only food and potions heal."""
    actor = snapshot.actor
    if denied := check_state("rest", actor):
        return denied
    room = content.get_room(actor.current_room_id)
    if not room.safe:
        return rejected("Rasten kannst du nur an einem sicheren Ort.")
    max_hp = stats_of(actor, content.items).max_hp
    if actor.hp >= max_hp:
        return rejected("Du bist schon bei vollen Kräften.")
    actor.hp = max_hp
    builder = ResultBuilder(actor_id=actor.id)
    builder.update_player(actor.id, hp=max_hp)
    builder.to_actor("player.rested", f"Du rastest in {room.name} und kommst wieder zu Kräften.")
    report_hp(builder, actor)
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
    count_visit(builder, actor, content)  # waking up at a quest's destination counts
    return builder.build()
