"""SessionService against PostgreSQL: choosing characters, logout (incl. death in combat), and
the connection bookkeeping of SSE streams."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.api.schemas import LookAction
from app.db.models import Account, CombatParticipation, LogEntry, MonsterInstance
from app.db.models import Player as PlayerRow
from app.engine.presence import CONFIRM_REQUIRED, LOGGED_IN, LOGGED_OUT
from app.services.account_service import CharacterNotFoundError
from app.services.action_service import NotPlayingError
from app.services.session_service import (
    ConfirmDeathRequiredError,
    SessionService,
)
from tests.db.factories import NOW, add_account, add_player
from tests.services.conftest import ServiceHarness


@pytest.fixture
def presence(harness: ServiceHarness) -> SessionService:
    return SessionService(harness.service.sessions, harness.service, clock=harness.clock)


async def characters(h: ServiceHarness, room: str, *specs: dict[str, Any]) -> list[PlayerRow]:
    """Characters of ONE account (offline unless a spec says otherwise)."""
    h.committed.rooms.append(room)
    async with h.committed.session() as db, db.begin():
        account = await add_account(db)
        h.committed.accounts.append(account.id)
        return [
            await add_player(db, account, current_room_id=room, **{"online": False, **spec})
            for spec in specs
        ]


async def in_fight(h: ServiceHarness, room: str, **fields: Any) -> PlayerRow:
    """An online character fighting a goblin (with the participation every fight has)."""
    async with h.committed.session() as db, db.begin():
        monster = MonsterInstance(monster_id="goblin", room_id=room, hp=10)
        db.add(monster)
    [fighter] = await characters(
        h, room, {"online": True, "state": "in_combat", "combat_target_id": monster.id, **fields}
    )
    async with h.committed.session() as db, db.begin():
        db.add(
            CombatParticipation(
                monster_instance_id=monster.id,
                player_id=fighter.id,
                party_key=f"solo:{fighter.id}",
            )
        )
    return fighter


async def load(h: ServiceHarness, player_id: UUID) -> PlayerRow:
    async with h.committed.session() as db:
        row = await db.get(PlayerRow, player_id)
        assert row is not None
        return row


async def log_kinds(h: ServiceHarness, player_id: UUID) -> list[str]:
    async with h.committed.session() as db:
        rows = await db.scalars(
            select(col(LogEntry.kind))
            .where(col(LogEntry.player_id) == player_id)
            .order_by(col(LogEntry.id))
        )
        return list(rows.all())


async def test_play_logs_in_and_tells_the_room(
    harness: ServiceHarness, presence: SessionService
) -> None:
    h = harness
    room = h.scratch_room("town_square")
    [hero] = await characters(h, room, {})
    [watcher] = await characters(h, room, {"online": True})

    outcome = await presence.play(hero.account_id, hero.id)

    assert [e.kind for e in outcome.events] == [LOGGED_IN]
    row = await load(h, hero.id)
    assert row.online and row.disconnected_at is None
    assert [e.kind for e in h.publisher.published] == ["player.entered_world"]
    assert await log_kinds(h, watcher.id) == ["player.entered_world"]
    # now the character can act
    await h.service.execute(hero.id, LookAction())


async def test_play_switches_characters_of_the_same_account(
    harness: ServiceHarness, presence: SessionService
) -> None:
    h = harness
    forest = h.scratch_room()
    first, second = await characters(h, forest, {"online": True}, {})

    await presence.play(first.account_id, second.id)

    old, new = await load(h, first.id), await load(h, second.id)
    assert not old.online and old.current_room_id == h.content.rules.town_room_id
    assert new.online
    assert LOGGED_OUT in await log_kinds(h, first.id)


async def test_switching_away_from_a_fight_needs_confirmation(
    harness: ServiceHarness, presence: SessionService
) -> None:
    h = harness
    forest = h.scratch_room()
    fighter = await in_fight(h, forest, gold=100)
    async with h.committed.session() as db, db.begin():
        account = await db.get(Account, fighter.account_id)
        other = await add_player(db, account, current_room_id=forest, online=False)

    with pytest.raises(ConfirmDeathRequiredError):
        await presence.play(fighter.account_id, other.id)
    assert (await load(h, fighter.id)).online
    assert not (await load(h, other.id)).online

    h.clock.advance(seconds=1)
    await presence.play(fighter.account_id, other.id, confirm_death=True)
    dead = await load(h, fighter.id)
    assert (dead.state, dead.online, dead.current_room_id) == ("dead", False, forest)
    assert dead.gold < 100
    assert (await load(h, other.id)).online


async def test_logout_account_logs_out_every_online_character(
    harness: ServiceHarness, presence: SessionService
) -> None:
    h = harness
    forest = h.scratch_room()
    online, offline = await characters(h, forest, {"online": True}, {})

    await presence.logout_account(online.account_id)

    assert not (await load(h, online.id)).online
    assert LOGGED_OUT in await log_kinds(h, online.id)
    assert await log_kinds(h, offline.id) == []


async def test_logout_account_in_a_fight_needs_confirmation(
    harness: ServiceHarness, presence: SessionService
) -> None:
    h = harness
    fighter = await in_fight(h, h.scratch_room())

    with pytest.raises(ConfirmDeathRequiredError) as refused:
        await presence.logout_account(fighter.account_id)
    assert refused.value.name == fighter.name
    assert (await load(h, fighter.id)).online

    await presence.logout_account(fighter.account_id, confirm_death=True)
    assert (await load(h, fighter.id)).state == "dead"


async def test_logout_reports_confirmation_and_is_not_rate_limited(
    harness: ServiceHarness, presence: SessionService
) -> None:
    h = harness
    fighter = await in_fight(h, h.scratch_room())
    await h.service.execute(fighter.id, LookAction())  # sets last_action_at = now

    result = await presence.logout(fighter.id)
    assert result.status == "confirm_required"
    assert [e.kind for e in result.events] == [CONFIRM_REQUIRED]

    result = await presence.logout(fighter.id, confirm_death=True)  # same instant: no rate limit
    assert result.status == "logged_out"
    assert (await presence.logout(fighter.id)).status == "logged_out"  # already offline


async def test_play_unknown_or_foreign_character(
    harness: ServiceHarness, presence: SessionService
) -> None:
    h = harness
    room = h.scratch_room("town_square")
    [mine] = await characters(h, room, {})
    [foreign] = await characters(h, room, {})
    with pytest.raises(CharacterNotFoundError):
        await presence.play(mine.account_id, foreign.id)


async def test_connection_bookkeeping(harness: ServiceHarness, presence: SessionService) -> None:
    h = harness
    room = h.scratch_room("town_square")
    [hero] = await characters(h, room, {"online": True, "disconnected_at": NOW})
    [offline] = await characters(h, room, {})

    target = await presence.connect(hero.id)
    assert target is not None and target.room_id == room
    assert (await load(h, hero.id)).disconnected_at is None
    assert await presence.connect(offline.id) is None

    await presence.disconnected(hero.id)
    assert (await load(h, hero.id)).disconnected_at == h.clock.now
    assert await presence.heartbeat(hero.id)  # a live stream clears the deadline again
    assert (await load(h, hero.id)).disconnected_at is None
    assert not await presence.heartbeat(offline.id)


async def test_server_start_marks_online_characters_disconnected(
    harness: ServiceHarness, presence: SessionService
) -> None:
    h = harness
    room = h.scratch_room("town_square")
    [hero] = await characters(h, room, {"online": True})
    await presence.mark_all_disconnected()
    assert (await load(h, hero.id)).disconnected_at == h.clock.now
    # actions still work while the grace period runs
    await h.service.execute(hero.id, LookAction())


async def test_logged_out_character_cannot_act(
    harness: ServiceHarness, presence: SessionService
) -> None:
    h = harness
    [hero] = await characters(h, h.scratch_room(), {"online": True})
    await presence.logout(hero.id)
    with pytest.raises(NotPlayingError):
        await h.service.execute(hero.id, LookAction())
