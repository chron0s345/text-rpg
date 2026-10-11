"""World ticker: background task for everything that happens without a player action
(ARCHITECTURE.md, "World-Ticker").

Every ``ticker_interval_seconds`` one tick runs these steps, each in its own short transaction(s):

1. monster respawns
2. item respawns
3. expired floor items
4. idle ambushes (one transaction per candidate)
5. expired group invites and trade requests

Expired disconnect grace periods follow with the session service (login/logout/disconnect).

The decisions ("is due", "is ambushed") are made by the engine; the ticker only locks, applies,
logs, commits and then publishes, exactly like ``ActionService``. A failing step is logged and
does not stop the following ones.

Only one process ticks: the leader holds a session-level PostgreSQL advisory lock on a dedicated
connection (``TickerLeadership``). If that connection breaks, the lock is gone and any process
(this one included) can take over on its next attempt.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, exists, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker
from sqlmodel import col

from app.content.registry import ContentRegistry
from app.db.mappers import floor_item_snapshot, monster_snapshot
from app.db.models import GroupInvite, MonsterInstance, Player, Trade, TradeOffer
from app.db.repository import Repository, SnapshotChangedError, UnknownPlayerError
from app.engine.ambush import resolve_idle_ambush
from app.engine.cleanup import expire_floor_items
from app.engine.events import ActionResult, GameEvent
from app.engine.respawn import respawn_due_items, respawn_monsters
from app.engine.state import ItemSpawnSnapshot
from app.services.events import EventPublisher
from app.services.logbook import write_log
from app.services.transactions import MAX_ATTEMPTS, RetryLimitExceededError, is_retryable

logger = logging.getLogger(__name__)

TICKER_LOCK = "text-rpg:world-ticker"


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class TickReport:
    """What one tick did (for logs and tests)."""

    monsters_respawned: int = 0
    items_respawned: int = 0
    floor_items_expired: int = 0
    ambushes: int = 0
    invites_expired: int = 0
    trade_requests_expired: int = 0
    failed_steps: list[str] = field(default_factory=list)


class TickerLeadership:
    """Session-level ``pg_try_advisory_lock`` held on a dedicated connection.

    The lock belongs to the database session, so the connection must never go back to the pool
    while it holds the lock: ``release`` unlocks explicitly and invalidates the connection if
    that fails.
    """

    def __init__(self, engine: AsyncEngine, key: str = TICKER_LOCK) -> None:
        self.engine = engine
        self.key = key
        self._connection: AsyncConnection | None = None
        self._backend_pid: int | None = None  # the database session that holds the lock

    async def ensure(self) -> bool:
        """Checks that the held lock is still alive, or tries (non-blocking) to take it."""
        if self._connection is not None:
            # Same backend = lock still held. SQLAlchemy silently reconnects an invalidated
            # connection, and a new backend does not hold the lock any more.
            try:
                pid = await self._connection.scalar(select(func.pg_backend_pid()))
                await self._connection.commit()
            except Exception:
                logger.warning("world ticker lost its leader connection", exc_info=True)
                pid = None
            if pid is not None and pid == self._backend_pid:
                return True
            logger.warning("world ticker leadership lost")
            await self._discard()
        connection = await self.engine.connect()
        try:
            acquired = await connection.scalar(
                select(func.pg_try_advisory_lock(func.hashtextextended(self.key, 0)))
            )
            pid = await connection.scalar(select(func.pg_backend_pid()))
            await connection.commit()  # the lock outlives the transaction; do not idle in one
        except BaseException:
            await connection.invalidate()
            await connection.close()
            raise
        if not acquired:
            await connection.close()
            return False
        self._connection, self._backend_pid = connection, pid
        return True

    async def release(self) -> None:
        if self._connection is None:
            return
        connection, self._connection = self._connection, None
        try:
            await connection.scalar(
                select(func.pg_advisory_unlock(func.hashtextextended(self.key, 0)))
            )
            await connection.commit()
        except Exception:
            logger.warning("unlocking the world ticker lock failed", exc_info=True)
            await connection.invalidate()  # closing the database session drops the lock
        finally:
            await connection.close()

    async def _discard(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                await connection.invalidate()
                await connection.close()
            except Exception:
                logger.debug("closing a broken leader connection failed", exc_info=True)


class WorldTicker:
    def __init__(
        self,
        engine: AsyncEngine,
        sessions: async_sessionmaker[AsyncSession],
        content: ContentRegistry,
        publisher: EventPublisher,
        *,
        clock: Callable[[], datetime] = utc_now,
        rng_factory: Callable[[], random.Random] = random.SystemRandom,
        interval_seconds: float | None = None,
    ) -> None:
        self.sessions = sessions
        self.content = content
        self.publisher = publisher
        self.clock = clock
        # SystemRandom by default: ambush rolls must not be predictable from earlier outputs
        self.rng_factory = rng_factory
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else content.rules.timings.ticker_interval_seconds
        )
        self.leadership = TickerLeadership(engine)

    # --- background loop ------------------------------------------------------------------------

    async def run(self) -> None:
        """Ticks forever while this process is the leader; stops on cancellation."""
        try:
            while True:
                try:
                    if await self.leadership.ensure():
                        report = await self.tick()
                        logger.debug("world tick: %s", report)
                except Exception:
                    logger.exception("world tick failed")
                await asyncio.sleep(self.interval_seconds)
        finally:
            await asyncio.shield(self.leadership.release())

    async def tick(self) -> TickReport:
        """One run of all steps at the same ``now``. Callers must hold the leadership (tests call
        it directly)."""
        now = self.clock()
        report = TickReport()
        steps: list[tuple[str, Callable[[datetime, TickReport], Awaitable[None]]]] = [
            ("monster_respawns", self._respawn_monsters),
            ("item_respawns", self._respawn_items),
            ("floor_cleanup", self._expire_floor_items),
            ("idle_ambushes", self._idle_ambushes),
            ("expired_requests", self._expire_requests),
        ]
        for name, step in steps:
            try:
                await step(now, report)
            except Exception:
                logger.exception("world ticker step %s failed", name)
                report.failed_steps.append(name)
        return report

    # --- steps ----------------------------------------------------------------------------------

    async def _respawn_monsters(self, now: datetime, report: TickReport) -> None:
        async def attempt(session: AsyncSession) -> ActionResult:
            repo = Repository(session)
            monsters = [monster_snapshot(m) for m in await repo.lock_due_monsters(now)]
            result = respawn_monsters(monsters, self.content, now)
            await repo.apply_changes(result.changes)
            report.monsters_respawned = len(result.changes.monsters)
            return result

        await self._run(attempt, now)

    async def _respawn_items(self, now: datetime, report: TickReport) -> None:
        async def attempt(session: AsyncSession) -> ActionResult:
            repo = Repository(session)
            spawns = [
                ItemSpawnSnapshot(
                    room_id=s.room_id, item_id=s.item_id, next_spawn_at=s.next_spawn_at
                )
                for s in await repo.lock_due_item_spawns(now)
            ]
            lying = await repo.spawn_items_lying((s.room_id, s.item_id) for s in spawns)
            result = respawn_due_items(spawns, lying, self.content, now, self.rng_factory())
            await repo.apply_changes(result.changes)
            report.items_respawned = len(result.changes.floor_items_create)
            return result

        await self._run(attempt, now)

    async def _expire_floor_items(self, now: datetime, report: TickReport) -> None:
        async def attempt(session: AsyncSession) -> ActionResult:
            repo = Repository(session)
            items = [floor_item_snapshot(f) for f in await repo.lock_expired_floor_items(now)]
            result = expire_floor_items(items, self.content, now)
            await repo.apply_changes(result.changes)
            report.floor_items_expired = len(result.changes.floor_items_delete)
            return result

        await self._run(attempt, now)

    async def _idle_ambushes(self, now: datetime, report: TickReport) -> None:
        for victim_id in await self._ambush_candidates():

            async def attempt(session: AsyncSession, victim_id: UUID = victim_id) -> ActionResult:
                repo = Repository(session)
                try:
                    snapshot = await repo.load_action_snapshot(victim_id)
                except UnknownPlayerError:  # deleted meanwhile
                    return ActionResult()
                victim = await session.get(Player, victim_id)  # refreshed under the lock
                # never against offline characters (ambush.ambush_candidates precondition)
                if victim is None or not victim.online or victim.deleted_at is not None:
                    return ActionResult()
                result = resolve_idle_ambush(
                    snapshot, self.content, self.content.rules, now, self.rng_factory()
                )
                await repo.apply_changes(result.changes)
                return result

            try:
                result = await self._run(attempt, now)
            except Exception:
                # one player's conflict must not cost everybody else their roll
                logger.exception("idle ambush roll for %s failed", victim_id)
                continue
            if result.changes.participations_upsert:
                report.ambushes += 1

    async def _ambush_candidates(self) -> list[UUID]:
        """Unlocked pre-read: online, exploring players in unsafe rooms with a living monster
        that may ambush while players idle. The engine re-checks everything under the locks."""
        ambushers = [
            m.id
            for m in self.content.monsters.values()
            if m.ambush and m.ambush.chance_per_tick > 0
        ]
        if not ambushers:
            return []
        unsafe = [r.id for r in self.content.rooms.values() if not r.safe]
        async with self.sessions() as session:
            rows = await session.scalars(
                select(col(Player.id))
                .where(
                    col(Player.online),
                    col(Player.deleted_at).is_(None),
                    col(Player.state) == "exploring",
                    col(Player.current_room_id).in_(unsafe),
                    exists().where(
                        col(MonsterInstance.room_id) == col(Player.current_room_id),
                        col(MonsterInstance.alive),
                        col(MonsterInstance.monster_id).in_(ambushers),
                    ),
                )
                .order_by(col(Player.id))
            )
            return list(rows.all())

    async def _expire_requests(self, now: datetime, report: TickReport) -> None:
        """Expired group invites and unanswered trade requests are deleted. A request has no
        offers yet; the guard keeps an inconsistent row from failing the whole step."""
        async with self.sessions() as session, session.begin():
            invites = await session.execute(
                delete(GroupInvite).where(col(GroupInvite.expires_at) <= now)
            )
            trades = await session.execute(
                delete(Trade).where(
                    col(Trade.status) == "requested",
                    col(Trade.expires_at) <= now,
                    ~exists().where(col(TradeOffer.trade_id) == col(Trade.id)),
                )
            )
        report.invites_expired = _rowcount(invites)
        report.trade_requests_expired = _rowcount(trades)

    # --- transaction, log, publish --------------------------------------------------------------

    async def _run(
        self, attempt: Callable[[AsyncSession], Awaitable[ActionResult]], now: datetime
    ) -> ActionResult:
        """One transaction per attempt (fresh ``Repository`` inside ``attempt``); log entries in
        the same transaction; events only after the commit. Lock conflicts are retried."""
        for _ in range(MAX_ATTEMPTS):
            try:
                async with self.sessions() as session, session.begin():
                    result = await attempt(session)
                    await write_log(session, result.events, now)
            except SnapshotChangedError:
                continue
            except DBAPIError as exc:
                if is_retryable(exc):
                    continue
                raise
            await self._publish(result.events)
            return result
        raise RetryLimitExceededError("world ticker transaction kept conflicting")

    async def _publish(self, events: Sequence[GameEvent]) -> None:
        if not events:
            return
        try:
            await self.publisher.publish(events)
        except Exception:
            # committed already; recipients catch up through their log entries
            logger.exception("publishing world ticker events failed")


def _rowcount(result: object) -> int:
    count = getattr(result, "rowcount", 0)
    return count if isinstance(count, int) and count > 0 else 0
