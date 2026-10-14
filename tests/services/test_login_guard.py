"""Login protection: the pure counting rule, and LoginGuard against PostgreSQL (shared counters,
no race between parallel attempts, reset on success, cleanup)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import LoginThrottle
from app.services.login_guard import (
    LoginGuard,
    ThrottlePolicy,
    ThrottleState,
    TooManyAttemptsError,
    address_key,
    count_attempt,
    user_key,
)
from app.services.world_ticker import WorldTicker
from tests.db.factories import NOW
from tests.services.conftest import ServiceHarness

POLICY = ThrottlePolicy(max_attempts=3, window=timedelta(minutes=15), lockout=timedelta(minutes=5))


# --- the counting rule ---


def test_attempts_within_the_limit_are_allowed() -> None:
    state = None
    for expected in (1, 2, 3):
        state, refused = count_attempt(state, NOW, POLICY)
        assert (state.attempts, refused) == (expected, None)


def test_one_attempt_too_many_locks() -> None:
    state = ThrottleState(attempts=3, window_started_at=NOW)
    later = NOW + timedelta(minutes=1)

    locked, refused = count_attempt(state, later, POLICY)

    assert refused == later + POLICY.lockout
    assert locked.locked_until == refused
    # while locked nothing is counted, the lock does not grow
    again, refused_again = count_attempt(locked, later + timedelta(minutes=1), POLICY)
    assert (again, refused_again) == (locked, refused)


def test_counting_starts_over_after_the_lock_or_the_window() -> None:
    locked = ThrottleState(attempts=4, window_started_at=NOW, locked_until=NOW + POLICY.lockout)
    after_lock = NOW + POLICY.lockout
    assert count_attempt(locked, after_lock, POLICY) == (ThrottleState(1, after_lock), None)

    old = ThrottleState(attempts=3, window_started_at=NOW)
    after_window = NOW + POLICY.window
    assert count_attempt(old, after_window, POLICY) == (ThrottleState(1, after_window), None)


def test_state_expires_after_window_and_lock() -> None:
    assert ThrottleState(1, NOW).expires_at(POLICY) == NOW + POLICY.window
    long_lock = ThrottleState(4, NOW, locked_until=NOW + timedelta(hours=1))
    assert long_lock.expires_at(POLICY) == NOW + timedelta(hours=1)


def test_wait_message_rounds_up() -> None:
    assert TooManyAttemptsError(timedelta(seconds=5)).message.endswith("eine Minute.")
    assert TooManyAttemptsError(timedelta(seconds=61)).message.endswith("2 Minuten.")


# --- LoginGuard against PostgreSQL ---


@pytest.fixture
async def guard(harness: ServiceHarness) -> AsyncIterator[LoginGuard]:
    yield LoginGuard(
        harness.service.sessions,
        clock=harness.clock,
        per_user=POLICY,
        per_address=ThrottlePolicy(5, POLICY.window, POLICY.lockout),
        registrations_per_address=ThrottlePolicy(2, POLICY.window, POLICY.lockout),
    )
    async with harness.committed.session() as db, db.begin():
        await db.execute(text("DELETE FROM login_throttle"))


def names() -> tuple[str, str]:
    return f"user_{uuid4().hex[:8]}", f"10.0.{uuid4().int % 250}.{uuid4().int % 250}"


async def attempts_of(h: ServiceHarness, key: str) -> int | None:
    async with h.committed.session() as db:
        row = await db.get(LoginThrottle, key)
        return None if row is None else row.attempts


async def test_user_name_locks_after_its_attempts(
    harness: ServiceHarness, guard: LoginGuard
) -> None:
    user, address = names()
    for _ in range(3):
        await guard.login_attempt(user, address)

    with pytest.raises(TooManyAttemptsError) as refused:
        await guard.login_attempt(user.upper(), "other-address")  # case-insensitive name

    assert refused.value.retry_after == POLICY.lockout
    # the refused attempt did not use up the other address's attempts: nothing was tried
    assert await attempts_of(harness, address_key("other-address")) == 0

    harness.clock.advance(seconds=POLICY.lockout.total_seconds())
    await guard.login_attempt(user, address)  # lock over, counting starts again


async def test_address_locks_across_user_names(harness: ServiceHarness, guard: LoginGuard) -> None:
    _, address = names()
    for _ in range(5):
        await guard.login_attempt(f"user_{uuid4().hex[:8]}", address)
    with pytest.raises(TooManyAttemptsError):
        await guard.login_attempt(f"user_{uuid4().hex[:8]}", address)


async def test_success_resets_the_name_and_refunds_the_address(
    harness: ServiceHarness, guard: LoginGuard
) -> None:
    user, address = names()
    await guard.login_attempt(user, address)
    await guard.login_attempt(user, address)

    await guard.login_succeeded(user, address)

    assert await attempts_of(harness, user_key(user)) is None
    assert await attempts_of(harness, address_key(address)) == 1


async def test_parallel_attempts_cannot_slip_through(
    harness: ServiceHarness, guard: LoginGuard
) -> None:
    """All requests fire before any failure is recorded: still exactly the allowed number pass."""
    user, address = names()

    results = await asyncio.gather(
        *(guard.login_attempt(user, address) for _ in range(12)), return_exceptions=True
    )

    allowed = [r for r in results if r is None]
    refused = [r for r in results if isinstance(r, TooManyAttemptsError)]
    assert (len(allowed), len(refused)) == (3, 9)


async def test_registrations_per_address(harness: ServiceHarness, guard: LoginGuard) -> None:
    _, address = names()
    await guard.registration_attempt(address)
    await guard.registration_attempt(address)
    with pytest.raises(TooManyAttemptsError):
        await guard.registration_attempt(address)
    await guard.registration_attempt("somewhere-else")


async def test_ticker_removes_rows_that_no_longer_matter(
    harness: ServiceHarness, guard: LoginGuard, engine: AsyncEngine
) -> None:
    h = harness
    old_user, address = names()
    await guard.login_attempt(old_user, address)
    h.clock.advance(seconds=POLICY.window.total_seconds())
    fresh_user, _ = names()
    await guard.login_attempt(fresh_user, "fresh-address")

    ticker = WorldTicker(
        engine,
        h.service.sessions,
        h.content,
        h.service,
        h.publisher,
        clock=h.clock,
        login_guard=guard,
    )
    report = await ticker.tick()

    assert report.login_throttles_removed >= 2  # old user name and its address
    assert await attempts_of(h, user_key(old_user)) is None
    assert await attempts_of(h, user_key(fresh_user)) == 1
