"""Routes end to end: auth, character selection, actions (incl. rate limit and logout
confirmation), log catch-up (ARCHITECTURE.md, "Tests": API)."""

from __future__ import annotations

from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlmodel import col

from app.api.cookies import COOKIE_NAME
from app.db.models import CombatParticipation, MonsterInstance, Player
from app.services.action_service import TOO_FAST
from tests.api.conftest import PASSWORD, Api


def texts(response_json: dict[str, object]) -> list[str]:
    events = response_json["events"]
    assert isinstance(events, list)
    return [e["text"] for e in events]


async def put_in_combat(api: Api, player_id: UUID) -> None:
    async with api.committed.session() as db, db.begin():
        player = await db.get(Player, player_id)
        assert player is not None
        room = f"dark_forest-{uuid4().hex[:6]}"
        api.services.content.rooms[room] = api.services.content.rooms["dark_forest"].model_copy(
            update={"id": room}
        )
        api.committed.rooms.append(room)
        goblin = MonsterInstance(monster_id="goblin", room_id=room, hp=12)
        db.add(goblin)
        await db.flush()
        db.add(
            CombatParticipation(
                monster_instance_id=goblin.id, player_id=player_id, party_key=f"solo:{player_id}"
            )
        )
        player.current_room_id, player.state, player.combat_target_id = room, "in_combat", goblin.id


# --- accounts ---


async def test_register_login_and_logout(api: Api, client: AsyncClient) -> None:
    username = f"Anna{uuid4().hex[:6]}"
    account_id = await api.register(client, username)
    assert client.cookies.get(COOKIE_NAME)
    assert (await client.get("/characters")).json() == []

    # usernames are unique ignoring case
    taken = await client.post(
        "/register", json={"username": username.upper(), "password": PASSWORD}
    )
    assert taken.status_code == 409

    await client.post("/logout")
    assert (await client.get("/characters")).status_code == 401

    wrong = await client.post("/login", json={"username": username, "password": "falsch!!"})
    unknown = await client.post("/login", json={"username": "niemand_xyz", "password": PASSWORD})
    assert (wrong.status_code, unknown.status_code) == (401, 401)
    assert wrong.json() == unknown.json()  # no hint which one exists

    ok = await client.post("/login", json={"username": username.lower(), "password": PASSWORD})
    assert ok.status_code == 200
    assert ok.json() == {"account_id": str(account_id)}
    assert (await client.get("/characters")).status_code == 200


async def test_invalid_registration_input(client: AsyncClient) -> None:
    for body in (
        {"username": "ab", "password": PASSWORD},
        {"username": "mit leerzeichen", "password": PASSWORD},
        {"username": "okname", "password": "kurz"},
    ):
        assert (await client.post("/register", json=body)).status_code == 422


async def test_forged_cookie_is_rejected(api: Api, client: AsyncClient) -> None:
    await api.register(client)
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    client.cookies.set(COOKIE_NAME, f"{uuid4()}.{cookie.split('.', 1)[1]}")
    assert (await client.get("/characters")).status_code == 401


# --- characters ---


async def test_create_list_play_and_delete(api: Api, client: AsyncClient) -> None:
    await api.register(client)
    player_id = await api.character(client, play=False)

    [listed] = (await client.get("/characters")).json()
    assert listed["id"] == str(player_id)
    assert (listed["level"], listed["state"], listed["online"], listed["room_id"]) == (
        1,
        "exploring",
        False,
        "town_square",
    )

    played = (await client.post(f"/characters/{player_id}/play")).json()
    assert played["status"] == "ok"
    assert texts(played)[0].startswith("Willkommen zurück")

    assert (await client.delete(f"/characters/{player_id}")).status_code == 409  # online
    await client.post("/actions", json={"type": "logout"})
    assert (await client.delete(f"/characters/{player_id}")).status_code == 204
    assert (await client.get("/characters")).json() == []


async def test_character_rules(api: Api, client: AsyncClient) -> None:
    await api.register(client)
    first = await api.character(client, play=False)
    [row] = (await client.get("/characters")).json()

    assert (await client.post("/characters", json={"name": "A1"})).status_code == 422
    duplicate = await client.post("/characters", json={"name": row["name"].upper()})
    assert duplicate.status_code == 409
    for _ in range(api.services.content.rules.max_characters_per_account - 1):
        await api.character(client, play=False)
    assert (await client.post("/characters", json={"name": "Zuviel"})).status_code == 409
    assert first


