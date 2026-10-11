"""API tests: the real app (lifespan with database and event bus, without the world ticker)
driven through ``httpx.AsyncClient``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import AppServices
from app.main import create_app
from app.settings import Settings
from tests.conftest import DATA_DIR
from tests.pg import Committed

PASSWORD = "geheim123"


@dataclass
class Api:
    app: FastAPI
    committed: Committed

    @property
    def services(self) -> AppServices:
        services: AppServices = self.app.state.services
        return services

    def client(self) -> AsyncClient:
        """A separate browser (own cookie jar)."""
        return AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    async def register(self, client: AsyncClient, username: str | None = None) -> UUID:
        username = username or f"u{uuid4().hex[:10]}"
        response = await client.post("/register", json={"username": username, "password": PASSWORD})
        assert response.status_code == 201, response.text
        account_id = UUID(response.json()["account_id"])
        self.committed.accounts.append(account_id)
        return account_id

    async def character(self, client: AsyncClient, *, play: bool = True) -> UUID:
        name = "Held" + "".join(chr(ord("a") + int(c, 16) % 26) for c in uuid4().hex[:8])
        response = await client.post("/characters", json={"name": name})
        assert response.status_code == 201, response.text
        player_id = UUID(response.json()["id"])
        if play:
            played = await client.post(f"/characters/{player_id}/play")
            assert played.json()["status"] == "ok", played.text
        return player_id


@pytest.fixture
async def api(database_url: str, committed: Committed) -> AsyncIterator[Api]:
    app = create_app(
        Settings(
            database_url=database_url,
            secret_key="test-secret",
            data_dir=DATA_DIR,
            icons_dir=DATA_DIR.parent / "app/web/static/icons",
            start_world_ticker=False,
        )
    )
    async with app.router.lifespan_context(app):
        # fast hashing in tests (production keeps argon2's defaults)
        app.state.services.accounts.hasher = PasswordHasher(
            time_cost=1, memory_cost=8, parallelism=1
        )
        yield Api(app, committed)


@pytest.fixture
async def client(api: Api) -> AsyncIterator[AsyncClient]:
    async with api.client() as client:
        yield client
