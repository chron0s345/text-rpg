"""Due monster respawns (ARCHITECTURE.md, "Respawn & Aufräumen").

Used by the world ticker and lazily when an action loads a room, so players never see a monster
that should already be back.
"""

from __future__ import annotations

from datetime import datetime

from app.content.registry import ContentRegistry
from app.engine.events import ActionResult, ResultBuilder, RoomAudience
from app.engine.state import ActionSnapshot


def respawn_due_monsters(
    snapshot: ActionSnapshot, content: ContentRegistry, now: datetime
) -> ActionResult:
    """Dead monsters with ``respawn_at <= now`` come back with full HP, untagged, no
    regeneration history (mutates the snapshot so a following action sees them)."""
    builder = ResultBuilder(actor_id=snapshot.actor.id)
    for monster in snapshot.monsters:
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
        builder.to_actor("monster.respawned", text, monster_instance_id=str(monster.id))
        builder.event(
            "monster.respawned",
            RoomAudience(room_id=monster.room_id),
            text,
            exclude=[snapshot.actor.id],
            monster_instance_id=str(monster.id),
        )
    return builder.build()
