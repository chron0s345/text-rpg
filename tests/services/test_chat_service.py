"""Group chat and whispers against PostgreSQL: they reach members/recipients in other rooms,
end up in the logs, and whispers find their recipient by name among online characters."""

from __future__ import annotations

from sqlalchemy import select
from sqlmodel import col

from app.api.schemas import GroupSayAction, WhisperAction
from app.db.models import LogEntry
from tests.services.conftest import ServiceHarness
from tests.services.test_groups_service import act, form_group, player


async def chat_log(h: ServiceHarness, player_id: object) -> list[str]:
    async with h.committed.session() as db:
        rows = await db.scalars(
            select(LogEntry)
            .where(col(LogEntry.player_id) == player_id, col(LogEntry.kind).startswith("chat."))
            .order_by(col(LogEntry.id))
        )
        return [r.text for r in rows.all()]


async def test_group_say_reaches_members_in_other_rooms(harness: ServiceHarness) -> None:
    h = harness
    town, forest = h.scratch_room("town_square"), h.scratch_room()
    anna, ben = await player(h, town), await player(h, forest)
    bystander = await player(h, town)
    await form_group(h, anna, ben)

    assert await act(h, anna.id, GroupSayAction(text="Wo bist du?")) == ["[Gruppe] Du: Wo bist du?"]

    assert await chat_log(h, ben.id) == [f"[Gruppe] {anna.name}: Wo bist du?"]
    assert await chat_log(h, bystander.id) == []


async def test_whisper_finds_the_recipient_by_name_anywhere(harness: ServiceHarness) -> None:
    h = harness
    town, forest = h.scratch_room("town_square"), h.scratch_room()
    anna, ben = await player(h, town), await player(h, forest)
    bystander = await player(h, town)

    said = await act(h, anna.id, WhisperAction(player_name=ben.name.upper(), text="Geheim"))

    assert said == [f"Du flüsterst {ben.name} zu: Geheim"]
    assert await chat_log(h, ben.id) == [f"{anna.name} flüstert dir zu: Geheim"]
    assert await chat_log(h, bystander.id) == []
    [event] = [e for e in h.publisher.published if e.kind == "chat.whisper"]
    assert event.data["from_name"] == anna.name


async def test_whisper_to_an_offline_character_is_rejected(harness: ServiceHarness) -> None:
    h = harness
    town = h.scratch_room("town_square")
    anna, ben = await player(h, town), await player(h, town, online=False)

    assert await act(h, anna.id, WhisperAction(player_name=ben.name, text="Hallo?")) == [
        "Niemand mit diesem Namen ist gerade im Spiel."
    ]
    assert await chat_log(h, ben.id) == []
