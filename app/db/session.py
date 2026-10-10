"""Async engine/session setup (PostgreSQL via asyncpg)."""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DEFAULT_DATABASE_URL = "postgresql+asyncpg://rpg:rpg@localhost:5432/rpg"


def database_url() -> str:
    """``DATABASE_URL`` from the environment; defaults to the docker-compose dev database."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def create_engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(url or database_url(), pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: rows stay readable after commit (events are built after commit)
    return async_sessionmaker(engine, expire_on_commit=False)
