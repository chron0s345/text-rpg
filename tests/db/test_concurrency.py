"""Real interleavings of two transactions against PostgreSQL (no mocks).

Pattern: transaction B holds a row lock and changes data; transaction A starts loading a snapshot
and blocks on that lock (verified via pg_stat_activity); B commits; A must see B's data.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from datetime import timedelta
from typing import Any, TypeVar
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.content.loader import load_content
from app.db.models import Group, MonsterInstance, Player
from app.db.repository import Repository, SnapshotChangedError, StaleRepositoryError
from app.db.seed import seed
from app.engine.events import PlayerUpdate, StateChanges
from app.engine.state import ActionSnapshot
from tests.conftest import DATA_DIR
from tests.db.factories import NOW, add_player
from tests.pg import Committed, backend_pid, wait_until_blocked


async def setup_actor(committed: Committed, room: str) -> Player:
    committed.rooms.append(room)
    async with committed.session() as db, db.begin():
        actor = await add_player(db, current_room_id=room, gold=100)
        committed.accounts.append(actor.account_id)
    return actor


async def lock_player(db: AsyncSession, player_id: object) -> Player:
    return (
        await db.scalars(select(Player).where(col(Player.id) == player_id).with_for_update())
    ).one()


T = TypeVar("T")


async def against_writer(
    committed: Committed, writer: AsyncSession, load: Callable[[], Coroutine[Any, Any, T]]
) -> T:
    """Runs ``load`` while ``writer`` holds row locks: waits until ``load`` is blocked by the
    writer, commits the writer and returns ``load``'s result.

    Never hangs: on any failure the writer is rolled back first (releasing its locks so ``load``
    can finish), and every wait has a timeout.
    """
    writer_pid = await backend_pid(writer)
    task: asyncio.Task[T] = asyncio.create_task(load())
    try:
        await wait_until_blocked(committed.engine, writer_pid)
        await writer.commit()
    except BaseException as failure:
        try:
            await writer.rollback()
        finally:
            # always settle the task (it can finish once the writer's locks are gone)
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(task, timeout=10)
        if task.done() and not task.cancelled() and task.exception() is not None:
            # the load failed early: its own error explains the failure better than ours
            raise task.exception() from failure  # type: ignore[misc]
        raise
    return await asyncio.wait_for(task, timeout=30)


async def test_snapshot_sees_data_committed_while_waiting(committed: Committed) -> None:
    room = f"room-{uuid4().hex[:6]}"
    actor = await setup_actor(committed, room)

    async with committed.session() as writer, committed.session() as reader:
        await writer.begin()
        row = await lock_player(writer, actor.id)
        row.gold = 50

        async def load() -> ActionSnapshot:
            async with reader.begin():
                # The actor is already in the identity map before the lock, as when a service
                # reads the player first (e.g. rate limit). Keep a strong reference: the identity
                # map is weak, an unreferenced object would be dropped and reloaded fresh anyway.
                cached = await reader.get(Player, actor.id)
                assert cached is not None and cached.gold == 100
                snapshot = await Repository(reader).load_action_snapshot(actor.id)
                assert cached.gold == 50  # the cached object was refreshed under the lock
                return snapshot

        snapshot = await against_writer(committed, writer, load)

    assert snapshot.actor.gold == 50


async def test_group_joined_while_waiting_requests_retry(committed: Committed) -> None:
    room = f"room-{uuid4().hex[:6]}"
    actor = await setup_actor(committed, room)

    async with committed.session() as writer, committed.session() as reader:
        await writer.begin()
        row = await lock_player(writer, actor.id)
        group = Group(leader_id=actor.id)
        writer.add(group)
        await writer.flush()
        row.group_id, row.group_joined_at = group.id, NOW

        async def load() -> object:
            async with reader.begin():
                return await Repository(reader).load_action_snapshot(actor.id)

        with pytest.raises(SnapshotChangedError):
            await against_writer(committed, writer, load)


async def test_actor_moved_while_waiting_requests_retry(committed: Committed) -> None:
    room = f"room-{uuid4().hex[:6]}"
    actor = await setup_actor(committed, room)

    async with committed.session() as writer, committed.session() as reader:
        await writer.begin()
        row = await lock_player(writer, actor.id)
        row.current_room_id = "elsewhere"

        async def load() -> object:
            async with reader.begin():
                return await Repository(reader).load_action_snapshot(actor.id)

        with pytest.raises(SnapshotChangedError):
            await against_writer(committed, writer, load)


async def test_snapshot_locks_players_and_monsters_until_commit(committed: Committed) -> None:
    room = f"room-{uuid4().hex[:6]}"
    actor = await setup_actor(committed, room)
    async with committed.session() as db, db.begin():
        monster = MonsterInstance(monster_id="goblin", room_id=room, hp=12)
        db.add(monster)

    async with committed.session() as holder, holder.begin():
        await Repository(holder).load_action_snapshot(actor.id)
        for table, row_id in (("player", actor.id), ("monster_instance", monster.id)):
            async with committed.session() as other, other.begin():
                await other.execute(text("SET LOCAL lock_timeout = '200ms'"))
                with pytest.raises(DBAPIError, match="lock"):
                    await other.execute(
                        text(f"SELECT 1 FROM {table} WHERE id = :id FOR UPDATE"), {"id": row_id}
                    )


async def test_concurrent_seeds_do_not_duplicate(committed: Committed) -> None:
    content = load_content(DATA_DIR)
    rooms = list(content.rooms)
    committed.rooms.extend(rooms)  # cleanup removes seeded rows of these rooms, even on failure

    async def count_monsters() -> int:
        async with committed.session() as db:
            rows = await db.scalars(
                select(MonsterInstance).where(col(MonsterInstance.room_id).in_(rooms))
            )
            return len(rows.all())

    before = await count_monsters()

    async def run_seed() -> None:
        async with committed.session() as db, db.begin():
            await seed(db, content)
            await asyncio.sleep(0.2)  # keep the transaction open so the seeds overlap

    await asyncio.gather(run_seed(), run_seed())

    wanted = sum(s.count for r in content.rooms.values() for s in r.spawns)
    assert await count_monsters() - before == wanted


async def test_wait_helper_fails_without_waiter(
    committed: Committed,
) -> None:
    """Sanity check of the helper: without a writer nobody waits, the probe times out."""
    with pytest.raises(AssertionError):
        await wait_until_blocked(
            committed.engine, timeout=timedelta(milliseconds=200).total_seconds()
        )


async def test_repository_cannot_outlive_its_transaction(committed: Committed) -> None:
    actor = await setup_actor(committed, f"room-{uuid4().hex[:6]}")
    async with committed.session() as db:
        repo = Repository(db)
        async with db.begin():
            await repo.load_action_snapshot(actor.id)
        async with db.begin():
            # the locks of the first transaction are gone; the old lock set must not authorize
            with pytest.raises(StaleRepositoryError):
                await repo.apply_changes(
                    StateChanges(players=[PlayerUpdate(player_id=actor.id, gold=1)])
                )
            with pytest.raises(StaleRepositoryError):
                await repo.load_action_snapshot(actor.id)


async def test_repository_refuses_locks_inside_a_savepoint(committed: Committed) -> None:
    actor = await setup_actor(committed, f"room-{uuid4().hex[:6]}")
    async with committed.session() as db, db.begin():
        await db.begin_nested()
        with pytest.raises(StaleRepositoryError):
            await Repository(db).load_action_snapshot(actor.id)


async def test_savepoint_after_locking_blocks_apply(committed: Committed) -> None:
    actor = await setup_actor(committed, f"room-{uuid4().hex[:6]}")
    async with committed.session() as db, db.begin():
        repo = Repository(db)
        await repo.load_action_snapshot(actor.id)
        await db.begin_nested()
        with pytest.raises(StaleRepositoryError):
            await repo.apply_changes(
                StateChanges(players=[PlayerUpdate(player_id=actor.id, gold=1)])
            )
