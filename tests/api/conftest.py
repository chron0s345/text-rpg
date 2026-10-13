"""API tests: the real app (lifespan, event bus, services) against the test PostgreSQL.

Clock and RNG are injected so rate limits and fights are deterministic. Accounts created through
the API are deleted afterwards (characters, log entries etc. cascade).
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.main import create_app
from app.settings import Settings
from tests.pg import Committed
from tests.services.conftest import Clock

PASSWORD = "geheim123"


@dataclass
class Api:
    app: FastAPI
    clock: Clock
    committed: Committed
    usernames: list[str] = field(default_factory=list)

    def client(self) -> AsyncClient:
        return AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    async def player(self, client: AsyncClient, name: str | None = None) -> str:
        """Registers an account (logged in on ``client``), creates a character and plays it.
        Returns the character id."""
        username = f"u_{uuid4().hex[:10]}"
        self.usernames.append(username)
        response = await client.post("/register", json={"username": username, "password": PASSWORD})
        assert response.status_code == 201, response.text
        name = name or "Held" + "".join(chr(ord("a") + int(c, 16)) for c in uuid4().hex[:8])
        response = await client.post("/characters", json={"name": name})
        assert response.status_code == 201, response.text
        character_id: str = response.json()["id"]
        response = await client.post(f"/characters/{character_id}/play")
        assert response.status_code == 200, response.text
        return character_id


def settings_for(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        session_secret="test-secret",
        heartbeat_seconds=0.2,
        world_ticker=False,  # ticks are tested on their own (tests/services/test_world_ticker.py)
    )


@pytest.fixture
async def api(database_url: str, committed: Committed) -> AsyncIterator[Api]:
    clock = Clock()
    app = create_app(settings_for(database_url), clock=clock, rng_factory=lambda: random.Random(5))
    api = Api(app, clock, committed)
    async with app.router.lifespan_context(app):
        try:
            yield api
        finally:
            async with committed.session() as db, db.begin():
                await db.execute(
                    text("DELETE FROM account WHERE username = ANY(:names)"),
                    {"names": api.usernames},
                )
