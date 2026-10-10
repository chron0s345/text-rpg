"""Publishing of game events after a successful commit.

The realtime layer (PostgreSQL LISTEN/NOTIFY + SSE, ARCHITECTURE.md "Echtzeit") implements
``EventPublisher``; services only depend on this interface.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.engine.events import GameEvent


class EventPublisher(Protocol):
    async def publish(self, events: Sequence[GameEvent]) -> None:
        """Delivers events to their audiences. Called only after the transaction committed."""


class NullPublisher:
    async def publish(self, events: Sequence[GameEvent]) -> None:
        return None


class RecordingPublisher:
    """Keeps published events in memory (tests, local debugging)."""

    def __init__(self) -> None:
        self.published: list[GameEvent] = []

    async def publish(self, events: Sequence[GameEvent]) -> None:
        self.published.extend(events)
