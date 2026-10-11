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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.api.schemas import (
    AttackAction,
    BankDepositAction,
    BankWithdrawAction,
    BuyAction,
    DropAction,
    EquipAction,
    FleeAction,
    GameAction,
    GroupAcceptAction,
    GroupDeclineAction,
    GroupInviteAction,
    GroupKickAction,
    GroupLeaveAction,
    InventoryAction,
    LogoutAction,
    LookAction,
    MoveAction,
    PickUpAction,
    RespawnAction,
    SayAction,
    SellAction,
    UnequipAction,
    UseItemAction,
)
from app.content.registry import ContentRegistry
from app.db.models import Player
from app.db.repository import Repository, SnapshotChangedError, UnknownPlayerError
from app.engine.actions import resolve_look, resolve_respawn, resolve_say
from app.engine.ambush import resolve_idle_ambush
from app.engine.bank import resolve_bank_deposit, resolve_bank_withdraw
from app.engine.combat import resolve_attack, resolve_flee
from app.engine.events import ActionResult, ActorAudience, GameEvent, rejected
from app.engine.groups import (
    resolve_group_accept,
    resolve_group_decline,
    resolve_group_invite,
    resolve_group_kick,
    resolve_group_leave,
)
from app.engine.inventory_actions import (
    resolve_drop,
    resolve_equip,
    resolve_inventory,
    resolve_pick_up,
    resolve_unequip,
    resolve_use_item,
)
from app.engine.merchant import resolve_buy, resolve_sell
from app.engine.movement import resolve_move
from app.engine.presence import (
    LOGGED_OUT,
    connection_expired,
    resolve_connection_lost,
    resolve_login,
    resolve_logout,
)
from app.engine.respawn import respawn_due_monsters
from app.engine.state import ActionSnapshot, GroupActionSnapshot
from app.services.events import EventPublisher, Relocation
from app.services.log import write_log

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRYABLE_SQLSTATES = {"40001", "40P01"}  # serialization failure, deadlock detected
TOO_FAST = "Nicht so schnell."
NOT_AVAILABLE = "Diese Aktion ist noch nicht verfügbar."
GROUP_ACTIONS: Final = (
    GroupInviteAction,
    GroupAcceptAction,
    GroupDeclineAction,
    GroupLeaveAction,
    GroupKickAction,
)


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


