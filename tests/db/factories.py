"""Row builders for database tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, Player

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


async def add_account(session: AsyncSession, username: str | None = None) -> Account:
    account = Account(username=username or f"user-{uuid4().hex[:8]}", password_hash="x")
    session.add(account)
    await session.flush()
    return account


async def add_player(
    session: AsyncSession, account: Account | None = None, **fields: Any
) -> Player:
    account = account or await add_account(session)
    data: dict[str, Any] = {
        "account_id": account.id,
        "name": f"P{uuid4().hex[:8]}",
        "hp": 20,
        "base_max_hp": 20,
        "base_attack": 5,
        "base_defense": 2,
        "current_room_id": "dark_forest",
        "checkpoint_room_id": "town_square",
        "online": True,
    }
    data.update(fields)
    player = Player(**data)
    session.add(player)
    await session.flush()
    return player
