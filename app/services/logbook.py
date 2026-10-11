"""Log entries for every recipient of an event (ARCHITECTURE.md, "Ablauf einer Aktion").

Written in the same transaction as the state change, so a reconnecting client can catch up on
what it missed via ``GET /log?after=``. Shared by player actions and the world ticker.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.db.models import LogEntry, Player
from app.engine.events import ActorAudience, GameEvent, GroupAudience, PlayersAudience, RoomAudience


async def write_log(
    session: AsyncSession,
    events: Sequence[GameEvent],
    now: datetime,
    *,
    actor_id: UUID | None = None,
) -> None:
    """Every recipient gets a log entry. Global announcements are not logged per player.

    ``actor_id`` receives the ``Actor`` events; results without an actor (world ticker) have none.
    """
    for event in events:
        session.add_all(
            LogEntry(player_id=pid, kind=event.kind, text=event.text, created_at=now)
            for pid in await recipients(session, event, actor_id=actor_id)
        )


async def recipients(
    session: AsyncSession, event: GameEvent, *, actor_id: UUID | None = None
) -> list[UUID]:
    audience = event.audience
    excluded = set(event.exclude_player_ids)
    if isinstance(audience, ActorAudience):
        return [] if actor_id is None else [actor_id]
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
