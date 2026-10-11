"""Retry policy for transactions that take row locks (ARCHITECTURE.md, "Ablauf einer Aktion").

Retried: ``SnapshotChangedError``, deadlocks and serialization failures, at most
``MAX_ATTEMPTS`` times.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.repository import SnapshotChangedError

T = TypeVar("T")

MAX_ATTEMPTS = 3
RETRYABLE_SQLSTATES = {"40001", "40P01"}  # serialization failure, deadlock detected


def is_retryable(exc: DBAPIError) -> bool:
    code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    return code in RETRYABLE_SQLSTATES


class RetryLimitExceededError(Exception):
    """Lock conflicts persisted over all attempts."""


async def in_transaction(
    sessions: async_sessionmaker[AsyncSession],
    attempt: Callable[[AsyncSession], Awaitable[T]],
) -> T:
    """Runs ``attempt`` in a new session + transaction and commits; retries lock conflicts.

    ``attempt`` must create its own ``Repository`` (a lock set never survives a retry) and must
    not publish anything: the caller publishes after this returns, i.e. after the commit.
    """
    for _ in range(MAX_ATTEMPTS):
        try:
            async with sessions() as session, session.begin():
                return await attempt(session)
        except SnapshotChangedError:
            continue
        except DBAPIError as exc:
            if is_retryable(exc):
                continue
            raise
    raise RetryLimitExceededError("transaction kept conflicting")
