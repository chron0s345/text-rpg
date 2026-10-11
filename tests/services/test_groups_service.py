"""Group actions against PostgreSQL: members in different rooms, relocations for live
connections, logout leaves the group, ticker cleanup, concurrent accepts."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import col

from app.api.schemas import (
    GroupAcceptAction,
    GroupInviteAction,
    GroupKickAction,
    GroupLeaveAction,
    LogoutAction,
)
from app.db.models import Group, GroupInvite, LogEntry
from app.db.models import Player as PlayerRow
from app.services.world_ticker import WorldTicker
from tests.db.factories import add_player
from tests.services.conftest import ServiceHarness


async def player(h: ServiceHarness, room: str, **fields: Any) -> PlayerRow:
    h.committed.rooms.append(room)
    async with h.committed.session() as db, db.begin():
        row = await add_player(db, current_room_id=room, **fields)
        h.committed.accounts.append(row.account_id)
    return row


async def load(h: ServiceHarness, player_id: UUID) -> PlayerRow:
    async with h.committed.session() as db:
        row = await db.get(PlayerRow, player_id)
        assert row is not None
        return row


async def act(h: ServiceHarness, player_id: UUID, action: Any) -> list[str]:
    h.clock.advance(seconds=1)  # stay clear of the rate limit
    return [e.text for e in (await h.service.execute(player_id, action)).events]


async def form_group(h: ServiceHarness, leader: PlayerRow, *members: PlayerRow) -> UUID:
    for member in members:
        await act(h, leader.id, GroupInviteAction(player_name=member.name))
        await act(h, member.id, GroupAcceptAction(inviter_id=leader.id))
    group_id = (await load(h, leader.id)).group_id
    assert group_id is not None
    return group_id


async def test_invite_and_accept_across_rooms(harness: ServiceHarness) -> None:
    h = harness
    town, forest = h.scratch_room("town_square"), h.scratch_room()
    anna, ben = await player(h, town), await player(h, forest)

    assert await act(h, anna.id, GroupInviteAction(player_name=ben.name.upper())) == [
        f"Du lädst {ben.name} in deine Gruppe ein."
    ]
    async with h.committed.session() as db:
        assert await db.get(GroupInvite, (anna.id, ben.id)) is not None
    h.publisher.relocations.clear()

    assert await act(h, ben.id, GroupAcceptAction(inviter_id=anna.id)) == [
        f"Du trittst der Gruppe von {anna.name} bei."
    ]
    leader, member = await load(h, anna.id), await load(h, ben.id)
    assert leader.group_id is not None and leader.group_id == member.group_id
    async with h.committed.session() as db:
        group = await db.get(Group, leader.group_id)
        assert group is not None and group.leader_id == anna.id
        assert await db.get(GroupInvite, (anna.id, ben.id)) is None
    # both live connections now also listen to the group channel
    assert {(r.player_id, r.group_id) for r in h.publisher.relocations} == {
        (anna.id, leader.group_id),
        (ben.id, leader.group_id),
    }


async def test_leaving_dissolves_and_reaches_members_elsewhere(harness: ServiceHarness) -> None:
    h = harness
    anna = await player(h, h.scratch_room("town_square"))
    ben = await player(h, h.scratch_room())
    group_id = await form_group(h, anna, ben)

    await act(h, anna.id, GroupLeaveAction())

    assert (await load(h, anna.id)).group_id is None
    assert (await load(h, ben.id)).group_id is None  # in another room, still updated
    async with h.committed.session() as db:
        assert await db.get(Group, group_id) is None
        texts = (
            await db.scalars(select(col(LogEntry.text)).where(col(LogEntry.player_id) == ben.id))
        ).all()
    assert f"{anna.name} verlässt die Gruppe. Die Gruppe ist aufgelöst." in texts


async def test_leader_leaves_and_kick(harness: ServiceHarness) -> None:
    h = harness
    room = h.scratch_room("town_square")
    anna, ben, cleo = await player(h, room), await player(h, room), await player(h, room)
    group_id = await form_group(h, anna, ben, cleo)

    await act(h, anna.id, GroupLeaveAction())
    async with h.committed.session() as db:
        group = await db.get(Group, group_id)
        assert group is not None and group.leader_id == ben.id  # longest serving member

    assert await act(h, ben.id, GroupKickAction(player_id=cleo.id)) == [
        f"Du entfernst {cleo.name} aus der Gruppe."
    ]
    assert (await load(h, cleo.id)).group_id is None
    async with h.committed.session() as db:
        assert await db.get(Group, group_id) is None  # Ben alone: dissolved


async def test_logout_leaves_the_group(harness: ServiceHarness) -> None:
    h = harness
    room = h.scratch_room("town_square")
    anna, ben = await player(h, room), await player(h, room)
    group_id = await form_group(h, anna, ben)

    await act(h, ben.id, LogoutAction())

    assert (await load(h, ben.id)).group_id is None
    assert (await load(h, anna.id)).group_id is None
    async with h.committed.session() as db:
        assert await db.get(Group, group_id) is None


async def test_ticker_removes_offline_members(harness: ServiceHarness, engine: AsyncEngine) -> None:
    h = harness
    room = h.scratch_room("town_square")
    anna, ben, cleo = await player(h, room), await player(h, room), await player(h, room)
    group_id = await form_group(h, anna, ben, cleo)
    async with h.committed.session() as db, db.begin():  # e.g. leaving after logout failed
        row = await db.get(PlayerRow, cleo.id)
        assert row is not None
        row.online = False

    ticker = WorldTicker(
        engine, h.service.sessions, h.content, h.service, h.publisher, clock=h.clock
    )
    report = await ticker.tick()

    assert report.offline_members_removed == 1
    assert (await load(h, cleo.id)).group_id is None
    assert (await load(h, anna.id)).group_id == group_id


async def test_concurrent_accepts_respect_the_group_size(harness: ServiceHarness) -> None:
    h = harness
    room = h.scratch_room("town_square")
    rules = h.content.rules
    object.__setattr__(h.content, "rules", rules.model_copy(update={"max_group_size": 3}))
    try:
        anna, ben = await player(h, room), await player(h, room)
        await form_group(h, anna, ben)  # one slot left
        cleo, dora = await player(h, room), await player(h, room)
        await act(h, anna.id, GroupInviteAction(player_name=cleo.name))
        await act(h, anna.id, GroupInviteAction(player_name=dora.name))

        h.clock.advance(seconds=1)
        results = await asyncio.gather(
            h.service.execute(cleo.id, GroupAcceptAction(inviter_id=anna.id)),
            h.service.execute(dora.id, GroupAcceptAction(inviter_id=anna.id)),
        )
        texts = sorted(e.text for r in results for e in r.events)
        assert texts[0] == "Die Gruppe ist voll."
        joined = [p for p in (cleo, dora) if (await load(h, p.id)).group_id is not None]
        assert len(joined) == 1
    finally:
        object.__setattr__(h.content, "rules", rules)  # shared, frozen registry
