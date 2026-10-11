"""SessionService: which character is in the world (ARCHITECTURE.md, "Einloggen, Ausloggen &
Verbindungsabbruch").

- ``play``: choose a character. Another online character of the same account is logged out first
  (in combat only with ``confirm_death``, otherwise the caller gets a confirmation request).
- ``logout``: by the player (route for ``LogoutAction``).
- ``connect`` / ``disconnect``: the SSE stream of the character opened / closed. Closing starts
  the grace period; the world ticker calls ``expire_disconnect`` once it is over.
- ``mark_all_disconnected``: on server start, every online character without a live connection
  gets the same grace period.

Every change runs like an action: lock, engine, write, log, commit, then publish.
Logins of one account are serialized by a transaction-scoped advisory lock; the partial unique
index (one online character per account) is the database backstop.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.content.registry import ContentRegistry
from app.db.models import Player
from app.db.repository import Repository, UnknownPlayerError
from app.engine.events import ActionResult, ActorAudience, GameEvent
from app.engine.presence import (
    CONFIRM_DEATH_KIND,
    disconnect_expired,
    resolve_disconnect,
    resolve_login,
    resolve_logout,
)
from app.engine.state import ActionSnapshot
from app.services.events import EventPublisher, Relocation
from app.services.logbook import write_log
from app.services.transactions import MAX_ATTEMPTS, RetryLimitExceededError, in_transaction

logger = logging.getLogger(__name__)


class UnknownCharacterError(Exception):
    """No such (non-deleted) character on this account -> HTTP 404."""


@dataclass(frozen=True)
class SessionOutcome:
    """``confirm_death``: nothing happened; the character is in combat and logging out (or
    switching characters) would kill it. Ask the player and call again with confirmation."""

    status: Literal["ok", "confirm_death"]
    events: list[GameEvent] = field(default_factory=list)  # for the acting player (HTTP response)


@dataclass(frozen=True)
class _Committed:
    result: ActionResult
    relocations: list[Relocation]
    other_online: UUID | None = None  # play(): another character of the account is online


def utc_now() -> datetime:
    return datetime.now(UTC)


class SessionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        content: ContentRegistry,
        publisher: EventPublisher,
        *,
        clock: Callable[[], datetime] = utc_now,
        rng_factory: Callable[[], random.Random] = random.SystemRandom,
    ) -> None:
        self.sessions = sessions
        self.content = content
        self.publisher = publisher
        self.clock = clock
        # SystemRandom by default: death loss rolls must not be predictable
        self.rng_factory = rng_factory

    # --- login / logout -------------------------------------------------------------------------

    async def play(
        self, account_id: UUID, player_id: UUID, *, confirm_death: bool = False
    ) -> SessionOutcome:
        """Logs ``player_id`` in; logs out the account's other online character first."""
        for _ in range(MAX_ATTEMPTS):
            now = self.clock()

            async def attempt(session: AsyncSession) -> _Committed:
                await self._lock_account(session, account_id)
                other = await session.scalar(
                    select(col(Player.id)).where(
                        col(Player.account_id) == account_id,
                        col(Player.online),
                        col(Player.id) != player_id,
                    )
                )
                if other is not None:
                    return _Committed(ActionResult(), [], other_online=other)
                repo = Repository(session)
                snapshot = await self._load(repo, player_id)
                row = await self._row(session, player_id)
                if row.account_id != account_id:
                    raise UnknownCharacterError(player_id)
                result = resolve_login(
                    snapshot,
                    self.content,
                    self.content.rules,
                    now,
                    self.rng_factory(),
                    already_online=row.online,
                )
                return await self._apply(session, repo, snapshot, result, now, actor=player_id)

            committed = await in_transaction(self.sessions, attempt)
            if committed.other_online is None:
                return await self._finish(committed)
            switched = await self.logout(committed.other_online, confirm_death=confirm_death)
            if switched.status == "confirm_death":
                return switched
        raise RetryLimitExceededError(f"login of {player_id} kept conflicting")

    async def logout(self, player_id: UUID, *, confirm_death: bool = False) -> SessionOutcome:
        """Logout by the player. Logging out an offline character changes nothing."""
        now = self.clock()

        async def attempt(session: AsyncSession) -> _Committed:
            repo = Repository(session)
            snapshot = await self._load(repo, player_id, presence=True)
            if not (await self._row(session, player_id)).online:
                return _Committed(ActionResult(), [])
            result = resolve_logout(
                snapshot,
                self.content,
                self.content.rules,
                now,
                self.rng_factory(),
                confirm_death=confirm_death,
            )
            return await self._apply(session, repo, snapshot, result, now, actor=player_id)

        committed = await in_transaction(self.sessions, attempt)
        return await self._finish(committed)

    # --- live connection ------------------------------------------------------------------------

    async def connect(self, player_id: UUID) -> bool:
        """The character's SSE stream opened: the grace period (if any) ends. ``False`` if the
        character is not online any more (the stream must close)."""
        async with self.sessions() as session, session.begin():
            updated = await session.scalar(
                update(Player)
                .where(
                    col(Player.id) == player_id,
                    col(Player.online),
                    col(Player.deleted_at).is_(None),
                )
                .values(disconnected_at=None)
                .returning(col(Player.id))
            )
        return updated is not None

    async def disconnect(self, player_id: UUID) -> None:
        """The character's last SSE stream closed: the grace period starts, an active trade is
        cancelled. An already running grace period is not extended."""
        now = self.clock()

        async def attempt(session: AsyncSession) -> _Committed:
            repo = Repository(session)
            snapshot = await self._load(repo, player_id, presence=True)
            row = await self._row(session, player_id)
            if not row.online or row.disconnected_at is not None:
                return _Committed(ActionResult(), [])
            result = resolve_disconnect(
                snapshot, self.content, self.content.rules, now, self.rng_factory()
            )
            return await self._apply(session, repo, snapshot, result, now, actor=None)

        try:
            committed = await in_transaction(self.sessions, attempt)
        except UnknownCharacterError:
            return  # deleted meanwhile: nothing to do
        await self._finish(committed)

    async def expired_disconnects(self, now: datetime) -> list[UUID]:
        """Unlocked pre-read for the world ticker; ``expire_disconnect`` re-checks under lock."""
        deadline = now - timedelta(seconds=self.content.rules.timings.disconnect_grace_seconds)
        async with self.sessions() as session:
            rows = await session.scalars(
                select(col(Player.id))
                .where(
                    col(Player.online),
                    col(Player.deleted_at).is_(None),
                    col(Player.disconnected_at) <= deadline,
                )
                .order_by(col(Player.id))
            )
            return list(rows.all())

    async def expire_disconnect(self, player_id: UUID, now: datetime) -> bool:
        """Grace period over: logout, in combat with death (world ticker). ``True`` if done."""

        async def attempt(session: AsyncSession) -> _Committed:
            repo = Repository(session)
            snapshot = await self._load(repo, player_id, presence=True)
            row = await self._row(session, player_id)
            if not row.online or not disconnect_expired(
                row.disconnected_at, now, self.content.rules
            ):
                return _Committed(ActionResult(), [])  # reconnected meanwhile
            result = resolve_logout(
                snapshot,
                self.content,
                self.content.rules,
                now,
                self.rng_factory(),
                reason="disconnect",
            )
            return await self._apply(session, repo, snapshot, result, now, actor=None)

        try:
            committed = await in_transaction(self.sessions, attempt)
        except UnknownCharacterError:
            return False
        await self._finish(committed)
        return bool(committed.result.changes.players)

    async def mark_all_disconnected(self) -> int:
        """Server start: no process has live connections yet, so every online character gets
        the disconnect grace period. Run once per deployment start, before serving streams:
        it would also mark characters whose stream is held by an already running process."""
        now = self.clock()
        async with self.sessions() as session, session.begin():
            marked = await session.scalars(
                update(Player)
                .where(col(Player.online), col(Player.disconnected_at).is_(None))
                .values(disconnected_at=now)
                .returning(col(Player.id))
            )
            return len(marked.all())

    # --- helpers --------------------------------------------------------------------------------

    @staticmethod
    async def _lock_account(session: AsyncSession, account_id: UUID) -> None:
        """Serializes logins of one account (blocking, until the transaction ends)."""
        await session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(f"account:{account_id}", 0)))
        )

    @staticmethod
    async def _load(repo: Repository, player_id: UUID, *, presence: bool = False) -> ActionSnapshot:
        try:
            return await repo.load_action_snapshot(
                player_id, lock_trades=presence, lock_group_members=presence
            )
        except UnknownPlayerError as exc:
            raise UnknownCharacterError(player_id) from exc

    @staticmethod
    async def _row(session: AsyncSession, player_id: UUID) -> Player:
        """The actor row, already locked and refreshed by the snapshot load."""
        row = await session.get(Player, player_id)
        if row is None or row.deleted_at is not None:
            raise UnknownCharacterError(player_id)
        return row

    async def _apply(
        self,
        session: AsyncSession,
        repo: Repository,
        snapshot: ActionSnapshot,
        result: ActionResult,
        now: datetime,
        *,
        actor: UUID | None,
    ) -> _Committed:
        await repo.apply_changes(result.changes)
        await write_log(session, result.events, now, actor_id=actor)
        return _Committed(result, await self._relocations(session, result))

    @staticmethod
    async def _relocations(session: AsyncSession, result: ActionResult) -> list[Relocation]:
        """Players whose room or group changed; read from the written (locked) rows, because
        group members elsewhere are not in the snapshot."""
        relocations = []
        for player_update in result.changes.players:
            if not {"current_room_id", "group_id"} & player_update.model_fields_set:
                continue
            row = await session.get(Player, player_update.player_id)
            if row is not None:
                relocations.append(Relocation(row.id, row.current_room_id, row.group_id))
        return relocations

    async def _finish(self, committed: _Committed) -> SessionOutcome:
        """After the commit: publish to everybody else, answer the actor."""
        events = committed.result.events
        others = [e for e in events if not isinstance(e.audience, ActorAudience)]
        await self._publish(others, committed.relocations)
        mine = [e for e in events if isinstance(e.audience, ActorAudience)]
        if any(e.kind == CONFIRM_DEATH_KIND for e in mine):
            return SessionOutcome("confirm_death", mine)
        return SessionOutcome("ok", mine)

    async def _publish(self, events: Sequence[GameEvent], relocations: list[Relocation]) -> None:
        if not events and not relocations:
            return
        try:
            await self.publisher.publish(events, relocations)
        except Exception:
            # committed already; recipients catch up through their log entries
            logger.exception("publishing session events failed")