async def test_other_accounts_characters_are_invisible(api: Api, client: AsyncClient) -> None:
    await api.register(client)
    mine = await api.character(client, play=False)
    async with api.client() as other:
        await api.register(other)
        assert (await other.post(f"/characters/{mine}/play")).status_code == 404
        assert (await other.delete(f"/characters/{mine}")).status_code == 404


# --- actions ---


async def test_actions_need_a_character_in_the_world(api: Api, client: AsyncClient) -> None:
    assert (await client.post("/actions", json={"type": "look"})).status_code == 401
    await api.register(client)
    assert (await client.post("/actions", json={"type": "look"})).status_code == 401
    await api.character(client)
    assert (await client.post("/actions", json={"type": "look"})).status_code == 200
    assert (await client.post("/actions", json={"type": "fly"})).status_code == 422
    assert (await client.post("/actions", json={"type": "say", "text": ""})).status_code == 422


async def test_rate_limit(api: Api, client: AsyncClient) -> None:
    await api.register(client)
    await api.character(client)

    first = await client.post("/actions", json={"type": "look"})
    second = await client.post("/actions", json={"type": "say", "text": "Hallo"})

    assert first.status_code == second.status_code == 200
    assert texts(second.json()) == [TOO_FAST]


async def test_logout_in_combat_asks_for_confirmation(api: Api, client: AsyncClient) -> None:
    await api.register(client)
    player_id = await api.character(client)
    await put_in_combat(api, player_id)

    asked = (await client.post("/logout")).json()
    assert asked["status"] == "confirm_death"
    assert client.cookies.get(COOKIE_NAME)  # still logged in: the client shows the dialog

    done = await client.post("/logout", json={"confirm_death": True})

    assert done.json()["status"] == "ok"
    assert (await client.get("/characters")).status_code == 401
    async with api.committed.session() as db:
        row = await db.get(Player, player_id)
        assert row is not None and (row.state, row.online) == ("dead", False)


async def test_logout_action_keeps_the_account_session(api: Api, client: AsyncClient) -> None:
    await api.register(client)
    player_id = await api.character(client)
    await put_in_combat(api, player_id)

    asked = (await client.post("/actions", json={"type": "logout"})).json()
    assert asked["status"] == "confirm_death"
    done = await client.post("/actions", json={"type": "logout", "confirm_death": True})

    assert done.json()["status"] == "ok"
    assert (await client.get("/characters")).status_code == 200
    assert (await client.post("/actions", json={"type": "look"})).status_code == 401


async def test_switching_characters_in_combat_needs_confirmation(
    api: Api, client: AsyncClient
) -> None:
    await api.register(client)
    fighter = await api.character(client)
    await put_in_combat(api, fighter)
    other = await api.character(client, play=False)

    asked = (await client.post(f"/characters/{other}/play")).json()
    assert asked["status"] == "confirm_death"
    done = await client.post(f"/characters/{other}/play", json={"confirm_death": True})
    assert done.json()["status"] == "ok"

    async with api.committed.session() as db:
        online = await db.scalars(
            select(col(Player.id)).where(col(Player.id).in_([fighter, other]), col(Player.online))
        )
        assert list(online.all()) == [other]


# --- log ---


async def test_log_pages(api: Api, client: AsyncClient) -> None:
    await api.register(client)
    await api.character(client)

    latest = (await client.get("/log")).json()["entries"]
    assert [e["text"] for e in latest][-1].startswith("Willkommen zurück")
    newest_id = latest[-1]["id"]

    await client.post("/actions", json={"type": "look"})

    after = (await client.get("/log", params={"after": newest_id})).json()["entries"]
    assert after and all(e["id"] > newest_id for e in after)
    before = (await client.get("/log", params={"before": after[0]["id"], "limit": 1})).json()
    assert [e["id"] for e in before["entries"]] == [newest_id]
    assert (await client.get("/log", params={"limit": 0})).status_code == 422


async def test_stream_needs_a_character_in_the_world(api: Api, client: AsyncClient) -> None:
    await api.register(client)
    assert (await client.get("/stream")).status_code == 401
