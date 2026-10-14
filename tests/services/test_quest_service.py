"""Quests against PostgreSQL: rows are written, deliveries take items and pay, kills count for
every party member in the room (and their quest rows are locked with them)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.api.schemas import AttackAction, QuestAcceptAction, QuestTurnInAction
from app.content.schemas import MerchantTemplate, QuestTemplate
from app.db.models import InventoryItem, MonsterInstance, QuestProgress
from app.db.models import Player as PlayerRow
from tests.services.conftest import ServiceHarness
from tests.services.test_groups_service import act, form_group, load, player


@pytest.fixture
def giver_room(harness: ServiceHarness) -> Iterator[str]:
    """A scratch forest room with a quest giver and two test quests."""
    h = harness
    room = h.scratch_room()
    h.content.merchants["qgiver"] = MerchantTemplate(
        id="qgiver", name="Auftraggeberin", room_id=room, greeting="", sells=[]
    )
    base = {"giver_id": "qgiver", "description": "Tu was.", "completion_text": "Danke."}
    h.content.quests["q_deliver"] = QuestTemplate.model_validate(
        {
            **base,
            "id": "q_deliver",
            "name": "Ohrensammler",
            "objective": {"type": "deliver", "item_id": "goblin_ear", "quantity": 2},
            "reward": {"gold": 10, "items": [{"item_id": "healing_potion"}]},
        }
    )
    h.content.quests["q_kill"] = QuestTemplate.model_validate(
        {
            **base,
            "id": "q_kill",
            "name": "Goblinjagd",
            "objective": {"type": "kill", "monster_id": "goblin", "count": 2},
        }
    )
    h.content.quests["q_steps"] = QuestTemplate.model_validate(
        {
            **base,
            "id": "q_steps",
            "name": "Zwei Wege",
            "steps": [
                {"type": "visit", "room_id": room},
                {"type": "visit", "room_id": h.content.rules.town_room_id},
            ],
        }
    )
    try:
        yield room
    finally:
        del h.content.merchants["qgiver"]
        del h.content.quests["q_deliver"]
        del h.content.quests["q_kill"]
        del h.content.quests["q_steps"]


async def quest_row(h: ServiceHarness, player_id: Any, quest_id: str) -> QuestProgress | None:
    async with h.committed.session() as db:
        return await db.get(QuestProgress, (player_id, quest_id))


async def test_accept_and_deliver(harness: ServiceHarness, giver_room: str) -> None:
    h = harness
    anna = await player(h, giver_room, gold=3)
    async with h.committed.session() as db, db.begin():
        db.add(InventoryItem(player_id=anna.id, item_id="goblin_ear", quantity=3))

    assert (await act(h, anna.id, QuestAcceptAction(quest_id="q_deliver")))[0].startswith(
        "Auftrag angenommen: Ohrensammler."
    )
    row = await quest_row(h, anna.id, "q_deliver")
    assert row is not None and (row.status, row.progress) == ("active", 0)

    texts = await act(h, anna.id, QuestTurnInAction(quest_id="q_deliver"))

    assert texts[:2] == [
        "Auftrag erledigt: Ohrensammler. Auftraggeberin: „Danke.“",
        "Belohnung: 10 Gold, Heiltrank.",
    ]
    row = await quest_row(h, anna.id, "q_deliver")
    assert row is not None and (row.status, row.times_completed) == ("completed", 1)
    assert (await load(h, anna.id)).gold == 13
    async with h.committed.session() as db:
        items = {
            i.item_id: i.quantity
            for i in (
                await db.scalars(
                    select(InventoryItem).where(col(InventoryItem.player_id) == anna.id)
                )
            ).all()
        }
    assert items == {"goblin_ear": 1, "healing_potion": 1}


async def test_kills_count_for_every_party_member_in_the_room(
    harness: ServiceHarness, giver_room: str
) -> None:
    h = harness
    anna, ben = await player(h, giver_room), await player(h, giver_room)
    await form_group(h, anna, ben)
    for member in (anna, ben):
        await act(h, member.id, QuestAcceptAction(quest_id="q_kill"))
    async with h.committed.session() as db, db.begin():
        goblin = MonsterInstance(monster_id="goblin", room_id=giver_room, hp=1)
        db.add(goblin)

    texts = await act(h, anna.id, AttackAction(target_id=goblin.id))

    assert "Goblinjagd: 1/2" in texts
    for member in (anna, ben):
        row = await quest_row(h, member.id, "q_kill")
        assert row is not None and row.progress == 1, member.name
    told_ben = [e for e in h.publisher.published if e.kind == "quest.progress"]
    assert [e.audience.model_dump() for e in told_ben] == [
        {"kind": "players", "player_ids": [ben.id]}
    ]


async def test_quest_rows_disappear_with_the_character(
    harness: ServiceHarness, giver_room: str
) -> None:
    h = harness
    anna = await player(h, giver_room)
    await act(h, anna.id, QuestAcceptAction(quest_id="q_kill"))
    async with h.committed.session() as db, db.begin():
        row = await db.get(PlayerRow, anna.id)
        assert row is not None
        await db.delete(row)
    assert await quest_row(h, anna.id, "q_kill") is None


async def test_the_current_step_is_stored(harness: ServiceHarness, giver_room: str) -> None:
    h = harness
    anna = await player(h, giver_room)
    texts = await act(h, anna.id, QuestAcceptAction(quest_id="q_steps"))
    assert texts[-1].startswith("Zwei Wege: ")  # the first visit is done at once: we stand here
    row = await quest_row(h, anna.id, "q_steps")
    assert row is not None and (row.status, row.step) == ("active", 1)


async def test_login_hint_disappears_once_the_intro_is_accepted(harness: ServiceHarness) -> None:
    h = harness
    newcomer = await player(h, h.scratch_room())
    intro = h.content.rules.intro_quest_id
    assert intro is not None

    first = [e.text for e in (await h.service.login(newcomer.id)).events]
    assert any(text.startswith("Neu hier?") for text in first)

    async with h.committed.session() as db, db.begin():
        db.add(QuestProgress(player_id=newcomer.id, quest_id=intro, status="active"))
    h.clock.advance(seconds=1)
    again = [e.text for e in (await h.service.login(newcomer.id)).events]
    assert not any(text.startswith("Neu hier?") for text in again)
