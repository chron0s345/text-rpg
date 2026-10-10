"""Which character of an account is in the world, and its connection state
(ARCHITECTURE.md, "Einloggen, Ausloggen & Verbindungsabbruch", "Verbindungsstatus").

Login and logout run through ``ActionService`` (same locking, logging and publishing as any
action). This service adds the account rules around them: one character per account at a time,
switching characters logs out the previous one (death in combat only with confirmation), and the
``disconnected_at`` bookkeeping of SSE connections. Processing expired disconnect deadlines is the
world ticker's job.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.api.schemas import LogoutAction
from app.db.models import Player
from app.engine.events import GameEvent
from app.engine.presence import CONFIRM_REQUIRED, LOGGED_OUT
from app.services.account_service import CharacterNotFoundError
from app.services.action_service import ActionOutcome, ActionService, NotPlayingError, utc_now


class ConfirmDeathRequiredError(Exception):
    """The character to log out is in combat; logging out kills it."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} ist im Kampf und würde beim Ausloggen sterben.")
        self.name = name


class SessionConflictError(Exception):
    """A concurrent login/logout/action of the same account got in the way; try again."""


@dataclass(frozen=True)
class LogoutResult:
    status: Literal["logged_out", "confirm_required", "rejected"]
    events: list[GameEvent]


@dataclass(frozen=True)
class StreamTarget:
    """What a new SSE connection subscribes to."""

    room_id: str
    group_id: UUID | None


class SessionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        actions: ActionService,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.sessions = sessions
        self.actions = actions
        self.clock = clock

    # --- login / logout ---------------------------------------------------------------------

    async def play(
        self, account_id: UUID, character_id: UUID, *, confirm_death: bool = False
    ) -> ActionOutcome:
        """Selects a character. Another online character of the account is logged out first
        (exactly like a logout, including death in combat after confirmation)."""
        async with self.sessions() as session:
            target = await session.get(Player, character_id)
            if target is None or target.account_id != account_id or target.deleted_at is not None:
                raise CharacterNotFoundError("Diesen Charakter gibt es nicht.")
            others = (
                await session.scalars(
                    select(col(Player.id)).where(
                        col(Player.account_id) == account_id,
                        col(Player.online),
                        col(Player.id) != character_id,
                    )
                )
            ).all()

        for other_id in others:
            result = await self.logout(other_id, confirm_death=confirm_death)
            if result.status == "confirm_required":
                async with self.sessions() as session:
                    other = await session.get(Player, other_id)
                raise ConfirmDeathRequiredError(other.name if other else "Dein Charakter")
            if result.status == "rejected":
                raise SessionConflictError("Der andere Charakter ist gerade beschäftigt.")

        try:
            return await self.actions.login(character_id)
        except NotPlayingError as exc:  # deleted in the meantime
            raise CharacterNotFoundError("Diesen Charakter gibt es nicht.") from exc
        except IntegrityError as exc:  # one online character per account (partial unique index)
            raise SessionConflictError("Ein anderer Charakter wurde gerade eingeloggt.") from exc

    async def logout(self, player_id: UUID, *, confirm_death: bool = False) -> LogoutResult:
        """Logs the character out (a character that is already offline counts as logged out)."""
        try:
            outcome = await self.actions.execute(
                player_id, LogoutAction(confirm_death=confirm_death)
            )
        except NotPlayingError:
            return LogoutResult("logged_out", [])
        kinds = {event.kind for event in outcome.events}
        if CONFIRM_REQUIRED in kinds:
            return LogoutResult("confirm_required", outcome.events)
        if LOGGED_OUT in kinds:
            return LogoutResult("logged_out", outcome.events)
        return LogoutResult("rejected", outcome.events)  # another action was in flight

    # --- connection state (SSE) -------------------------------------------------------------

    async def connect(self, player_id: UUID) -> StreamTarget | None:
        """A stream opened: clears a running disconnect deadline. ``None`` if the character is
        not in the world (offline or deleted)."""
        async with self.sessions() as session, session.begin():
            row = (
                await session.execute(
                    update(Player)
                    .where(
                        col(Player.id) == player_id,
                        col(Player.online),
                        col(Player.deleted_at).is_(None),
                    )
                    .values(disconnected_at=None)
                    .returning(col(Player.current_room_id), col(Player.group_id))
                )
            ).first()
        return StreamTarget(row[0], row[1]) if row is not None else None

    async def heartbeat(self, player_id: UUID) -> bool:
        """Periodic check of a live stream: still in the world? Clears a deadline that was set
        while this stream was connected (e.g. another worker restarted). Writes only then."""
        async with self.sessions() as session, session.begin():
            row = (
                await session.execute(
                    select(col(Player.disconnected_at)).where(
                        col(Player.id) == player_id,
                        col(Player.online),
                        col(Player.deleted_at).is_(None),
                    )
                )
            ).first()
            if row is None:
                return False
            if row[0] is not None:
                await session.execute(
                    update(Player)
                    .where(col(Player.id) == player_id, col(Player.disconnected_at).is_not(None))
                    .values(disconnected_at=None)
                )
            return True

    async def disconnected(self, player_id: UUID) -> None:
        """The last stream of the character closed: the grace period starts. The character
        stays in the world (and can still be attacked) until the ticker processes it."""
        async with self.sessions() as session, session.begin():
            await session.execute(
                update(Player)
                .where(
                    col(Player.id) == player_id,
                    col(Player.online),
                    col(Player.disconnected_at).is_(None),
                )
                .values(disconnected_at=self.clock())
            )

    async def mark_all_disconnected(self) -> int:
        """Server start: online characters have no connection yet, so the same grace period
        applies to them as to a lost connection. Returns the number of characters marked."""
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                update(Player)
                .where(col(Player.online), col(Player.disconnected_at).is_(None))
                .values(disconnected_at=self.clock())
                .returning(col(Player.id))
            )
            return len(result.all())
