"""World ticker (ARCHITECTURE.md, "World-Ticker"): background upkeep every
``ticker_interval_seconds``, each step in its own short transactions:

1. monster respawns
2. item respawns
3. expired floor items
4. expired disconnect grace periods (logout; death in combat)
5. ambush rolls for players idling in a room
6. expired group invites and trade requests
7. offline characters still in a group leave it (normally done right after the logout)
8. active trades whose traders are no longer both here and free end (normally done right after
   the action that broke them)
9. log entries older than the retention (at most hourly; ``LOG_RETENTION_DAYS``)

The decisions ("is due", "gets ambushed") are made by the engine; the ticker only finds
candidates (unlocked reads), locks, applies and publishes after the commit. Steps 4 and 5 go
through ``ActionService`` like player actions (same locks, log entries, retries).

With several server processes only one ticks: it holds a session-level PostgreSQL advisory lock
(``pg_try_advisory_lock``) on a dedicated connection. If that connection drops, another process
takes over at its next tick.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker
from sqlmodel import col

from app.content.registry import ContentRegistry
from app.db.mappers import floor_item_snapshot, monster_snapshot
from app.db.models import (
    FloorItem,
    GroupInvite,
    ItemSpawnState,
    MonsterInstance,
    Player,
    Trade,
)
from app.db.repository import Repository
from app.engine.cleanup import ItemSpawnSnapshot, expired_floor_items, respawn_items
from app.engine.events import ActionResult, GameEvent
from app.engine.respawn import respawn_monsters
from app.services.action_service import (
    ActionOutcome,
    ActionService,
    NotPlayingError,
    RetryLimitExceededError,
    utc_now,
)
from app.services.events import EventPublisher
from app.services.log import write_log
from app.services.log_retention import purge_log

logger = logging.getLogger(__name__)

TICKER_LOCK: Final = "text-rpg:world-ticker"
LOG_PURGE_INTERVAL: Final = timedelta(hours=1)


@dataclass
class TickReport:
    """What one tick did (numbers of rows/characters), for logs and tests."""

    monsters_respawned: int = 0
    items_respawned: int = 0
    floor_items_removed: int = 0
    connections_expired: int = 0
    ambushes: int = 0
    invites_removed: int = 0
    trade_requests_removed: int = 0
    offline_members_removed: int = 0
    trades_ended: int = 0
    log_entries_removed: int = 0


class WorldTicker:
    def __init__(
        self,
        engine: AsyncEngine,
        sessions: async_sessionmaker[AsyncSession],
        content: ContentRegistry,
        actions: ActionService,
        publisher: EventPublisher,
        *,
        clock: Callable[[], datetime] = utc_now,
        rng_factory: Callable[[], random.Random] = random.SystemRandom,
        log_retention: timedelta | None = None,
    ) -> None:
        self.engine = engine
        self.sessions = sessions
        self.content = content
        self.actions = actions
        self.publisher = publisher
        self.clock = clock
        self.rng_factory = rng_factory
        # None: keep the log forever; otherwise purged at most once per LOG_PURGE_INTERVAL
        self.log_retention = log_retention
        self._log_purged_at: datetime | None = None
        self._lock_connection: AsyncConnection | None = None

    # --- loop & leadership ------------------------------------------------------------------

    async def run(self, stop: asyncio.Event) -> None:
        """Ticks until ``stop`` is set. Never dies on errors (they are logged)."""
        interval = self.content.rules.timings.ticker_interval_seconds
        try:
            while not stop.is_set():
                try:
                    if await self.hold_lock():
                        await self.tick()
                except Exception:
                    logger.exception("world tick failed")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                except TimeoutError:
                    pass
        finally:
            await self.release_lock()

    async def hold_lock(self) -> bool:
        """True while this process is the one that ticks."""
        if self._lock_connection is not None:
            try:  # still alive? (the lock dies with the connection)
                await self._lock_connection.execute(select(1))
                await self._lock_connection.commit()
                return True
            except Exception:
                logger.warning("world ticker lost its lock connection")
                connection, self._lock_connection = self._lock_connection, None
                await connection.invalidate()
                await connection.close()
                return False
        connection = await self.engine.connect()
        try:
            acquired = await connection.scalar(
                select(func.pg_try_advisory_lock(func.hashtext(TICKER_LOCK)))
            )
            await connection.commit()  # the session-level lock outlives the transaction
        except Exception:
            await connection.close()
            raise
        if not acquired:
            await connection.close()
            return False
        self._lock_connection = connection
        return True

    async def release_lock(self) -> None:
        """Gives up leadership. ``close()`` alone would only return the connection to the pool,
        where its session (and the advisory lock) would live on."""
        connection, self._lock_connection = self._lock_connection, None
        if connection is None:
            return
        try:
            await connection.execute(select(func.pg_advisory_unlock(func.hashtext(TICKER_LOCK))))
            await connection.commit()
        except Exception:
            # broken connection: discard it, the server ends the session and with it the lock
            await connection.invalidate()
        finally:
            await connection.close()

    # --- one tick ---------------------------------------------------------------------------

    async def tick(self) -> TickReport:
        """All steps once. A failing step is logged and does not stop the others."""
        now = self.clock()
        report = TickReport()
        steps = (
            self._respawn_monsters,
            self._respawn_items,
            self._remove_expired_floor_items,
            self._expire_connections,
            self._roll_idle_ambushes,
            self._remove_expired_requests,
            self._remove_offline_members,
            self._end_broken_trades,
            self._purge_log,
        )
        for step in steps:
            try:
                await step(now, report)
            except Exception:
                logger.exception("world ticker step %s failed", step.__name__)
        return report

    # 9. old log entries

    async def _purge_log(self, now: datetime, report: TickReport) -> None:
        if self.log_retention is None:
            return
        if self._log_purged_at is not None and now - self._log_purged_at < LOG_PURGE_INTERVAL:
            return
        self._log_purged_at = now
        report.log_entries_removed = await purge_log(self.sessions, now, self.log_retention)

    async def _publish(self, events: Sequence[GameEvent]) -> None:
        if not events:
            return
        try:
            await self.publisher.publish(events)
        except Exception:  # committed anyway; clients catch up via their log
            logger.exception("publishing world ticker events failed")

    async def _rooms(self, column: Any, *conditions: Any) -> list[str]:
        """Rooms with something due (unlocked read; the locked re-check decides)."""
        async with self.sessions() as session:
            rows = await session.scalars(select(column).where(*conditions).distinct())
            return list(rows.all())

    # 1. monsters

    async def _respawn_monsters(self, now: datetime, report: TickReport) -> None:
        rooms = await self._rooms(
            col(MonsterInstance.room_id),
            col(MonsterInstance.alive).is_(False),
            col(MonsterInstance.respawn_at) <= now,
        )
        for room_id in rooms:
            async with self.sessions() as session, session.begin():
                repo = Repository(session)
                monsters = [monster_snapshot(m) for m in await repo.lock_monsters_in_room(room_id)]
                result = respawn_monsters(monsters, self.content, now)
                await self._apply(session, repo, result, now)
            report.monsters_respawned += len(result.changes.monsters)
            await self._publish(result.events)

    # 2. spawn items

    async def _respawn_items(self, now: datetime, report: TickReport) -> None:
        rooms = await self._rooms(
            col(ItemSpawnState.room_id), col(ItemSpawnState.next_spawn_at) <= now
        )
        for room_id in rooms:
            if room_id not in self.content.rooms:
                continue  # room removed from the content: nothing to spawn
            async with self.sessions() as session, session.begin():
                repo = Repository(session)
                floor_rows, spawn_rows = await repo.lock_room_items(room_id)
                result = respawn_items(
                    room_id,
                    [ItemSpawnSnapshot(s.room_id, s.item_id, s.next_spawn_at) for s in spawn_rows],
                    [floor_item_snapshot(f) for f in floor_rows],
                    self.content,
                    now,
                    self.rng_factory(),
                )
                await self._apply(session, repo, result, now)
            report.items_respawned += len(result.changes.floor_items_create)
            await self._publish(result.events)

    # 3. floor items

    async def _remove_expired_floor_items(self, now: datetime, report: TickReport) -> None:
        rooms = await self._rooms(col(FloorItem.room_id), col(FloorItem.expires_at) <= now)
        for room_id in rooms:
            async with self.sessions() as session, session.begin():
                repo = Repository(session)
                floor = [
                    floor_item_snapshot(f) for f in await repo.lock_floor_items_in_room(room_id)
                ]
                result = ActionResult()
                result.changes.floor_items_delete = expired_floor_items(floor, now)
                await repo.apply_changes(result.changes)
            report.floor_items_removed += len(result.changes.floor_items_delete)

    # 4. disconnect grace periods

    async def _expire_connections(self, now: datetime, report: TickReport) -> None:
        grace = timedelta(seconds=self.content.rules.timings.disconnect_grace_seconds)
        async with self.sessions() as session:
            player_ids = (
                await session.scalars(
                    select(col(Player.id)).where(
                        col(Player.online),
                        col(Player.deleted_at).is_(None),
                        col(Player.disconnected_at) <= now - grace,
                    )
                )
            ).all()
        for player_id in player_ids:
            if await self._for_player(self.actions.expire_connection, player_id):
                report.connections_expired += 1

    # 5. ambushes of idling players

    async def _roll_idle_ambushes(self, now: datetime, report: TickReport) -> None:
        """Candidates: exploring, online characters in unsafe rooms with a living monster that
        can ambush. Whether it happens (and which monster) is rolled by the engine."""
        ambushers = [m.id for m in self.content.monsters.values() if m.ambush is not None]
        if not ambushers:
            return
        async with self.sessions() as session:
            rooms = (
                await session.scalars(
                    select(col(MonsterInstance.room_id))
                    .where(
                        col(MonsterInstance.alive),
                        col(MonsterInstance.monster_id).in_(ambushers),
                    )
                    .distinct()
                )
            ).all()
            rooms = [r for r in rooms if r in self.content.rooms and not self.content.rooms[r].safe]
            if not rooms:
                return
            player_ids = (
                await session.scalars(
                    select(col(Player.id))
                    .where(
                        col(Player.online),
                        col(Player.deleted_at).is_(None),
                        col(Player.state) == "exploring",
                        col(Player.current_room_id).in_(rooms),
                    )
                    .order_by(col(Player.id))
                )
            ).all()
        for player_id in player_ids:
            if await self._for_player(self.actions.idle_ambush, player_id):
                report.ambushes += 1

    async def _for_player(
        self, operation: Callable[[UUID], Awaitable[ActionOutcome]], player_id: UUID
    ) -> bool:
        """Runs a per-player step; True if something happened."""
        try:
            outcome = await operation(player_id)
        except (NotPlayingError, RetryLimitExceededError):
            return False  # logged out meanwhile / busy: next tick
        except Exception:
            logger.exception("world ticker step for %s failed", player_id)
            return False
        return outcome.changed

    # 6. invites & trade requests

    async def _remove_expired_requests(self, now: datetime, report: TickReport) -> None:
        async with self.sessions() as session, session.begin():
            invites = await session.execute(
                delete(GroupInvite)
                .where(col(GroupInvite.expires_at) <= now)
                .returning(col(GroupInvite.player_id))
            )
            report.invites_removed = len(invites.all())
            trades = await session.execute(
                delete(Trade)
                .where(col(Trade.status) == "requested", col(Trade.expires_at) <= now)
                .returning(col(Trade.id))
            )
            report.trade_requests_removed = len(trades.all())

    # 7. offline group members

    async def _remove_offline_members(self, now: datetime, report: TickReport) -> None:
        async with self.sessions() as session:
            player_ids = (
                await session.scalars(
                    select(col(Player.id)).where(
                        col(Player.online).is_(False), col(Player.group_id).is_not(None)
                    )
                )
            ).all()
        for player_id in player_ids:
            if await self._for_player(self.actions.leave_group, player_id):
                report.offline_members_removed += 1

    # 8. trades

    async def _end_broken_trades(self, now: datetime, report: TickReport) -> None:
        async with self.sessions() as session:
            initiators = (
                await session.scalars(
                    select(col(Trade.initiator_id)).where(
                        col(Trade.status).in_(("requested", "open"))
                    )
                )
            ).all()
        if initiators:
            report.trades_ended += await self.actions.check_trades(initiators)

    # --- helpers ----------------------------------------------------------------------------

    async def _apply(
        self, session: AsyncSession, repo: Repository, result: ActionResult, now: datetime
    ) -> None:
        await repo.apply_changes(result.changes)
        await write_log(session, None, result.events, now)
