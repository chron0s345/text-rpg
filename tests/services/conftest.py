"""Service tests run against PostgreSQL (Testcontainers) with committed data, because the
service opens, commits and retries its own transactions."""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.content.loader import load_content
from app.content.registry import ContentRegistry
from app.services.action_service import ActionService
from app.services.events import RecordingPublisher
from tests.conftest import DATA_DIR
from tests.db.factories import NOW
from tests.pg import Committed


@dataclass
class Clock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@dataclass
class ServiceHarness:
    service: ActionService
    publisher: RecordingPublisher
    clock: Clock
    committed: Committed
    content: ContentRegistry = field(repr=False)
    scratch_rooms: list[str] = field(default_factory=list)

    def scratch_room(self, template: str = "dark_forest") -> str:
        """A private copy of a content room (unique id), so tests never share room state.
        Removed from the content and cleaned up in the database after the test."""
        room_id = f"{template}-{uuid4().hex[:6]}"
        self.content.rooms[room_id] = self.content.rooms[template].model_copy(
            update={"id": room_id}
        )
        self.scratch_rooms.append(room_id)
        self.committed.rooms.append(room_id)
        return room_id

    def scratch_path(self) -> tuple[str, str]:
        """Private copies of forest_gate and dark_forest, linked south/north to each other
        (and otherwise like the originals: unsafe, no checkpoint)."""
        gate, forest = self.scratch_room("forest_gate"), self.scratch_room("dark_forest")
        rooms = self.content.rooms
        rooms[gate] = rooms[gate].model_copy(update={"exits": {"south": forest}})
        rooms[forest] = rooms[forest].model_copy(update={"exits": {"north": gate}})
        return gate, forest


@pytest.fixture(scope="session")
def content() -> ContentRegistry:
    return load_content(DATA_DIR)


@pytest.fixture
async def harness(
    engine: AsyncEngine,
    committed: Committed,
    content: ContentRegistry,
) -> AsyncIterator[ServiceHarness]:
    publisher = RecordingPublisher()
    clock = Clock()
    service = ActionService(
        async_sessionmaker(engine, expire_on_commit=False),
        content,
        publisher,
        clock=clock,
        rng_factory=lambda: random.Random(7),
    )
    harness = ServiceHarness(service, publisher, clock, committed, content)
    try:
        yield harness
    finally:
        for room_id in harness.scratch_rooms:
            del content.rooms[room_id]
