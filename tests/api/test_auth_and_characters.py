"""Auth, character selection, actions, logout confirmation, rate limit, log (via httpx)."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import update
from sqlmodel import col

from app.db.models import CombatParticipation, MonsterInstance, Player
from tests.api.conftest import PASSWORD, Api

DIGITS = str.maketrans("0123456789", "ghijklmnop")


def kinds(response_json: dict[str, list[dict[str, str]]]) -> list[str]:
    return [e["kind"] for e in response_json["events"]]


async def test_register_login_logout(api: Api) -> None:
    username = f"u_{uuid4().hex[:10]}"
    api.usernames.append(username)
    async with api.client() as client:
        assert (await client.get("/characters")).status_code == 401
        response = await client.post("/register", json={"username": username, "password": PASSWORD})
        assert response.status_code == 201
        assert (await client.get("/characters")).json() == []  # registered = logged in

        again = await client.post("/register", json={"username": username, "password": PASSWORD})
        assert again.status_code == 409
        assert (await client.post("/logout")).status_code == 204
        assert (await client.get("/characters")).status_code == 401

        wrong = await client.post("/login", json={"username": username, "password": "falsch123"})
        assert wrong.status_code == 401
        right = await client.post("/login", json={"username": username, "password": PASSWORD})
        assert right.status_code == 200
        assert right.json()["username"] == username
        assert (await client.get("/characters")).status_code == 200


async def test_invalid_input_is_422(api: Api) -> None:
    async with api.client() as client:
        response = await client.post("/register", json={"username": "x", "password": PASSWORD})
        assert response.status_code == 422
        await api.player(client)
        assert (await client.post("/characters", json={"name": "<script>"})).status_code == 422
        assert (await client.post("/actions", json={"type": "teleport"})).status_code == 422
        assert (await client.post("/actions", json={"type": "say", "text": ""})).status_code == 422


async def test_play_and_act(api: Api) -> None:
    async with api.client() as client:
        character_id = await api.player(client)
        [character] = (await client.get("/characters")).json()
        assert character["id"] == character_id and character["online"]

        response = await client.post("/actions", json={"type": "look"})
        assert response.status_code == 200
        [look] = response.json()["events"]
        assert look["kind"] == "room.look"
        assert look["data"]["room_id"] == "town_square"

        # rate limit: same instant -> rejected as a game message, not an HTTP error
        response = await client.post("/actions", json={"type": "say", "text": "Hallo"})
        assert response.status_code == 200
        assert [e["text"] for e in response.json()["events"]] == ["Nicht so schnell."]
        api.clock.advance(seconds=1)
        response = await client.post("/actions", json={"type": "say", "text": "Hallo"})
        assert [e["text"] for e in response.json()["events"]] == ["Du sagst: Hallo"]


async def test_actions_need_a_character_in_the_world(api: Api) -> None:
    async with api.client() as client:
        assert (await client.post("/actions", json={"type": "look"})).status_code == 401
        character_id = await api.player(client)
        response = await client.post("/actions", json={"type": "logout"})
        assert kinds(response.json()) == ["session.logged_out"]
        assert (await client.post("/actions", json={"type": "look"})).status_code == 401
        [character] = (await client.get("/characters")).json()
        assert character["id"] == character_id and not character["online"]


async def test_other_accounts_characters_cannot_be_played(api: Api) -> None:
    async with api.client() as owner, api.client() as stranger:
        character_id = await api.player(owner)
        await api.player(stranger)
        response = await stranger.post(f"/characters/{character_id}/play")
        assert response.status_code == 404
        assert (await stranger.delete(f"/characters/{character_id}")).status_code == 404


async def test_delete_character(api: Api) -> None:
    async with api.client() as client:
        character_id = await api.player(client)
        assert (await client.delete(f"/characters/{character_id}")).status_code == 409  # online
        await client.post("/actions", json={"type": "logout"})
        assert (await client.delete(f"/characters/{character_id}")).status_code == 204
        assert (await client.get("/characters")).json() == []


async def put_in_fight(api: Api, character_id: str) -> None:
    room = "dark_forest"
    async with api.committed.session() as db, db.begin():
        monster = MonsterInstance(monster_id="goblin", room_id=room, hp=10)
        db.add(monster)
        await db.flush()
        await db.execute(
            update(Player)
            .where(col(Player.id) == character_id)
            .values(current_room_id=room, state="in_combat", combat_target_id=monster.id)
        )
        db.add(
            CombatParticipation(
                monster_instance_id=monster.id,
                player_id=UUID(character_id),
                party_key=f"solo:{character_id}",
            )
        )
    api.committed.rooms.append(room)


async def test_logout_in_combat_needs_confirmation(api: Api) -> None:
    async with api.client() as client:
        character_id = await api.player(client)
        await put_in_fight(api, character_id)

        assert (await client.post("/logout")).status_code == 409
        response = await client.post("/actions", json={"type": "logout"})
        assert kinds(response.json()) == ["session.confirm_death"]

        response = await client.post("/logout", json={"confirm_death": True})
        assert response.status_code == 204
        username = api.usernames[-1]
        await client.post("/login", json={"username": username, "password": PASSWORD})
        [character] = (await client.get("/characters")).json()
        assert (character["state"], character["online"]) == ("dead", False)


async def test_switching_character_out_of_a_fight_needs_confirmation(api: Api) -> None:
    async with api.client() as client:
        fighter_id = await api.player(client)
        await put_in_fight(api, fighter_id)
        response = await client.post(
            "/characters", json={"name": "Zweit" + uuid4().hex[:8].translate(DIGITS)}
        )
        other_id = response.json()["id"]

        assert (await client.post(f"/characters/{other_id}/play")).status_code == 409
        response = await client.post(f"/characters/{other_id}/play", json={"confirm_death": True})
        assert response.status_code == 200
        assert kinds(response.json()) == ["session.logged_in"]
        states = {
            c["id"]: (c["state"], c["online"]) for c in (await client.get("/characters")).json()
        }
        assert states == {fighter_id: ("dead", False), other_id: ("exploring", True)}


async def test_log_paging(api: Api) -> None:
    async with api.client() as client:
        await api.player(client)
        for i in range(3):
            api.clock.advance(seconds=1)
            await client.post("/actions", json={"type": "say", "text": f"Nachricht {i}"})

        entries = (await client.get("/log")).json()["entries"]
        texts = [e["text"] for e in entries]
        assert texts[-3:] == [
            "Du sagst: Nachricht 0",
            "Du sagst: Nachricht 1",
            "Du sagst: Nachricht 2",
        ]
        first_say = entries[-3]["id"]
        after = (await client.get(f"/log?after={first_say}")).json()["entries"]
        assert [e["text"] for e in after] == ["Du sagst: Nachricht 1", "Du sagst: Nachricht 2"]
        before = (await client.get(f"/log?before={first_say}")).json()["entries"]
        assert all(e["id"] < first_say for e in before)
        assert (await client.get("/log?before=5&after=1")).status_code == 422


async def test_tampered_session_cookie_is_ignored(api: Api) -> None:
    async with api.client() as client:
        await api.player(client)
        client.cookies.set("rpg_session", "forged.value")
        assert (await client.get("/characters")).status_code == 401
