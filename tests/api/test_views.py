"""HTML pages and HTMX partials (forms, redirects, out-of-band panels, escaping)."""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient

from tests.api.conftest import PASSWORD, Api


async def signed_up(api: Api, client: AsyncClient) -> str:
    username = f"w_{uuid4().hex[:10]}"
    api.usernames.append(username)
    response = await client.post(
        "/welcome/register", data={"username": username, "password": PASSWORD}
    )
    assert (response.status_code, response.headers["location"]) == (303, "/select")
    return username


def character_name() -> str:
    return "Web" + uuid4().hex[:8].translate(str.maketrans("0123456789", "abcdefghij"))


async def playing(api: Api, client: AsyncClient) -> str:
    await signed_up(api, client)
    name = character_name()
    assert (await client.post("/select/new", data={"name": name})).status_code == 303
    character_id = (await client.get("/characters")).json()[0]["id"]
    response = await client.post(f"/select/{character_id}/play")
    assert (response.status_code, response.headers["location"]) == (303, "/")
    return name


async def test_pages_redirect_by_session_state(api: Api) -> None:
    async with api.client() as client:
        assert (await client.get("/")).headers["location"] == "/welcome"
        assert (await client.get("/select")).headers["location"] == "/welcome"
        page = await client.get("/welcome")
        assert page.status_code == 200 and "Neuer Account" in page.text

        await signed_up(api, client)
        assert (await client.get("/welcome")).headers["location"] == "/select"
        assert (await client.get("/")).headers["location"] == "/select"  # no character yet


async def test_login_errors_are_shown(api: Api) -> None:
    async with api.client() as client:
        username = await signed_up(api, client)
        await client.post("/select/logout")
        wrong = await client.post(
            "/welcome/login", data={"username": username, "password": "falsch123"}
        )
        assert wrong.status_code == 401 and "Passwort falsch" in wrong.text
        right = await client.post(
            "/welcome/login", data={"username": username, "password": PASSWORD}
        )
        assert right.headers["location"] == "/select"
        bad = await client.post("/select/new", data={"name": "<b>x</b>"})
        assert bad.status_code == 400 and "3–20 Buchstaben" in bad.text


async def test_game_page_shows_all_panels(api: Api) -> None:
    async with api.client() as client:
        name = await playing(api, client)
        page = await client.get("/")
        assert page.status_code == 200
        for marker in ('id="status"', 'id="room"', 'id="actions"', 'id="inventory"', 'id="log"'):
            assert marker in page.text
        assert 'sse-connect="/stream"' in page.text
        assert name in page.text
        assert "Marktplatz von Oakhaven" in page.text
        assert "Willkommen" in page.text  # log of the login


async def test_ui_action_returns_log_lines_and_oob_panels(api: Api) -> None:
    async with api.client() as client:
        await playing(api, client)
        api.clock.advance(seconds=1)
        response = await client.post("/ui/actions", data={"type": "say", "text": "<i>hi</i>"})
        assert response.status_code == 200
        assert '<li class="log-chat-say">Du sagst: &lt;i&gt;hi&lt;/i&gt;</li>' in response.text
        # status, room, actions, trade, shop, bank, quests, group, inventory
        assert response.text.count('hx-swap-oob="true"') == 15

        api.clock.advance(seconds=1)
        moved = await client.post("/ui/actions", data={"type": "move", "direction": "south"})
        assert "Südtor zum Wald" in moved.text

        nonsense = await client.post("/ui/actions", data={"type": "teleport"})
        assert "Das verstehe ich nicht." in nonsense.text


async def test_ui_logout_redirects_to_selection(api: Api) -> None:
    async with api.client() as client:
        await playing(api, client)
        response = await client.post("/ui/actions", data={"type": "logout"})
        assert response.headers["HX-Redirect"] == "/select"
        assert (await client.get("/ui/panels")).headers["HX-Redirect"] == "/select"
        assert (await client.get("/")).headers["location"] == "/select"


async def test_switching_out_of_a_fight_asks_first(api: Api) -> None:
    from tests.api.test_auth_and_characters import put_in_fight

    async with api.client() as client:
        await playing(api, client)
        fighter_id = (await client.get("/characters")).json()[0]["id"]
        await put_in_fight(api, fighter_id)
        await client.post("/select/new", data={"name": character_name()})
        other_id = next(
            c["id"] for c in (await client.get("/characters")).json() if c["id"] != fighter_id
        )

        page = await client.post(f"/select/{other_id}/play")
        assert page.status_code == 200
        assert "würde beim Ausloggen sterben" in page.text
        assert 'name="confirm_death" value="true"' in page.text

        confirmed = await client.post(f"/select/{other_id}/play", data={"confirm_death": "true"})
        assert confirmed.headers["location"] == "/"
        states = {c["id"]: c["state"] for c in (await client.get("/characters")).json()}
        assert states[fighter_id] == "dead"


async def test_static_files_are_served(api: Api) -> None:
    async with api.client() as client:
        for path in (
            "/static/vendor/htmx-2.0.4.min.js",
            "/static/vendor/htmx-ext-sse-2.2.2.js",
            "/static/app.js",
            "/static/style.css",
            "/static/icons/dagger.svg",
            "/static/fonts/press-start-2p-latin.woff2",
            "/static/fonts/vt323-latin.woff2",
        ):
            assert (await client.get(path)).status_code == 200, path


async def test_shop_and_bank_views(api: Api) -> None:
    from sqlalchemy import update
    from sqlmodel import col

    from app.db.models import InventoryItem, Player

    async with api.client() as client:
        await playing(api, client)
        character_id = (await client.get("/characters")).json()[0]["id"]
        async with api.committed.session() as db, db.begin():
            await db.execute(
                update(Player)
                .where(col(Player.id) == character_id)
                .values(current_room_id="town_market", gold=10)
            )
            db.add(InventoryItem(player_id=character_id, item_id="goblin_ear", quantity=3))

        page = (await client.get("/")).text
        assert "Alchemistin Elira" in page
        assert "Verkaufen (1 G)" in page and "Alle (3 G)" in page
        assert 'title="Zu wenig Gold"' in page  # healing potion costs 15
        assert 'id="bank"' in page and "Bankfach" not in page  # no bank here

        api.clock.advance(seconds=1)
        bought = await client.post(
            "/ui/actions",
            data={"type": "buy", "merchant_id": "alchemist", "item_id": "bread", "quantity": "1"},
        )
        assert "Du kaufst Frisches Brot für 3 Gold." in bought.text
        # what an item does is shown next to its name: in the shop and in the backpack
        assert '<small class="item-summary">heilt 15</small>' in page  # potion offer
        assert '<small class="item-summary">Tasche · +2 Plätze</small>' in page  # small bag
        assert '<small class="item-summary">heilt 6</small>' in bought.text  # bread carried
        # every item name can be inspected: description and numbers go into the log
        assert '{"item_id": "healing_potion", "type": "inspect"}' in page  # shop offer
        api.clock.advance(seconds=1)
        inspected = await client.post("/ui/actions", data={"type": "inspect", "item_id": "bread"})
        assert '<li class="log-item-inspected">Frisches Brot: ' in inspected.text
        assert "(heilt 6)</li>" in inspected.text

        async with api.committed.session() as db, db.begin():
            await db.execute(
                update(Player)
                .where(col(Player.id) == character_id)
                .values(current_room_id="town_bank")
            )
        page = (await client.get("/")).text
        assert "Bankfach" in page and "Einlagern" in page
