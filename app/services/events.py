"""Publishing of game events after a successful commit.

The realtime layer (PostgreSQL LISTEN/NOTIFY + SSE, ARCHITECTURE.md "Echtzeit") implements
``EventPublisher``; services only depend on this interface.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.engine.events import GameEvent


@dataclass(frozen=True)
class Relocation:
    """A player's room or group changed: its live connections must switch subscriptions."""

    player_id: UUID
    room_id: str
    group_id: UUID | None


class EventPublisher(Protocol):
    async def publish(
        self, events: Sequence[GameEvent], relocations: Sequence[Relocation] = ()
    ) -> None:
        """Delivers events to their audiences and applies relocations (relocations first, so
        an event in the new room already reaches the moved player). Called only after the
        transaction committed.

        Best effort: a failure is logged by the caller and does not undo or fail the action;
        recipients catch up via their log entries.
        """


class NullPublisher:
    async def publish(
        self, events: Sequence[GameEvent], relocations: Sequence[Relocation] = ()
    ) -> None:
        return None


class RecordingPublisher:
    """Keeps published events in memory (tests, local debugging)."""

    def __init__(self) -> None:
        self.published: list[GameEvent] = []
        self.relocations: list[Relocation] = []

    async def publish(
        self, events: Sequence[GameEvent], relocations: Sequence[Relocation] = ()
    ) -> None:
        self.relocations.extend(relocations)
        self.published.extend(events)
