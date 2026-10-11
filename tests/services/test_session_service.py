"""SessionService against PostgreSQL: login (incl. switching characters), logout with town,
group and trade side effects, death on logout in combat, connection loss and its grace period
(world ticker), server start."""

from __future__ import annotations

import asyncio
import dataclasses
import random
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel import col

from app.content.registry import ContentRegistry
from app.db.models import (
    Account,
    CombatParticipation,
    Group,
    InventoryItem,
    LogEntry,
    MonsterInstance,
    Trade,
    TradeOffer,
)
from app.db.models import Player as PlayerRow
from app.engine.presence import CONFIRM_DEATH
from app.services.session_service import SessionService, UnknownCharacterError
from app.services.world_ticker import WorldTicker
from tests.db.factories import NOW, add_account, add_player
from tests.services.conftest import ServiceHarness

TOWN = "town_square"


def make_service(h: ServiceHarness, content: ContentRegistry | None = None) -> SessionService:
    return SessionService(
        async_sessionmaker(h.committed.engine, expire_on_commit=False),
        content or h.content,
        h.publisher,
        clock=h.clock,
        rng_factory=lambda: random.Random(7),
    )


@pytest.fixture
def sessions(harness: ServiceHarness) -> SessionService:
    return make_service(harness)


async def account(h: ServiceHarness) -> Account:
    async with h.committed.session() as db, db.begin():
        row = await add_account(db)
        h.committed.accounts.append(row.id)
    return row


async def player(h: ServiceHarness, room: str, **fields: Any) -> PlayerRow:
    h.committed.rooms.append(room)
    async with h.committed.session() as db, db.begin():
        owner = None
        if "account" in fields:
            owner = await db.get(Account, fields.pop("account").id)
        row = await add_player(db, owner, current_room_id=room, **fields)
        h.committed.accounts.append(row.account_id)
    return row


async def load(h: ServiceHarness, model: type[Any], key: object) -> Any:
    async with h.committed.session() as db:
        return await db.get(model, key)


async def logs(h: ServiceHarness, player_id: UUID) -> list[str]:
    async with h.committed.session() as db:
        rows = await db.scalars(
            select(LogEntry).where(col(LogEntry.player_id) == player_id).order_by(col(LogEntry.id))
        )
        return [r.text for r in rows.all()]


async def make_group(h: ServiceHarness, *members: PlayerRow) -> UUID:
    async with h.committed.session() as db, db.begin():
        group = Group(leader_id=members[0].id)
        db.add(group)
        await db.flush()
        for offset, member in enumerate(members):
            row = await db.get(PlayerRow, member.id)
            assert row is not None
            row.group_id, row.group_joined_at = group.id, NOW + timedelta(seconds=offset)
    return group.id


async def in_combat(h: ServiceHarness, fighter: PlayerRow, room: str) -> MonsterInstance:
    async with h.committed.session() as db, db.begin():
        goblin = MonsterInstance(
            monster_id="goblin", room_id=room, hp=12, tagged_by_party=f"solo:{fighter.id}"
        )
        db.add(goblin)
        await db.flush()
        db.add(
            CombatParticipation(
                monster_instance_id=goblin.id, player_id=fighter.id, party_key=f"solo:{fighter.id}"
            )
        )
        row = await db.get(PlayerRow, fighter.id)
        assert row is not None
        row.state, row.combat_target_id = "in_combat", goblin.id
    return goblin


def texts(events: list[Any]) -> list[str]:
    return [e.text for e in events]


# --- login ---


async def test_play_logs_in_and_tells_the_room(
    harness: ServiceHarness, sessions: SessionService
) -> None:
    h = harness
    room = h.scratch_room()
    hero = await player(h, room, online=False, name=f"Held{uuid4().hex[:4]}")
    watcher = await player(h, room)

    outcome = await sessions.play(hero.account_id, hero.id)

    assert outcome.status == "ok"
    assert texts(outcome.events) == [f"Willkommen zurück, {hero.name}. Du bist in Dunkler Wald."]
    row = await load(h, PlayerRow, hero.id)
    assert (row.online, row.disconnected_at) == (True, None)
    assert await logs(h, watcher.id) == [f"{hero.name} betritt die Welt."]
    assert [e.kind for e in h.publisher.published] == ["player.login"]


