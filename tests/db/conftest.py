"""PostgreSQL via Testcontainers. Tests in this package are skipped when Docker is unavailable.

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

ROOT = Path(__file__).resolve().parents[2]


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
                    text("DELETE FROM monster_instance WHERE room_id = :r"), {"r": room}
                )
            for account_id in self.accounts:
                await db.execute(text("DELETE FROM account WHERE id = :id"), {"id": account_id})


async def wait_until_blocked(engine: AsyncEngine, timeout: float = 5.0) -> None:
    """Waits until some other connection is waiting for a row lock."""
    import asyncio

    from sqlalchemy import text

    async with engine.connect() as probe:
        for _ in range(int(timeout / 0.05)):
            waiting = await probe.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND datname = current_database()"
                )
            )
            if waiting:
                return
            await asyncio.sleep(0.05)
    raise AssertionError("no transaction is waiting for a lock")
