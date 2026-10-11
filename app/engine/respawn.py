"""Due monster and item respawns (ARCHITECTURE.md, "Respawn & Aufräumen").

Monster respawns run in the world ticker and lazily when an action loads a room, so players never
see a monster that should already be back. Spawn items only come back through the ticker.
"""

from __future__ import annotations

import random
from collections.abc import Collection, Sequence
from datetime import datetime
from uuid import UUID

from app.content.registry import ContentRegistry
from app.engine.events import (
    ActionResult,
    FloorItemCreate,
    ItemSpawnUpdate,
    ResultBuilder,
    RoomAudience,
)
from app.engine.inventory import item_label
from app.engine.state import ActionSnapshot, ItemSpawnSnapshot, MonsterSnapshot, new_id


def respawn_monsters(
    monsters: Sequence[MonsterSnapshot],
    content: ContentRegistry,
    now: datetime,
    *,
    actor_id: UUID | None = None,
) -> ActionResult:
    """Dead monsters with ``respawn_at <= now`` come back with full HP, untagged, no
    regeneration history (mutates the snapshots so a following action sees them).

    With an ``actor_id`` (lazy respawn before an action) the actor is told directly and left out
    of the room broadcast; without one (world ticker) the room gets the only event.
    """
    builder = ResultBuilder(actor_id=actor_id)
    for monster in monsters:
        if monster.alive or monster.respawn_at is None or monster.respawn_at > now:
            continue
        template = content.get_monster(monster.monster_id)
        monster.alive = True
        monster.hp = template.max_hp
        monster.respawn_at = None
        monster.last_combat_at = None
        monster.tagged_by_party = None
        builder.update_monster(
            monster.id,
            alive=True,
            hp=template.max_hp,
            respawn_at=None,
            last_combat_at=None,
            tagged_by_party=None,
        )
        text = f"{template.name} taucht auf."
        if actor_id is not None:
            builder.to_actor("monster.respawned", text, monster_instance_id=str(monster.id))
        builder.event(
            "monster.respawned",
            RoomAudience(room_id=monster.room_id),
            text,
            exclude=[actor_id] if actor_id is not None else [],
            monster_instance_id=str(monster.id),
        )
    return builder.build()


def respawn_due_monsters(
    snapshot: ActionSnapshot, content: ContentRegistry, now: datetime
) -> ActionResult:
    """Lazy respawn in the room of an action, before the action itself."""
    return respawn_monsters(snapshot.monsters, content, now, actor_id=snapshot.actor.id)


def respawn_due_items(
    spawns: Sequence[ItemSpawnSnapshot],
    lying: Collection[tuple[str, str]],
    content: ContentRegistry,
    now: datetime,
    rng: random.Random,
) -> ActionResult:
    """Spawn items whose ``next_spawn_at <= now`` reappear in their room with the rule's quantity.

    ``lying``: (room, item) pairs that still have a spawn floor item. Such a spawn only has its
    timer cleared, so a rule never produces a second pile. A rule that was removed from the
    content (or no longer respawns) is cleared without creating anything.
    """
    builder = ResultBuilder()
    for spawn in spawns:
        if spawn.next_spawn_at is None or spawn.next_spawn_at > now:
            continue
        builder.changes.item_spawns.append(
            ItemSpawnUpdate(room_id=spawn.room_id, item_id=spawn.item_id, next_spawn_at=None)
        )
        room = content.rooms.get(spawn.room_id)
        rule = (
            None
            if room is None
            else next((r for r in room.items if r.item_id == spawn.item_id), None)
        )
        if rule is None or (spawn.room_id, spawn.item_id) in lying:
            continue
        floor_id = new_id(rng)
        builder.changes.floor_items_create.append(
            FloorItemCreate(
                id=floor_id,
                room_id=spawn.room_id,
                item_id=spawn.item_id,
                quantity=rule.quantity,
                source="spawn",
            )
        )
        label = item_label(rule.quantity, content.get_item(spawn.item_id))
        builder.event(
            "floor.spawned",
            RoomAudience(room_id=spawn.room_id),
            f"{label} liegt jetzt hier.",
            floor_item_id=str(floor_id),
            item_id=spawn.item_id,
            quantity=rule.quantity,
        )
    return builder.build()