async def test_play_rejects_foreign_or_deleted_characters(
    harness: ServiceHarness, sessions: SessionService
) -> None:
    h = harness
    room = h.scratch_room()
    hero = await player(h, room, online=False)
    stranger = await account(h)
    gone = await player(h, room, online=False, deleted_at=NOW)

    with pytest.raises(UnknownCharacterError):
        await sessions.play(stranger.id, hero.id)
    with pytest.raises(UnknownCharacterError):
        await sessions.play(gone.account_id, gone.id)
    with pytest.raises(UnknownCharacterError):
        await sessions.play(hero.account_id, uuid4())
    assert (await load(h, PlayerRow, hero.id)).online is False


async def test_play_with_another_character_logs_that_one_out(
    harness: ServiceHarness, sessions: SessionService
) -> None:
    h = harness
    room = h.scratch_room()
    owner = await account(h)
    old = await player(h, room, account=owner)
    new = await player(h, room, account=owner, online=False)

    outcome = await sessions.play(owner.id, new.id)

    assert outcome.status == "ok"
    previous = await load(h, PlayerRow, old.id)
    assert (previous.online, previous.current_room_id) == (False, TOWN)
    assert (await load(h, PlayerRow, new.id)).online is True


async def test_switching_away_from_a_fight_needs_confirmation(
    harness: ServiceHarness, sessions: SessionService
) -> None:
    h = harness
    room = h.scratch_room()
    owner = await account(h)
    fighter = await player(h, room, account=owner)
    await in_combat(h, fighter, room)
    other = await player(h, room, account=owner, online=False)

    asked = await sessions.play(owner.id, other.id)

    assert asked.status == "confirm_death"
    assert texts(asked.events) == [CONFIRM_DEATH]
    assert (await load(h, PlayerRow, fighter.id)).state == "in_combat"
    assert (await load(h, PlayerRow, other.id)).online is False

    confirmed = await sessions.play(owner.id, other.id, confirm_death=True)

    assert confirmed.status == "ok"
    dead = await load(h, PlayerRow, fighter.id)
    assert (dead.state, dead.online, dead.current_room_id) == ("dead", False, room)
    assert (await load(h, PlayerRow, other.id)).online is True


async def test_concurrent_logins_of_one_account_leave_one_character_online(
    harness: ServiceHarness, sessions: SessionService
) -> None:
    h = harness
    room = h.scratch_room()
    owner = await account(h)
    chars = [await player(h, room, account=owner, online=False) for _ in range(3)]

    outcomes = await asyncio.gather(*(sessions.play(owner.id, c.id) for c in chars))

    assert {o.status for o in outcomes} == {"ok"}
    async with h.committed.session() as db:
        online = await db.scalar(
            select(func.count()).where(col(PlayerRow.account_id) == owner.id, col(PlayerRow.online))
        )
    assert online == 1


async def test_relogin_while_online_ends_the_grace_period_quietly(
    harness: ServiceHarness, sessions: SessionService
) -> None:
    h = harness
    room = h.scratch_room()
    hero = await player(h, room, disconnected_at=NOW - timedelta(seconds=5))
    watcher = await player(h, room)

    await sessions.play(hero.account_id, hero.id)

    assert (await load(h, PlayerRow, hero.id)).disconnected_at is None
    assert await logs(h, watcher.id) == []


# --- logout ---


async def test_logout_returns_to_town(harness: ServiceHarness, sessions: SessionService) -> None:
    h = harness
    room = h.scratch_room()
    hero = await player(h, room, name=f"Held{uuid4().hex[:4]}")
    watcher = await player(h, room)

    outcome = await sessions.logout(hero.id)

    assert texts(outcome.events) == ["Du hast dich ausgeloggt."]
    row = await load(h, PlayerRow, hero.id)
    assert (row.online, row.current_room_id) == (False, TOWN)
    assert await logs(h, watcher.id) == [f"{hero.name} verlässt die Welt."]
    # logging out twice is harmless
    assert (await sessions.logout(hero.id)).events == []