# A room action works on an ActionSnapshot, a group action on a GroupActionSnapshot.
S = TypeVar("S", ActionSnapshot, GroupActionSnapshot)
# locks & loads the snapshot for one attempt
Loader = Callable[[Repository, UUID, PreRead], Awaitable[S]]
# engine call for one attempt: (locked snapshot, locked actor row, now, rng) -> result
Resolver = Callable[[S, Player, datetime, random.Random], ActionResult]


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
# world ticker / after logout: housekeeping for characters that may already be offline
CLEANUP = Mode(require_online=False, rate_limited=False, player_action=False, lazy_respawn=False)


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
        if isinstance(action, GROUP_ACTIONS):
            return await self._group_action(player_id, action)

        def resolve(
            snapshot: ActionSnapshot, _actor: Player, now: datetime, rng: random.Random
        ) -> ActionResult:
            return self._dispatch(action, snapshot, now, rng)

        outcome = await self._run(
            player_id,
            self._room_loader(
                lambda pre: self._target_room(action, pre.room_id, pre.checkpoint_room_id),
                with_bank=isinstance(action, BankDepositAction | BankWithdrawAction),
            ),
            resolve,
            LOGOUT if isinstance(action, LogoutAction) else PLAYER_ACTION,
        )
        if isinstance(action, LogoutAction) and any(e.kind == LOGGED_OUT for e in outcome.events):
            await self._leave_group_quietly(player_id)
        return outcome

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

        return await self._run(player_id, self._room_loader(), resolve, LOGIN)

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

        outcome = await self._run(player_id, self._room_loader(), resolve, SYSTEM)
        if outcome.changed:
            await self._leave_group_quietly(player_id)
        return outcome

    async def idle_ambush(self, player_id: UUID) -> ActionOutcome:
        """World ticker: ``chance_per_tick`` ambush roll for a character idling in its room."""

        def resolve(
            snapshot: ActionSnapshot, _actor: Player, now: datetime, rng: random.Random
        ) -> ActionResult:
            return resolve_idle_ambush(snapshot, self.content, self.content.rules, now, rng)

        return await self._run(player_id, self._room_loader(), resolve, SYSTEM)

    async def leave_group(self, player_id: UUID) -> ActionOutcome:
        """Takes a character out of its group, also when it is already offline (after a logout,
        world ticker). Without a group nothing happens."""

        def resolve(
            snapshot: GroupActionSnapshot, _actor: Player, now: datetime, rng: random.Random
        ) -> ActionResult:
            if snapshot.actor.group_id is None:
                return ActionResult()
            return resolve_group_leave(snapshot, self.content, self.content.rules, now, rng)

        return await self._run(player_id, self._group_loader(), resolve, CLEANUP)

    async def _leave_group_quietly(self, player_id: UUID) -> None:
        """Best effort after a logout; the world ticker removes offline members it missed."""
        try:
            await self.leave_group(player_id)
        except Exception:
            logger.exception("leaving the group of %s after logout failed", player_id)

    async def _group_action(self, player_id: UUID, action: GameAction) -> ActionOutcome:
        other_id: UUID | None = None
        other_name: str | None = None
        if isinstance(action, GroupInviteAction):
            other_name = action.player_name
        elif isinstance(action, GroupAcceptAction | GroupDeclineAction):
            other_id = action.inviter_id
        elif isinstance(action, GroupKickAction):
            other_id = action.player_id

        def resolve(
            snapshot: GroupActionSnapshot, _actor: Player, now: datetime, rng: random.Random
        ) -> ActionResult:
            args = (snapshot, self.content, self.content.rules, now, rng)
            match action:
                case GroupInviteAction():
                    return resolve_group_invite(*args, action.player_name)
                case GroupAcceptAction():
                    return resolve_group_accept(*args, action.inviter_id)
                case GroupDeclineAction():
                    return resolve_group_decline(*args, action.inviter_id)
                case GroupKickAction():
                    return resolve_group_kick(*args, action.player_id)
                case _:
                    return resolve_group_leave(*args)

        return await self._run(
            player_id, self._group_loader(other_id, other_name), resolve, PLAYER_ACTION
        )

    # --- snapshot loaders -----------------------------------------------------------------------

    @staticmethod
    def _room_loader(
        target_room: Callable[[PreRead], str] = lambda pre: pre.room_id,
        *,
        with_bank: bool = False,
    ) -> Loader[ActionSnapshot]:
        """The room the action ends in (move: destination, respawn: checkpoint); bank actions
        also lock the actor's bank."""

        async def load(repo: Repository, player_id: UUID, pre: PreRead) -> ActionSnapshot:
            if with_bank:
                return await repo.load_action_snapshot(player_id, target_room(pre), with_bank=True)
            return await repo.load_action_snapshot(player_id, target_room(pre))

        return load

    @staticmethod
    def _group_loader(
        other_id: UUID | None = None, other_name: str | None = None
    ) -> Loader[GroupActionSnapshot]:
        async def load(repo: Repository, player_id: UUID, pre: PreRead) -> GroupActionSnapshot:
            return await repo.load_group_snapshot(
                player_id, other_id=other_id, other_name=other_name
            )

        return load

    async def _run(
        self,
        player_id: UUID,
        load: Loader[S],
        resolve: Resolver[S],
        mode: Mode,
    ) -> ActionOutcome:
        for _ in range(MAX_ATTEMPTS):
            try:
                outcome, to_publish, relocations = await self._attempt(
                    player_id, load, resolve, mode
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
        load: Loader[S],
        resolve: Resolver[S],
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

            repo = Repository(session)
            try:
                snapshot = await load(repo, player_id, pre)
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
            respawns: ActionResult = (
                respawn_due_monsters(snapshot, self.content, now)
                if mode.lazy_respawn and isinstance(snapshot, ActionSnapshot)
                else ActionResult()
            )
            await repo.apply_changes(respawns.changes)
            result: ActionResult = resolve(snapshot, actor, now, rng)
            await repo.apply_changes(result.changes)
            events = [*respawns.events, *result.events]
            relocations = self._relocations(snapshot, result)
            await write_log(session, player_id, events, now)

        actor_events = [e for e in events if isinstance(e.audience, ActorAudience)]
        others = [e for e in events if not isinstance(e.audience, ActorAudience)]
        return ActionOutcome(events=actor_events, changed=bool(events)), others, relocations

    @staticmethod
    def _relocations(
        snapshot: ActionSnapshot | GroupActionSnapshot, result: ActionResult
    ) -> list[Relocation]:
        """Players whose room or group changed (move, respawn, joining or leaving a group):
        their live connections must follow them."""
        relocations = []
        for update in result.changes.players:
            if not {"current_room_id", "group_id"} & update.model_fields_set:
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
            case BuyAction():
                return resolve_buy(*args, action.merchant_id, action.item_id, action.quantity)
            case SellAction():
                return resolve_sell(
                    *args, action.merchant_id, action.inventory_item_id, action.quantity
                )
            case BankDepositAction():
                return resolve_bank_deposit(*args, action.inventory_item_id, action.quantity)
            case BankWithdrawAction():
                return resolve_bank_withdraw(*args, action.bank_item_id, action.quantity)
            case _:
                # trade: later step
                return rejected(NOT_AVAILABLE)
