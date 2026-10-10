"""Alembic environment (async, asyncpg).

URL: ``DATABASE_URL`` (see ``app/db/session.py``). Tests may hand over an existing sync
connection via ``config.attributes["connection"]``.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlmodel import SQLModel

import app.db.models  # noqa: F401  (registers all tables on SQLModel.metadata)
from app.db.session import create_engine, database_url

config = context.config
target_metadata = SQLModel.metadata


def _configure(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_engine()
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_configure)
            await connection.commit()
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
    else:
        asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
