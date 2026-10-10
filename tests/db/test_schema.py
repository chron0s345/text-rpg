"""Migrations match the models; database-level integrity rules hold."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlmodel import SQLModel

from app.db.models import (
    BankItem,
    CombatParticipation,
    FloorItem,
    Group,
    GroupInvite,
    InventoryItem,
    MonsterInstance,
    Trade,
    TradeOffer,
)
from app.db.session import create_engine
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


# --- remaining constraints -----------------------------------------------------------------


async def test_more_check_constraints(session: AsyncSession) -> None:
    a = await add_player(session)
    b = await add_player(session)
    monster = MonsterInstance(monster_id="goblin", room_id="x", hp=1)
    item = InventoryItem(player_id=a.id, item_id="bread")
    session.add_all([monster, item])
    await session.flush()

    def trade(**fields: Any) -> Trade:
        data: dict[str, Any] = {
            "initiator_id": a.id,
            "partner_id": b.id,
            "room_id": "x",
            "expires_at": NOW,
        }
        data.update(fields)
        return Trade(**data)

    invalid: list[object] = [
        BankItem(player_id=a.id, item_id="bread", quantity=0),
        Group(leader_id=a.id, loot_cursor=-1),
        CombatParticipation(
            monster_instance_id=monster.id, player_id=a.id, party_key="k", damage_dealt=-1
        ),
        GroupInvite(inviter_id=a.id, player_id=a.id, expires_at=NOW),
        trade(partner_id=a.id),
        trade(initiator_gold=-1),
        trade(status="stolen"),
        trade(revision=-1),
    ]
    for row in invalid:
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(row)
    for field_values in ({"xp": -1}, {"group_joined_at": NOW}):
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await add_player(session, **field_values)

    open_trade = trade(status="open")
    session.add(open_trade)
    await session.flush()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                TradeOffer(
                    trade_id=open_trade.id, inventory_item_id=item.id, player_id=a.id, quantity=0
                )
            )


async def test_offered_item_cannot_be_deleted_but_account_can(session: AsyncSession) -> None:
    a = await add_player(session)
    b = await add_player(session)
    item = InventoryItem(player_id=a.id, item_id="bread")
    session.add(item)
    open_trade = Trade(initiator_id=a.id, partner_id=b.id, room_id="x", expires_at=NOW)
    session.add(open_trade)
    await session.flush()
    session.add(
        TradeOffer(trade_id=open_trade.id, inventory_item_id=item.id, player_id=a.id, quantity=1)
    )
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                text("DELETE FROM inventory_item WHERE id = :id"), {"id": item.id}
            )

    # deleting the whole account cascades through players, trades and offers
    async with session.begin_nested():
        await session.execute(text("DELETE FROM account WHERE id = :id"), {"id": a.account_id})
    remaining = await session.scalar(text("SELECT count(*) FROM trade_offer"))
    assert remaining == 0


async def test_constraint_and_index_definitions_match_models(
    engine: AsyncEngine, database_url: str
) -> None:
    """compare_metadata misses changed CHECK expressions and partial-index WHERE clauses;
    compare the real definitions against a database built directly from the models."""
    queries = {
        "constraints": "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
        "FROM pg_constraint WHERE connamespace = 'public'::regnamespace "
        "AND conrelid::regclass::text <> 'alembic_version'",
        "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename <> 'alembic_version'",
    }

    async def definitions(target: AsyncEngine) -> dict[str, set[tuple[Any, ...]]]:
        async with target.connect() as connection:
            return {
                key: {tuple(row) for row in (await connection.execute(text(sql))).all()}
                for key, sql in queries.items()
            }

    check_db = f"schema_check_{uuid4().hex[:8]}"
    async with engine.connect() as admin:
        admin = await admin.execution_options(isolation_level="AUTOCOMMIT")
        await admin.execute(text(f'CREATE DATABASE "{check_db}"'))
    reference = create_engine(
        make_url(database_url).set(database=check_db).render_as_string(hide_password=False)
    )
    try:
        async with reference.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        expected = await definitions(reference)
    finally:
        await reference.dispose()
        async with engine.connect() as admin:
            admin = await admin.execution_options(isolation_level="AUTOCOMMIT")
            await admin.execute(text(f'DROP DATABASE "{check_db}"'))

    actual = await definitions(engine)
    assert actual["constraints"] == expected["constraints"]
    assert actual["indexes"] == expected["indexes"]
