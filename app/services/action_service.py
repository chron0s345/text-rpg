"""ActionService: runs one player action end to end (ARCHITECTURE.md, "Ablauf einer Aktion").

Login (``login``) and the world ticker's per-player steps (``expire_connection``,
``idle_ambush``) use the same path; login is the only operation for an offline character.

Per attempt, in one transaction:
1. decide which room the action ends in (unlocked pre-read; verified after locking),
2. lock & load the snapshot with a fresh ``Repository``,
3. check on the locked row that the character is online and not deleted,
4. rate limit (``min_action_interval_ms``) on the locked row,
5. lazy monster respawns in that room, then the engine action,
6. write state changes and log entries, commit.
Only after the commit are events published; a rollback never produces events.
Lock conflicts (``SnapshotChangedError``, deadlock, serialization failure) are retried.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.api.schemas import (
    AttackAction,
    DropAction,
    EquipAction,
    FleeAction,
    GameAction,
    InventoryAction,
    LogoutAction,
    LookAction,
    MoveAction,
    PickUpAction,
    RespawnAction,
    SayAction,
    UnequipAction,
    UseItemAction,
)
from app.content.registry import ContentRegistry
from app.db.models import Player
from app.db.repository import Repository, SnapshotChangedError, UnknownPlayerError
from app.engine.actions import resolve_look, resolve_respawn, resolve_say
from app.engine.ambush import resolve_idle_ambush
from app.engine.combat import resolve_attack, resolve_flee
from app.engine.events import ActionResult, ActorAudience, GameEvent, rejected
from app.engine.inventory_actions import (
    resolve_drop,
    resolve_equip,
    resolve_inventory,
    resolve_pick_up,
    resolve_unequip,
    resolve_use_item,
)
from app.engine.movement import resolve_move
from app.engine.presence import (
    connection_expired,
    resolve_connection_lost,
    resolve_login,
    resolve_logout,
)
from app.engine.respawn import respawn_due_monsters
from app.engine.state import ActionSnapshot
from app.services.events import EventPublisher, Relocation
from app.services.log import write_log

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRYABLE_SQLSTATES = {"40001", "40P01"}  # serialization failure, deadlock detected
TOO_FAST = "Nicht so schnell."
NOT_AVAILABLE = "Diese Aktion ist noch nicht verfügbar."


class NotPlayingError(Exception):
    """The character is not logged in (offline, deleted or unknown) -> HTTP 401."""


class RetryLimitExceededError(Exception):
    """Lock conflicts persisted over all attempts."""


@dataclass(frozen=True)
class PreRead:
    room_id: str
    checkpoint_room_id: str
    online: bool
    last_action_at: datetime | None


# engine call for one attempt: (locked snapshot, locked actor row, now, rng) -> result
Resolver = Callable[[ActionSnapshot, Player, datetime, random.Random], ActionResult]


@dataclass(frozen=True)
class Mode:
    """How an operation goes through the common path."""

    require_online: bool  # offline characters -> NotPlayingError
    rate_limited: bool  # min_action_interval_ms against last_action_at
    player_action: bool  # sets last_action_at
    lazy_respawn: bool  # due monsters in the room come back first (actor is told)


PLAYER_ACTION = Mode(require_online=True, rate_limited=True, player_action=True, lazy_respawn=True)
# logout always goes through: a player must never be stuck in the world
LOGOUT = Mode(require_online=True, rate_limited=False, player_action=True, lazy_respawn=True)
LOGIN = Mode(require_online=False, rate_limited=False, player_action=False, lazy_respawn=True)
# world ticker; its respawn step brings monsters back, and nobody is "acting" to be told
SYSTEM = Mode(require_online=True, rate_limited=False, player_action=False, lazy_respawn=False)


@dataclass(frozen=True)
class ActionOutcome:
    """Events for the acting player, delivered with the HTTP response (not via SSE)."""

    events: list[GameEvent]
    changed: bool = False  # anything happened at all (incl. events for others)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _retryable(exc: DBAPIError) -> bool:
    code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    return code in RETRYABLE_SQLSTATES


class ActionService:
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
        # SystemRandom by default: loot and combat rolls must not be predictable from outputs
        self.rng_factory = rng_factory

    async def execute(self, player_id: UUID, action: GameAction) -> ActionOutcome:
        def resolve(
            snapshot: ActionSnapshot, _actor: Player, now: datetime, rng: random.Random
        ) -> ActionResult:
            return self._dispatch(action, snapshot, now, rng)

        return await self._run(
            player_id,
            resolve,
            lambda pre: self._target_room(action, pre.room_id, pre.checkpoint_room_id),
            LOGOUT if isinstance(action, LogoutAction) else PLAYER_ACTION,
        )

    async def login(self, player_id: UUID) -> ActionOutcome:
        """Puts a character online (ownership is checked by the caller). Raises
        ``NotPlayingError`` if the character is deleted or unknown."""

        def resolve(
            snapshot: ActionSnapshot, actor: Player, now: datetime, rng: random.Random
        ) -> ActionResult:
            return resolve_login(
                snapshot,
                self.content,
                self.content.rules,
                now,
                rng,
                already_online=actor.online,
            )

        return await self._run(player_id, resolve, lambda pre: pre.room_id, LOGIN)

    async def expire_connection(self, player_id: UUID) -> ActionOutcome:
        """World ticker: logs the character out if its disconnect grace period is over (checked
        on the locked row: a reconnect in the meantime wins). In combat it dies."""
        rules = self.content.rules

        def resolve(
            snapshot: ActionSnapshot, actor: Player, now: datetime, rng: random.Random
        ) -> ActionResult:
            if not connection_expired(actor.disconnected_at, now, rules):
                return ActionResult()
            return resolve_connection_lost(snapshot, self.content, rules, now, rng)

        return await self._run(player_id, resolve, lambda pre: pre.room_id, SYSTEM)

    async def idle_ambush(self, player_id: UUID) -> ActionOutcome:
        """World ticker: ``chance_per_tick`` ambush roll for a character idling in its room."""

        def resolve(
            snapshot: ActionSnapshot, _actor: Player, now: datetime, rng: random.Random
        ) -> ActionResult:
            return resolve_idle_ambush(snapshot, self.content, self.content.rules, now, rng)

        return await self._run(player_id, resolve, lambda pre: pre.room_id, SYSTEM)

    async def _run(
        self,
        player_id: UUID,
        resolve: Resolver,
        target_room: Callable[[PreRead], str],
        mode: Mode,
    ) -> ActionOutcome:
        for _ in range(MAX_ATTEMPTS):
            try:
                outcome, to_publish, relocations = await self._attempt(
                    player_id, resolve, target_room, mode
                )
            except SnapshotChangedError:
                continue
            except DBAPIError as exc:
                if _retryable(exc):
                    continue
                raise
            # committed: now (and only now) other players learn about it
            try:
                await self.publisher.publish(to_publish, relocations)
            except Exception:
                # The action is saved; failing the request would make the client resend it.
                # Recipients catch up through their log entries (GET /log?after=...).
                logger.exception("publishing events of %s failed", player_id)
            return outcome
        raise RetryLimitExceededError(f"action of {player_id} kept conflicting")

    # --- one attempt = one transaction ----------------------------------------------------------

    async def _attempt(
        self,
        player_id: UUID,
        resolve: Resolver,
        target_room: Callable[[PreRead], str],
        mode: Mode,
    ) -> tuple[ActionOutcome, list[GameEvent], list[Relocation]]:
        now = self.clock()
        rng = self.rng_factory()
        async with self.sessions() as session, session.begin():
            pre = await self._pre_read(session, player_id)
            # Cheap rejections before any row lock, so spamming requests cannot tie up
            # connections or block other players in the room. The checks below on the
            # locked row stay authoritative.
            if not pre.online and mode.require_online:
                raise NotPlayingError(player_id)
            too_fast = mode.rate_limited and self._too_fast(pre.last_action_at, now)
            if too_fast or not await self._claim(session, player_id):
                return ActionOutcome(events=rejected(TOO_FAST).events), [], []

            room = target_room(pre)
            repo = Repository(session)
            try:
                snapshot = await repo.load_action_snapshot(player_id, room)
            except UnknownPlayerError as exc:  # deleted in the meantime
                raise NotPlayingError(player_id) from exc
            actor = await session.get(Player, player_id)  # refreshed under the lock
            if actor is None or actor.deleted_at is not None:
                raise NotPlayingError(player_id)
            if mode.require_online and not actor.online:
                raise NotPlayingError(player_id)
            if (actor.current_room_id, actor.checkpoint_room_id) != (
                pre.room_id,
                pre.checkpoint_room_id,
            ):
                raise SnapshotChangedError(f"{player_id} moved during the snapshot load")
            if mode.rate_limited and self._too_fast(actor.last_action_at, now):
                return ActionOutcome(events=rejected(TOO_FAST).events), [], []

            if mode.player_action:
                actor.last_action_at = now
            respawns = (
                respawn_due_monsters(snapshot, self.content, now)
                if mode.lazy_respawn
                else ActionResult()
            )
            await repo.apply_changes(respawns.changes)
            result = resolve(snapshot, actor, now, rng)
            await repo.apply_changes(result.changes)
            events = [*respawns.events, *result.events]
            relocations = self._relocations(snapshot, result)
            await write_log(session, player_id, events, now)

        actor_events = [e for e in events if isinstance(e.audience, ActorAudience)]
        others = [e for e in events if not isinstance(e.audience, ActorAudience)]
        return ActionOutcome(events=actor_events, changed=bool(events)), others, relocations

    @staticmethod
    def _relocations(snapshot: ActionSnapshot, result: ActionResult) -> list[Relocation]:
        """Players whose room changed (move, respawn): live connections must follow them."""
        relocations = []
        for update in result.changes.players:
            if "current_room_id" not in update.model_fields_set:
                continue
            player = snapshot.player(update.player_id)
            if player is not None:
                relocations.append(Relocation(player.id, player.current_room_id, player.group_id))
        return relocations

    def _too_fast(self, last_action_at: datetime | None, now: datetime) -> bool:
        if last_action_at is None:
            return False
        interval = timedelta(milliseconds=self.content.rules.timings.min_action_interval_ms)
        # a clock that went backwards (other worker, NTP step) never locks a player out
        return timedelta(0) <= now - last_action_at < interval

    async def _claim(self, session: AsyncSession, player_id: UUID) -> bool:
        """At most one action per player in flight across all processes. Non-blocking (try),
        transaction-scoped, taken before any row lock: cannot deadlock with the lock order."""
        claimed = await session.scalar(
            select(func.pg_try_advisory_xact_lock(func.hashtextextended(f"action:{player_id}", 0)))
        )
        return bool(claimed)

    async def _pre_read(self, session: AsyncSession, player_id: UUID) -> PreRead:
        """Unlocked read of the actor; only used to choose rows to lock and for cheap
        rejections. Everything that matters is re-checked on the locked row."""
        row = (
            await session.execute(
                select(
                    col(Player.current_room_id),
                    col(Player.checkpoint_room_id),
                    col(Player.online),
                    col(Player.last_action_at),
                ).where(col(Player.id) == player_id, col(Player.deleted_at).is_(None))
            )
        ).first()
        if row is None:
            raise NotPlayingError(player_id)
        return PreRead(*row)

    def _target_room(self, action: GameAction, origin: str, checkpoint: str) -> str:
        """The room the action ends in, i.e. the room the snapshot must describe."""
        if isinstance(action, MoveAction):
            return self.content.get_room(origin).exits.get(action.direction, origin)
        if isinstance(action, RespawnAction):
            return checkpoint
        return origin

    def _dispatch(
        self, action: GameAction, snapshot: ActionSnapshot, now: datetime, rng: random.Random
    ) -> ActionResult:
        args = (snapshot, self.content, self.content.rules, now, rng)
        match action:
            case MoveAction():
                return resolve_move(*args, action.direction)
            case AttackAction():
                return resolve_attack(*args, action.target_id)
            case FleeAction():
                return resolve_flee(*args)
            case SayAction():
                return resolve_say(*args, action.text)
            case LookAction():
                return resolve_look(*args)
            case RespawnAction():
                return resolve_respawn(*args)
            case InventoryAction():
                return resolve_inventory(*args)
            case UseItemAction():
                return resolve_use_item(*args, action.inventory_item_id)
            case EquipAction():
                return resolve_equip(*args, action.inventory_item_id)
            case UnequipAction():
                return resolve_unequip(*args, action.inventory_item_id)
            case PickUpAction():
                return resolve_pick_up(*args, action.floor_item_id, action.quantity)
            case DropAction():
                return resolve_drop(*args, action.inventory_item_id, action.quantity)
            case LogoutAction():
                return resolve_logout(*args, action.confirm_death)
            case _:
                # groups, trade, bank, merchants: later steps
                return rejected(NOT_AVAILABLE)
