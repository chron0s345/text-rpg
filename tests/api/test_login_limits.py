"""Login and registration limits through the routes (JSON and HTML forms): 429 + Retry-After,
and a locked name stays locked even for the right password."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from tests.api.conftest import PASSWORD, Api


@pytest.fixture
def api_settings() -> dict[str, Any]:
    return {
        "login_attempts_per_user": 3,
        "login_attempts_per_address": 100,
        "registrations_per_address": 3,
        "login_window_seconds": 900,
        "login_lockout_seconds": 300,
    }


async def registered(api: Api) -> str:
    username = f"u_{uuid4().hex[:10]}"
    api.usernames.append(username)
    async with api.client() as client:
        response = await client.post("/register", json={"username": username, "password": PASSWORD})
        assert response.status_code == 201, response.text
    return username


async def test_json_login_locks_the_user_name(api: Api) -> None:
    username = await registered(api)
    async with api.client() as client:
        for _ in range(3):
            wrong = await client.post("/login", json={"username": username, "password": "falsch!!"})
            assert wrong.status_code == 401

        locked = await client.post("/login", json={"username": username, "password": PASSWORD})

        assert locked.status_code == 429
        assert locked.headers["Retry-After"] == "300"
        assert locked.json()["detail"] == "Zu viele Versuche. Bitte warte 5 Minuten."

        api.clock.advance(seconds=300)
        assert (
            await client.post("/login", json={"username": username, "password": PASSWORD})
        ).status_code == 200


async def test_successful_login_resets_the_count(api: Api) -> None:
    username = await registered(api)
    async with api.client() as client:
        for _ in range(2):
            await client.post("/login", json={"username": username, "password": "falsch!!"})
        assert (
            await client.post("/login", json={"username": username, "password": PASSWORD})
        ).status_code == 200
        for _ in range(3):  # a fresh set of attempts
            wrong = await client.post("/login", json={"username": username, "password": "falsch!!"})
            assert wrong.status_code == 401


async def test_form_login_shows_the_wait(api: Api) -> None:
    username = await registered(api)
    async with api.client() as client:
        for _ in range(3):
            await client.post("/welcome/login", data={"username": username, "password": "falsch!!"})
        locked = await client.post(
            "/welcome/login", data={"username": username, "password": PASSWORD}
        )
    assert locked.status_code == 429
    assert "Zu viele Versuche. Bitte warte 5 Minuten." in locked.text
    assert locked.headers["Retry-After"] == "300"


async def test_registrations_per_address(api: Api) -> None:
    for _ in range(3):
        await registered(api)
    async with api.client() as client:
        refused = await client.post(
            "/register", json={"username": f"u_{uuid4().hex[:10]}", "password": PASSWORD}
        )
        form = await client.post(
            "/welcome/register",
            data={"username": f"u_{uuid4().hex[:10]}", "password": PASSWORD},
        )
    assert (refused.status_code, form.status_code) == (429, 429)
