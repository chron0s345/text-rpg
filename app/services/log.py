"""Per-player log entries (ARCHITECTURE.md, "Ablauf einer Aktion"): every recipient of an event
gets one, so a reconnecting client can catch up via ``GET /log?after=``."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.db.models import LogEntry, Player
from app.engine.events import (
    ActorAudience,
    GameEvent,
    GroupAudience,
    PlayersAudience,
    RoomAudience,
)


async def write_log(
    session: AsyncSession,
    actor_id: UUID,
    events: Sequence[GameEvent],
    now: datetime,
) -> None:
    """Adds the entries in the caller's transaction. Recipients: actor, ``Players``, online
    players in the room, group members; excluded players do not get one. Global announcements
    are not logged per player."""
    for event in events:
        session.add_all(
            LogEntry(player_id=pid, kind=event.kind, text=event.text, created_at=now)
            for pid in await _recipients(session, actor_id, event)
        )


async def _recipients(session: AsyncSession, actor_id: UUID, event: GameEvent) -> list[UUID]:
    audience = event.audience
    excluded = set(event.exclude_player_ids)
    if isinstance(audience, ActorAudience):
        return [actor_id]
    if isinstance(audience, PlayersAudience):
        candidates = list(audience.player_ids)
    elif isinstance(audience, RoomAudience):
        candidates = list(
            (
                await session.scalars(
                    select(col(Player.id)).where(
                        col(Player.online),
                        col(Player.deleted_at).is_(None),
                        col(Player.current_room_id) == audience.room_id,
                    )
                )
            ).all()
        )
    elif isinstance(audience, GroupAudience):
        candidates = list(
            (
                await session.scalars(
                    select(col(Player.id)).where(
                        col(Player.group_id) == audience.group_id,
                        col(Player.deleted_at).is_(None),
                    )
                )
            ).all()
        )
    else:
        return []
    return [pid for pid in candidates if pid not in excluded]
