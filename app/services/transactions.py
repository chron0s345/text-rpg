"""Retry policy for transactions that take row locks (ARCHITECTURE.md, "Ablauf einer Aktion").

Retried: ``SnapshotChangedError`` (handled by the callers), deadlocks and serialization failures.
"""

from __future__ import annotations

from sqlalchemy.exc import DBAPIError

MAX_ATTEMPTS = 3
RETRYABLE_SQLSTATES = {"40001", "40P01"}  # serialization failure, deadlock detected


def is_retryable(exc: DBAPIError) -> bool:
    code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    return code in RETRYABLE_SQLSTATES


class RetryLimitExceededError(Exception):
    """Lock conflicts persisted over all attempts."""
