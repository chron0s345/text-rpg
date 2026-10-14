"""Password change (JSON and the form on the selection page): it ends every other session of
the account, logs its characters out and counts wrong guesses against the login limit."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient

from tests.api.conftest import PASSWORD, Api
from tests.api.test_auth_and_characters import put_in_fight

NEW_PASSWORD = "ganz-neu-42"


@pytest.fixture
def api_settings() -> dict[str, Any]:
    return {"login_attempts_per_user": 3, "login_lockout_seconds": 300}


async def account(api: Api, client: AsyncClient) -> str:
    username = f"u_{uuid4().hex[:10]}"
    api.usernames.append(username)
    response = await client.post("/register", json={"username": username, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return username


async def login(client: AsyncClient, username: str, password: str) -> int:
    response = await client.post("/login", json={"username": username, "password": password})
    return response.status_code


def change(current: str = PASSWORD, new: str = NEW_PASSWORD, **extra: Any) -> dict[str, Any]:
    return {"current_password": current, "new_password": new, **extra}


async def test_change_ends_the_other_sessions_and_logs_characters_out(api: Api) -> None:
    async with api.client() as mine, api.client() as other:
        username = await account(api, mine)
        assert await login(other, username, PASSWORD) == 200
        name = "Dieb" + uuid4().hex[:8].translate(str.maketrans("0123456789", "abcdefghij"))
        created = await other.post("/characters", json={"name": name})
        character_id = created.json()["id"]
        assert (await other.post(f"/characters/{character_id}/play")).status_code == 200

        assert (await mine.post("/password", json=change())).status_code == 204

        assert (await other.get("/characters")).status_code == 401
        assert (await other.post("/actions", json={"type": "look"})).status_code == 401
        [character] = (await mine.get("/characters")).json()  # this session stays
        assert not character["online"]
        assert await login(other, username, PASSWORD) == 401
        assert await login(other, username, NEW_PASSWORD) == 200
        assert (await other.get("/characters")).status_code == 200


async def test_nothing_changes_on_a_wrong_or_invalid_request(api: Api) -> None:
    async with api.client() as mine, api.client() as other:
        username = await account(api, mine)
        assert await login(other, username, PASSWORD) == 200

        wrong = await mine.post("/password", json=change(current="falsch!!"))
        assert wrong.status_code == 403
        assert wrong.json()["detail"] == "Das aktuelle Passwort stimmt nicht."
        assert (await mine.post("/password", json=change(new="kurz"))).status_code == 422

        assert (await other.get("/characters")).status_code == 200
        assert await login(other, username, PASSWORD) == 200
        async with api.client() as nobody:
            assert (await nobody.post("/password", json=change())).status_code == 401


async def test_wrong_current_passwords_count_against_the_login_limit(api: Api) -> None:
    async with api.client() as client:
        username = await account(api, client)
        for _ in range(3):
            response = await client.post("/password", json=change(current="falsch!!"))
            assert response.status_code == 403

        locked = await client.post("/password", json=change())
        assert (locked.status_code, locked.headers["Retry-After"]) == (429, "300")
        async with api.client() as other:
            assert await login(other, username, PASSWORD) == 429  # same counter as the login


async def test_a_character_in_combat_needs_confirmation(api: Api) -> None:
    async with api.client() as client:
        character_id = await api.player(client)
        await put_in_fight(api, character_id)

        refused = await client.post("/password", json=change())
        assert refused.status_code == 409
        assert "im Kampf" in refused.json()["detail"]
        api.clock.advance(seconds=1)
        confirmed = await client.post("/password", json=change(confirm_death=True))
        assert confirmed.status_code == 204
        [character] = (await client.get("/characters")).json()
        assert (character["state"], character["online"]) == ("dead", False)


async def test_password_form_on_the_selection_page(api: Api) -> None:
    async with api.client() as mine, api.client() as other:
        username = await account(api, mine)
        assert await login(other, username, PASSWORD) == 200
        assert "Passwort ändern" in (await mine.get("/select")).text

        wrong = await mine.post("/select/password", data=change(current="falsch!!"))
        assert wrong.status_code == 400 and "Das aktuelle Passwort stimmt nicht." in wrong.text

        done = await mine.post("/select/password", data=change())
        assert (done.status_code, done.headers["location"]) == (303, "/select?passwort=neu")
        assert "Passwort geändert." in (await mine.get(done.headers["location"])).text
        assert (await other.get("/select")).headers["location"] == "/welcome"
