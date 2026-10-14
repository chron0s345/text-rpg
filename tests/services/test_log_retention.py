"""Old log entries are deleted: boundary found by binary search over ids, deleted in batches,
at most hourly by the world ticker, never with retention switched off."""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import col

from app.db.models import LogEntry
from app.services import log_retention
from app.services.log_retention import first_kept_id, purge_log
from app.services.world_ticker import WorldTicker
from tests.db.factories import add_player
from tests.services.conftest import ServiceHarness

KEEP = timedelta(days=30)


async def write_log(h: ServiceHarness, ages_in_days: list[float]) -> UUID:
    """One entry per age (oldest first, like real writing order) for a fresh character."""
    async with h.committed.session() as db, db.begin():
        player = await add_player(db, current_room_id="town_square")
        h.committed.accounts.append(player.account_id)
        for age in ages_in_days:
            db.add(
                LogEntry(
                    player_id=player.id,
                    created_at=h.clock.now - timedelta(days=age),
                    kind="test",
                    text=f"{age} Tage alt",
                )
            )
    return player.id


async def texts(h: ServiceHarness, player_id: UUID) -> list[str]:
    async with h.committed.session() as db:
        rows = await db.scalars(
            select(col(LogEntry.text))
            .where(col(LogEntry.player_id) == player_id)
            .order_by(col(LogEntry.id))
        )
        return list(rows.all())


async def test_old_entries_go_young_ones_stay(
    harness: ServiceHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    monkeypatch.setattr(log_retention, "BATCH", 3)  # several batches
    player_id = await write_log(h, [90, 60, 45, 40, 35, 31, 29, 10, 1, 0])

    removed = await purge_log(h.service.sessions, h.clock.now, KEEP)

    assert removed >= 6
    assert await texts(h, player_id) == ["29 Tage alt", "10 Tage alt", "1 Tage alt", "0 Tage alt"]


async def test_boundary_is_the_first_young_entry(harness: ServiceHarness) -> None:
    h = harness
    player_id = await write_log(h, [50, 40, 20, 5])
    async with h.committed.session() as db:
        ids = list(
            (
                await db.scalars(
                    select(col(LogEntry.id))
                    .where(col(LogEntry.player_id) == player_id)
                    .order_by(col(LogEntry.id))
                )
            ).all()
        )
        mine = [int(i) for i in ids if i is not None]
        assert await first_kept_id(db, h.clock.now - KEEP) == mine[2]
        # everything young: keep from the oldest; everything old: keep nothing below max + 1
        keep_all = await first_kept_id(db, h.clock.now - timedelta(days=365))
        assert keep_all is not None and keep_all <= mine[0]
        assert await first_kept_id(db, h.clock.now + timedelta(days=1)) == mine[-1] + 1


@pytest.fixture
async def ticker(harness: ServiceHarness, engine: AsyncEngine) -> AsyncIterator[WorldTicker]:
    h = harness
    ticker = WorldTicker(
        engine,
        h.service.sessions,
        h.content,
        h.service,
        h.publisher,
        clock=h.clock,
        rng_factory=lambda: random.Random(1),
        log_retention=KEEP,
    )
    try:
        yield ticker
    finally:
        await ticker.release_lock()


async def test_ticker_purges_at_most_hourly(harness: ServiceHarness, ticker: WorldTicker) -> None:
    h = harness
    first = await write_log(h, [40])
    assert (await ticker.tick()).log_entries_removed >= 1
    assert await texts(h, first) == []

    second = await write_log(h, [40])
    h.clock.advance(minutes=30)
    assert (await ticker.tick()).log_entries_removed == 0  # purged less than an hour ago
    assert await texts(h, second) == ["40 Tage alt"]

    h.clock.advance(minutes=31)
    assert (await ticker.tick()).log_entries_removed >= 1
    assert await texts(h, second) == []


async def test_no_retention_keeps_everything(harness: ServiceHarness, engine: AsyncEngine) -> None:
    h = harness
    player_id = await write_log(h, [400])
    ticker = WorldTicker(
        engine, h.service.sessions, h.content, h.service, h.publisher, clock=h.clock
    )

    report = await ticker.tick()

    assert report.log_entries_removed == 0
    assert await texts(h, player_id) == ["400 Tage alt"]