async def test_logout_in_combat_asks_first_then_dies(
    harness: ServiceHarness, sessions: SessionService
) -> None:
    h = harness
    room = h.scratch_room()
    hero = await player(h, room, gold=100)
    goblin = await in_combat(h, hero, room)

    asked = await sessions.logout(hero.id)
    assert asked.status == "confirm_death"
    assert (await load(h, PlayerRow, hero.id)).online is True

    done = await sessions.logout(hero.id, confirm_death=True)

    assert done.status == "ok"
    row = await load(h, PlayerRow, hero.id)
    assert (row.state, row.hp, row.online, row.current_room_id) == ("dead", 0, False, room)
    assert row.gold < 100
    assert await load(h, CombatParticipation, (goblin.id, hero.id)) is None
    assert (await load(h, MonsterInstance, goblin.id)).tagged_by_party is None


async def test_logout_leaves_the_group_and_hands_over_leadership(
    harness: ServiceHarness, sessions: SessionService
) -> None:
    h = harness
    room, elsewhere = h.scratch_room(), h.scratch_room()
    leader = await player(h, room)
    ben = await player(h, elsewhere)  # members in other rooms are locked and updated too
    cleo = await player(h, elsewhere)
    group_id = await make_group(h, leader, ben, cleo)

    await sessions.logout(leader.id)

    assert (await load(h, PlayerRow, leader.id)).group_id is None
    assert (await load(h, Group, group_id)).leader_id == ben.id
    assert await logs(h, ben.id) == [
        f"{leader.name} hat die Gruppe verlassen. Die Gruppe hat eine neue Führung."
    ]


async def test_logout_dissolves_a_group_of_two(
    harness: ServiceHarness, sessions: SessionService
) -> None:
    h = harness
    room, elsewhere = h.scratch_room(), h.scratch_room()
    anna, ben = await player(h, room), await player(h, elsewhere)
    group_id = await make_group(h, anna, ben)

    await sessions.logout(anna.id)

    assert await load(h, Group, group_id) is None
    remaining = await load(h, PlayerRow, ben.id)
    assert (remaining.group_id, remaining.group_joined_at) == (None, None)
    # Ben's live connection must stop listening to the old group
    assert any(r.player_id == ben.id and r.group_id is None for r in h.publisher.relocations)


async def test_logout_cancels_the_trade_and_frees_offered_items(
    harness: ServiceHarness, harness_content_with_sure_loss: ContentRegistry
) -> None:
    """Dying on logout may lose an item that is offered in the trade being cancelled: the offer
    must be gone before the item (no foreign key violation)."""
    h = harness
    room = h.scratch_room()
    hero, partner = await player(h, room), await player(h, room)
    await in_combat(h, hero, room)
    async with h.committed.session() as db, db.begin():
        bread = InventoryItem(player_id=hero.id, item_id="bread", quantity=1)
        trade = Trade(
            initiator_id=hero.id, partner_id=partner.id, room_id=room, status="open", expires_at=NOW
        )
        db.add_all([bread, trade])
        await db.flush()
        db.add(
            TradeOffer(trade_id=trade.id, inventory_item_id=bread.id, player_id=hero.id, quantity=1)
        )
    service = make_service(h, harness_content_with_sure_loss)

    await service.logout(hero.id, confirm_death=True)

    assert (await load(h, Trade, trade.id)).status == "cancelled"
    assert await load(h, TradeOffer, (trade.id, bread.id)) is None
    assert await load(h, InventoryItem, bread.id) is None  # lost on death
    assert f"Der Handel mit {hero.name} wurde abgebrochen." in await logs(h, partner.id)


@pytest.fixture
def harness_content_with_sure_loss(harness: ServiceHarness) -> ContentRegistry:
    rules = harness.content.rules
    death = rules.death.model_copy(update={"item_loss_chance": 1.0})
    return dataclasses.replace(harness.content, rules=rules.model_copy(update={"death": death}))


