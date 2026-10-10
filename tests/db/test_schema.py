"""Migrations match the models; database-level integrity rules hold."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlmodel import SQLModel

from app.db.models import FloorItem, InventoryItem, MonsterInstance
from tests.db.factories import NOW, add_account, add_player


async def test_migrations_match_models(engine: AsyncEngine) -> None:
    def diff(connection: Connection) -> list[object]:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return list(compare_metadata(context, SQLModel.metadata))

    async with engine.connect() as connection:
        assert await connection.run_sync(diff) == []


async def test_only_one_online_character_per_account(session: AsyncSession) -> None:
    account = await add_account(session)
    await add_player(session, account, online=True)
    await add_player(session, account, online=False)  # offline characters are fine
    with pytest.raises(IntegrityError):
        await add_player(session, account, online=True)


async def test_names_are_unique_case_insensitive_unless_deleted(session: AsyncSession) -> None:
    first = await add_player(session, name="Anna", online=False)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await add_player(session, name="ANNA", online=False)
    first.deleted_at = NOW
    await session.flush()
    await add_player(session, name="anna", online=False)  # soft-deleted name is free again


async def test_usernames_are_unique_case_insensitive(session: AsyncSession) -> None:
    await add_account(session, "Bob")
    with pytest.raises(IntegrityError):
        await add_account(session, "bob")


@pytest.mark.parametrize(
    "field_values",
    [{"gold": -1}, {"hp": -1}, {"level": 0}, {"state": "flying"}],
    ids=["gold", "hp", "level", "state"],
)
async def test_player_check_constraints(
    session: AsyncSession, field_values: dict[str, Any]
) -> None:
    with pytest.raises(IntegrityError):
        await add_player(session, **field_values)


async def test_item_and_floor_constraints(session: AsyncSession) -> None:
    player = await add_player(session)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(InventoryItem(player_id=player.id, item_id="bread", quantity=0))
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(InventoryItem(player_id=player.id, item_id="bread", equipped_slot="pocket"))
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            # dropped/looted items must expire
            session.add(FloorItem(room_id="x", item_id="bread", source="drop"))
    session.add(FloorItem(room_id="x", item_id="bread", source="spawn"))
    session.add(
        FloorItem(room_id="x", item_id="bread", source="loot", expires_at=NOW + timedelta(1))
    )
    await session.flush()


async def test_monster_hp_never_negative(session: AsyncSession) -> None:
    session.add(MonsterInstance(monster_id="goblin", room_id="x", hp=-3))
    with pytest.raises(IntegrityError):
        await session.flush()
