"""Panels that the other view tests do not render: monsters and floor items in the room, the group
panel with members, the trade window with offered items, and deleting/logging out on the
character selection. Template errors would otherwise only show up in a browser."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlmodel import col

from app.db.models import FloorItem, InventoryItem, MonsterInstance, Player, Trade
from tests.api.conftest import Api
from tests.api.test_views import playing, signed_up


async def character_id(client: AsyncClient) -> str:
    return str((await client.get("/characters")).json()[0]["id"])


async def move_to(api: Api, player_id: str, room: str) -> None:
    async with api.committed.session() as db, db.begin():
        await db.execute(
            update(Player).where(col(Player.id) == player_id).values(current_room_id=room)
        )


async def act(api: Api, client: AsyncClient, **form: str) -> str:
    api.clock.advance(seconds=1)  # rate limit
    response = await client.post("/ui/actions", data=form)
    assert response.status_code == 200, response.text
    return response.text


async def test_room_shows_monsters_and_visible_floor_items(api: Api) -> None:
    room = f"dark_forest-view-{uuid4().hex[:6]}"
    content = api.app.state.content
    content.rooms[room] = content.rooms["dark_forest"].model_copy(update={"id": room})
    api.committed.rooms.append(room)
    try:
        async with api.client() as client:
            await playing(api, client)
            me = await character_id(client)
            await move_to(api, me, room)
            async with api.committed.session() as db, db.begin():
                db.add(MonsterInstance(monster_id="goblin", room_id=room, hp=7))
                db.add(MonsterInstance(monster_id="wolf", room_id=room, hp=0, alive=False))
                db.add(
                    FloorItem(
                        room_id=room,
                        item_id="goblin_ear",
                        quantity=3,
                        source="drop",
                        expires_at=api.clock.now + timedelta(minutes=5),
                    )
                )
                db.add(
                    FloorItem(
                        room_id=room,
                        item_id="healing_potion",
                        source="loot",
                        expires_at=api.clock.now - timedelta(seconds=1),
                    )
                )

            page = (await client.get("/")).text

        assert "Goblin-Späher" in page and "7/12 HP" in page
        assert "Hungriger Wolf" not in page  # dead monsters are not shown
        assert '"type": "attack"' in page
        assert "Am Boden" in page and "3× <button" in page and ">Goblinohr</button>" in page
        assert 'src="/static/icons/trophy.svg"' in page
        assert "Heiltrank" not in page.split('id="room"')[1].split("</section>")[0]  # expired
    finally:
        del content.rooms[room]


async def test_group_panel_with_members(api: Api) -> None:
    async with api.client() as anna, api.client() as ben:
        await playing(api, anna)
        ben_name = await playing(api, ben)
        anna_id = await character_id(anna)

        await act(api, anna, type="group_invite", player_name=ben_name)
        invited = (await ben.get("/")).text
        assert "lädt dich ein" in invited and '"type": "group_accept"' in invited
        await act(api, ben, type="group_accept", inviter_id=anna_id)

        leader_view = (await anna.get("/")).text
        member_view = (await ben.get("/")).text

    for view in (leader_view, member_view):
        assert "2 Mitglieder" in view and "Leitung" in view
        assert "<meter" in view and "Gruppe verlassen" in view
    assert '"type": "group_kick"' in leader_view  # only the leader removes members
    assert '"type": "group_kick"' not in member_view


async def test_trade_window_with_offered_items(api: Api) -> None:
    async with api.client() as anna, api.client() as ben:
        await playing(api, anna)
        await playing(api, ben)
        anna_id, ben_id = await character_id(anna), await character_id(ben)
        async with api.committed.session() as db, db.begin():
            potions = InventoryItem(player_id=UUID(anna_id), item_id="healing_potion", quantity=4)
            db.add(potions)

        await act(api, anna, type="trade_request", target_player_id=ben_id)
        async with api.committed.session() as db:
            trade = (
                await db.scalars(select(Trade).where(col(Trade.initiator_id) == anna_id))
            ).one()
        request_view = (await ben.get("/")).text
        assert "möchte mit dir handeln" in request_view
        await act(api, ben, type="trade_accept", trade_id=str(trade.id))
        await act(
            api,
            anna,
            type="trade_offer_item",
            trade_id=str(trade.id),
            inventory_item_id=str(potions.id),
            quantity="3",
        )

        mine = (await anna.get("/")).text
        theirs = (await ben.get("/")).text

    assert "3× Heiltrank" in mine and "Heiltrank zurücknehmen" in mine  # remove button
    assert "3× Heiltrank" in theirs and "zurücknehmen" not in theirs
    assert "Stand 1" in theirs and "Handel bestätigen" in theirs


async def test_select_page_delete_and_logout(api: Api) -> None:
    async with api.client() as client:
        await signed_up(api, client)
        for name in ("Erster", "Zweiter"):
            await client.post(
                "/select/new",
                data={
                    "name": name
                    + uuid4().hex[:4].translate(str.maketrans("0123456789", "abcdefghij"))
                },
            )
        characters = (await client.get("/characters")).json()
        first, second = characters[0]["id"], characters[1]["id"]
        await client.post(f"/select/{first}/play")

        online = await client.post(f"/select/{first}/delete")
        assert online.status_code == 409 and 'role="alert"' in online.text

        gone = await client.post(f"/select/{second}/delete")
        assert gone.headers["location"] == "/select"
        assert [c["id"] for c in (await client.get("/characters")).json()] == [first]

        logged_out = await client.post("/select/logout")
        assert logged_out.headers["location"] == "/welcome"
        assert (await client.get("/select")).headers["location"] == "/welcome"
    async with api.committed.session() as db:
        row = await db.get(Player, first)
        assert row is not None and not row.online  # the character left the world too


async def test_bestiary_and_achievement_panels(api: Api) -> None:
    async with api.client() as client:
        await playing(api, client)
        me = await character_id(client)
        async with api.committed.session() as db, db.begin():
            await db.execute(
                update(Player)
                .where(col(Player.id) == me)
                .values(
                    kills={"goblin": 3, "golden_goblin": 1, "ignaros": 1, "gone": 9},
                    achievements=["kill_ignaros"],
                    title="kill_ignaros",
                )
            )
        page = (await client.get("/")).text
        assert "Bestiarium" in page and "2/" in page and "5 besiegt" in page
        assert "Goldener Goblin" in page and "Glutfürst Ignaros" in page and "3×" in page
        assert "Feuerbändiger" in page  # the title, in the status bar
        panels = await act(api, client, type="look")
        assert 'id="bestiary"' in panels


async def test_travel_from_the_market_square(api: Api) -> None:
    async with api.client() as client:
        await playing(api, client)
        me = await character_id(client)
        page = (await client.get("/")).text
        assert "Reisen" in page and "Fischerdorf Möwenstein" in page
        async with api.committed.session() as db, db.begin():
            await db.execute(update(Player).where(col(Player.id) == me).values(level=3, gold=12))
        response = await act(api, client, type="travel", room_id="fishing_village")
        assert "Du nimmst die Postkutsche nach Fischerdorf Möwenstein für 10 Gold." in response
        async with api.committed.session() as db:
            row = await db.get(Player, UUID(me))
            assert row is not None
            assert (row.current_room_id, row.gold, row.checkpoint_room_id) == (
                "fishing_village",
                2,
                "fishing_village",
            )


async def test_online_panel_lists_who_is_playing(api: Api) -> None:
    async with api.client() as first, api.client() as second:
        anna = await playing(api, first)
        bert = await playing(api, second)
        async with api.committed.session() as db, db.begin():
            await db.execute(
                update(Player)
                .where(col(Player.name) == bert)
                .values(level=12, achievements=["kill_ignaros"], title="kill_ignaros")
            )
        page = (await first.get("/")).text
        panel = page[page.index('id="online"') :]
        panel = panel[: panel.index("</section>")]
        assert anna in panel and bert in panel and "Feuerbändiger" in panel
        assert panel.index(bert) < panel.index(anna)  # highest level first
        assert 'class="me"' in panel


async def test_healing_items_at_hand_in_a_fight(api: Api) -> None:
    async with api.client() as client:
        await playing(api, client)
        me = await character_id(client)
        async with api.committed.session() as db, db.begin():
            db.add(InventoryItem(player_id=UUID(me), item_id="healing_potion", quantity=3))
            db.add(InventoryItem(player_id=UUID(me), item_id="strength_potion", quantity=1))
            db.add(InventoryItem(player_id=UUID(me), item_id="short_sword", quantity=1))
            await db.execute(update(Player).where(col(Player.id) == me).values(state="in_combat"))

        def actions_panel(page: str) -> str:
            panel = page[page.index('id="actions"') :]
            return panel[: panel.index("</section>")]

        fighting = actions_panel((await client.get("/")).text)
        assert '"type": "use_item"' in fighting and "Heiltrank" in fighting and "×3" in fighting
        assert "Trank der Stärke" in fighting  # boosts are at hand, too
        assert "Kurzschwert" not in fighting
        async with api.committed.session() as db, db.begin():
            await db.execute(update(Player).where(col(Player.id) == me).values(state="exploring"))
        calm = actions_panel((await client.get("/")).text)  # full HP in town: nothing to heal
        assert '"type": "use_item"' not in calm


async def test_leaderboard_ranks_by_level_and_achievements(api: Api) -> None:
    async with api.client() as first, api.client() as second:
        low = await playing(api, first)
        high = await playing(api, second)
        async with api.committed.session() as db, db.begin():
            await db.execute(
                update(Player)
                .where(col(Player.name) == high)
                .values(level=15, xp=999_999, achievements=["kill_morvath", "level_15"])
            )
            await db.execute(
                update(Player).where(col(Player.name) == low).values(achievements=["level_5"])
            )
        page = (await first.get("/")).text
        panel = page[page.index('id="leaderboard"') :]
        panel = panel[: panel.index("</section>")]
        by_level, by_achievements = panel.split("<h3>Erfolge</h3>")
        assert high in by_level and "Stufe 15" in by_level
        assert by_level.index(high) < by_level.index("</li>")  # first place
        assert "2 Erfolge" in by_achievements and "1 Erfolg" in by_achievements
        assert 'class="me"' in panel