# --- connection ---


async def test_disconnect_and_reconnect(harness: ServiceHarness, sessions: SessionService) -> None:
    h = harness
    room = h.scratch_room()
    hero, partner = await player(h, room), await player(h, room)
    async with h.committed.session() as db, db.begin():
        trade = Trade(initiator_id=partner.id, partner_id=hero.id, room_id=room, expires_at=NOW)
        db.add(trade)

    await sessions.disconnect(hero.id)
    first = (await load(h, PlayerRow, hero.id)).disconnected_at
    h.clock.advance(seconds=10)
    await sessions.disconnect(hero.id)  # a second closed stream does not extend the grace

    assert first == NOW
    assert (await load(h, PlayerRow, hero.id)).disconnected_at == NOW
    assert (await load(h, Trade, trade.id)).status == "cancelled"

    assert await sessions.connect(hero.id) is True
    assert (await load(h, PlayerRow, hero.id)).disconnected_at is None


async def test_connect_fails_for_offline_characters(
    harness: ServiceHarness, sessions: SessionService
) -> None:
    hero = await player(harness, harness.scratch_room(), online=False)
    assert await sessions.connect(hero.id) is False
    assert await sessions.connect(uuid4()) is False
    await sessions.disconnect(hero.id)  # nothing to do
    assert (await load(harness, PlayerRow, hero.id)).disconnected_at is None


async def test_server_start_marks_online_characters_disconnected(
    harness: ServiceHarness, sessions: SessionService
) -> None:
    h = harness
    room = h.scratch_room()
    online = await player(h, room)
    earlier = NOW - timedelta(seconds=30)
    already = await player(h, room, disconnected_at=earlier)
    offline = await player(h, room, online=False)

    assert await sessions.mark_all_disconnected() >= 1

    assert (await load(h, PlayerRow, online.id)).disconnected_at == NOW
    assert (await load(h, PlayerRow, already.id)).disconnected_at == earlier
    assert (await load(h, PlayerRow, offline.id)).disconnected_at is None


# --- grace period over (world ticker) ---


@pytest.fixture
def ticker(engine: AsyncEngine, harness: ServiceHarness, sessions: SessionService) -> WorldTicker:
    return WorldTicker(
        engine,
        async_sessionmaker(engine, expire_on_commit=False),
        harness.content,
        harness.publisher,
        clock=harness.clock,
        session_service=sessions,
    )


async def test_ticker_logs_out_after_the_grace_period(
    harness: ServiceHarness, ticker: WorldTicker
) -> None:
    h = harness
    room = h.scratch_room()
    grace = timedelta(seconds=h.content.rules.timings.disconnect_grace_seconds)
    gone = await player(h, room, disconnected_at=NOW - grace)
    fighter = await player(h, room, disconnected_at=NOW - grace)
    await in_combat(h, fighter, room)
    waiting = await player(h, room, disconnected_at=NOW - grace + timedelta(seconds=1))

    report = await ticker.tick()

    assert report.disconnects_expired == 2
    row = await load(h, PlayerRow, gone.id)
    assert (row.online, row.current_room_id, row.disconnected_at) == (False, TOWN, None)
    dead = await load(h, PlayerRow, fighter.id)
    assert (dead.online, dead.state) == (False, "dead")
    assert "Deine Verbindung ist abgerissen. Du wurdest ausgeloggt." in await logs(h, fighter.id)
    assert (await load(h, PlayerRow, waiting.id)).online is True


async def test_reconnect_before_the_tick_wins(
    harness: ServiceHarness, sessions: SessionService
) -> None:
    h = harness
    grace = timedelta(seconds=h.content.rules.timings.disconnect_grace_seconds)
    hero = await player(h, h.scratch_room(), disconnected_at=NOW - grace)
    assert hero.id in await sessions.expired_disconnects(NOW)

    await sessions.connect(hero.id)  # between the ticker's pre-read and its locked check

    assert await sessions.expire_disconnect(hero.id, NOW) is False
    assert (await load(h, PlayerRow, hero.id)).online is True
