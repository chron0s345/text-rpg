"""Due monster respawns (ARCHITECTURE.md, "Respawn & Aufräumen").

Used by the world ticker and lazily when an action loads a room, so players never see a monster
that should already be back.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from app.content.registry import ContentRegistry
from app.engine.events import ActionResult, ResultBuilder, RoomAudience
from app.engine.state import ActionSnapshot, MonsterSnapshot


def respawn_due_monsters(
    snapshot: ActionSnapshot, content: ContentRegistry, now: datetime
) -> ActionResult:
    """Lazy check before an action: the actor learns about it with its response."""
    return respawn_monsters(snapshot.monsters, content, now, actor_id=snapshot.actor.id)


def respawn_monsters(
    monsters: Iterable[MonsterSnapshot],
    content: ContentRegistry,
    now: datetime,
    *,
    actor_id: UUID | None = None,
) -> ActionResult:
    """Dead monsters with ``respawn_at <= now`` come back with full HP, untagged, no
    regeneration history (mutates the snapshots so a following action sees them).

    Without ``actor_id`` (world ticker) only the room is told."""
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
