"""PostgreSQL fixtures (pytest plugin, registered in tests/conftest.py) via Testcontainers.

Tests using these fixtures are skipped when Docker is unavailable (CI fails on skips).

The schema is created with the real Alembic migrations (upgrade -> downgrade -> upgrade, so the
downgrade path is exercised too). Each test runs in a transaction that is rolled back.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.session import create_engine

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    try:
        from testcontainers.community.postgres import PostgresContainer

        container = PostgresContainer("postgres:16-alpine", driver="asyncpg")
        container.start()
    except Exception as exc:  # Docker not installed / not running
        pytest.skip(f"PostgreSQL test container unavailable: {exc}")
    previous = os.environ.get("DATABASE_URL")
    try:
        url = container.get_connection_url()
        os.environ["DATABASE_URL"] = url
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        yield url
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        container.stop()


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Session inside an outer transaction that is always rolled back (commits become
    savepoints), so tests never see each other's data."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        db = AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield db
        finally:
            await db.close()
            await transaction.rollback()


@pytest.fixture
async def committed(engine: AsyncEngine) -> AsyncIterator[Committed]:
    """For tests with several concurrent transactions: data is really committed and removed
    afterwards (accounts cascade to their characters; rooms listed in ``rooms`` are cleared)."""
    helper = Committed(engine)
    try:
        yield helper
    finally:
        await helper.cleanup()


class Committed:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.accounts: list[object] = []
        self.rooms: list[str] = []

    def session(self) -> AsyncSession:
        return AsyncSession(self.engine, expire_on_commit=False)

    async def cleanup(self) -> None:
        from sqlalchemy import text

        async with self.session() as db, db.begin():
            for room in self.rooms:
                await db.execute(text("DELETE FROM floor_item WHERE room_id = :r"), {"r": room})
                await db.execute(
                    text("DELETE FROM item_spawn_state WHERE room_id = :r"), {"r": room}
                )
                await db.execute(
                    text("DELETE FROM monster_instance WHERE room_id = :r"), {"r": room}
                )
            for account_id in self.accounts:
                # the trade audit has no foreign keys (it outlives trades and players), so it is
                # not cascaded; seeded test RNGs reuse trade ids, so stale rows would leak
                await db.execute(
                    text(
                        "DELETE FROM trade_log WHERE trade_id IN (SELECT t.id FROM trade t "
                        "JOIN player p ON p.id IN (t.initiator_id, t.partner_id) "
                        "WHERE p.account_id = :id)"
                    ),
                    {"id": account_id},
                )
                await db.execute(text("DELETE FROM account WHERE id = :id"), {"id": account_id})


async def backend_pid(session: AsyncSession) -> int:
    """PostgreSQL backend pid of the session's connection (starts a transaction if needed)."""
    from sqlalchemy import text

    pid = await session.scalar(text("SELECT pg_backend_pid()"))
    assert isinstance(pid, int)
    return pid


async def wait_until_blocked(
    engine: AsyncEngine, blocker_pid: int | None = None, timeout: float = 5.0
) -> None:
    """Waits until a connection waits for a lock held by ``blocker_pid`` (any lock if None)."""
    import asyncio

    from sqlalchemy import text

    query = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND wait_event_type = 'Lock' "
        "AND (CAST(:blocker AS integer) IS NULL "
        "     OR CAST(:blocker AS integer) = ANY(pg_blocking_pids(pid)))"
    )
    async with engine.connect() as probe:
        for _ in range(int(timeout / 0.05)):
            if await probe.scalar(query, {"blocker": blocker_pid}):
                return
            await asyncio.sleep(0.05)
    raise AssertionError("no transaction is waiting for a lock")
