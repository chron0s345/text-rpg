"""Deleting old log entries (``log_entry`` grows with every event for every recipient).

There is no index on ``created_at`` (and none is needed): ids come from a sequence, so they grow
with the time of writing. A binary search over the primary key finds the first entry that is
still young enough with about log2(id range) index lookups; everything below that id is deleted
in batches of id ranges, each in its own short transaction.

Clocks of several workers can differ by moments, so the boundary is approximate by that much;
entries are never deleted much earlier than ``keep``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.db.models import LogEntry

BATCH = 5_000


async def _first_written_at_or_after(session: AsyncSession, entry_id: int) -> datetime | None:
    return await session.scalar(
        select(col(LogEntry.created_at))
        .where(col(LogEntry.id) >= entry_id)
        .order_by(col(LogEntry.id))
        .limit(1)
    )


async def first_kept_id(session: AsyncSession, cutoff: datetime) -> int | None:
    """The smallest id from which on entries are written at or after ``cutoff``; ``None`` if the
    log is empty. Entries below it are older than ``cutoff``."""
    lowest: int | None = await session.scalar(select(func.min(col(LogEntry.id))))
    highest: int | None = await session.scalar(select(func.max(col(LogEntry.id))))
    if lowest is None or highest is None:
        return None
    low, high = lowest, highest
    high += 1  # "nothing to keep" is a valid answer
    while low < high:  # invariant: the answer lies in [low, high]
        middle = (low + high) // 2
        written = await _first_written_at_or_after(session, middle)
        if written is None or written >= cutoff:
            high = middle
        else:
            low = middle + 1
    return low


async def purge_log(
    sessions: async_sessionmaker[AsyncSession], now: datetime, keep: timedelta
) -> int:
    """Deletes log entries written before ``now - keep``. Returns how many."""
    async with sessions() as session:
        boundary = await first_kept_id(session, now - keep)
        oldest = await session.scalar(select(func.min(col(LogEntry.id))))
    if boundary is None or oldest is None:
        return 0
    removed = 0
    start = oldest
    while start < boundary:
        end = min(start + BATCH, boundary)
        async with sessions() as session, session.begin():
            result = await session.execute(
                delete(LogEntry)
                .where(col(LogEntry.id) >= start, col(LogEntry.id) < end)
                .returning(col(LogEntry.id))
            )
            removed += len(result.all())
        start = end
    return removed
