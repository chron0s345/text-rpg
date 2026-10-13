"""Protection against password guessing (and against burning CPU with argon2 hashes).

Every login attempt counts against two keys: the user name and the client address; every
registration against the client address. A key that used up its attempts within its window is
locked for a while; locked attempts are refused *before* any password is hashed.

The counters live in the database (``login_throttle``), so all server processes share them.
Counting is race-free: an attempt takes the row lock, decides with ``count_attempt`` and writes
back in one transaction, so parallel requests cannot all slip through before the first failure
is recorded. A successful login resets the user name and gives the address its attempt back.

Known trade-off: anybody can lock a user name for ``lockout`` by guessing wrong on purpose. The
lock is short and the real owner only has to wait; the alternative (unlimited guessing) is worse.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.db.models import LoginThrottle

MAX_KEY_LENGTH = 200


@dataclass(frozen=True)
class ThrottlePolicy:
    max_attempts: int  # attempts allowed per window
    window: timedelta
    lockout: timedelta  # after one attempt too many


@dataclass(frozen=True)
class ThrottleState:
    attempts: int
    window_started_at: datetime
    locked_until: datetime | None = None

    def expires_at(self, policy: ThrottlePolicy) -> datetime:
        """After this the state no longer matters (window over, lock over)."""
        window_end = self.window_started_at + policy.window
        return max(window_end, self.locked_until) if self.locked_until else window_end


def count_attempt(
    state: ThrottleState | None, now: datetime, policy: ThrottlePolicy
) -> tuple[ThrottleState, datetime | None]:
    """Counts one attempt. Returns the new state and, if the attempt is refused, until when.

    A running lock refuses without counting. After a lock or a finished window counting starts
    over. The attempt that exceeds ``max_attempts`` is refused and starts the lock.
    """
    if state is not None and state.locked_until is not None and state.locked_until > now:
        return state, state.locked_until
    if (
        state is None
        or state.locked_until is not None
        or now - state.window_started_at >= policy.window
    ):
        state = ThrottleState(attempts=0, window_started_at=now)
    attempts = state.attempts + 1
    if attempts > policy.max_attempts:
        locked_until = now + policy.lockout
        return ThrottleState(attempts, state.window_started_at, locked_until), locked_until
    return ThrottleState(attempts, state.window_started_at), None


class TooManyAttemptsError(Exception):
    def __init__(self, retry_after: timedelta) -> None:
        super().__init__(retry_after)
        self.retry_after = retry_after

    @property
    def message(self) -> str:
        minutes = max(1, -(-int(self.retry_after.total_seconds()) // 60))  # round up
        return "Zu viele Versuche. Bitte warte " + (
            "eine Minute." if minutes == 1 else f"{minutes} Minuten."
        )


def _key(kind: str, value: str) -> str:
    return f"{kind}:{value.strip().lower()}"[:MAX_KEY_LENGTH]


def user_key(username: str) -> str:
    return _key("user", username)


def address_key(address: str | None) -> str:
    return _key("ip", address or "unknown")


def registration_key(address: str | None) -> str:
    return _key("register", address or "unknown")


class LoginGuard:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime],
        per_user: ThrottlePolicy,
        per_address: ThrottlePolicy,
        registrations_per_address: ThrottlePolicy,
    ) -> None:
        self.sessions = sessions
        self.clock = clock
        self.per_user = per_user
        self.per_address = per_address
        self.registrations_per_address = registrations_per_address

    async def login_attempt(self, username: str, address: str | None) -> None:
        """Counts a login attempt; raises ``TooManyAttemptsError`` if it must not happen."""
        await self._attempt(
            [(user_key(username), self.per_user), (address_key(address), self.per_address)]
        )

    async def registration_attempt(self, address: str | None) -> None:
        await self._attempt([(registration_key(address), self.registrations_per_address)])

    async def login_succeeded(self, username: str, address: str | None) -> None:
        """The user name starts over; the address gets this attempt back (people behind one
        shared address should not lock each other out by logging in)."""
        async with self.sessions() as session, session.begin():
            await session.execute(
                delete(LoginThrottle).where(col(LoginThrottle.key) == user_key(username))
            )
            row = await session.scalar(
                select(LoginThrottle)
                .where(col(LoginThrottle.key) == address_key(address))
                .with_for_update()
            )
            if row is not None and row.locked_until is None and row.attempts > 0:
                row.attempts -= 1

    async def purge(self, now: datetime) -> int:
        """Deletes rows that no longer matter (world ticker)."""
        async with self.sessions() as session, session.begin():
            removed = await session.execute(
                delete(LoginThrottle)
                .where(col(LoginThrottle.expires_at) <= now)
                .returning(col(LoginThrottle.key))
            )
            return len(removed.all())

    async def _attempt(self, keys: Iterable[tuple[str, ThrottlePolicy]]) -> None:
        now = self.clock()
        refused_until: datetime | None = None
        async with self.sessions() as session, session.begin():
            counted: list[tuple[LoginThrottle, ThrottleState, ThrottlePolicy, datetime | None]] = []
            for key, policy in sorted(keys, key=lambda kp: kp[0]):  # fixed lock order
                await session.execute(
                    insert(LoginThrottle)
                    .values(key=key, attempts=0, window_started_at=now, expires_at=now)
                    .on_conflict_do_nothing(index_elements=["key"])
                )
                row = await session.scalar(
                    select(LoginThrottle)
                    .where(col(LoginThrottle.key) == key)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                assert row is not None
                previous = ThrottleState(row.attempts, row.window_started_at, row.locked_until)
                state, refused = count_attempt(previous, now, policy)
                counted.append((row, state, policy, refused))
                if refused is not None:
                    refused_until = max(refused_until or refused, refused)
            for row, state, policy, refused in counted:
                # a refused attempt only (re)locks the keys that ran out; it does not use up
                # attempts of the other keys, since nothing was tried
                if refused_until is not None and refused is None:
                    continue
                row.attempts = state.attempts
                row.window_started_at = state.window_started_at
                row.locked_until = state.locked_until
                row.expires_at = state.expires_at(policy)
        if refused_until is not None:
            raise TooManyAttemptsError(refused_until - now)
