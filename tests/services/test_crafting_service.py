"""Crafting against PostgreSQL: ingredients and fee are taken, the result is written."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.api.schemas import CraftAction
from app.content.schemas import MerchantTemplate, RecipeTemplate
from app.db.models import InventoryItem
from tests.services.conftest import ServiceHarness
from tests.services.test_groups_service import act, load, player


@pytest.fixture
def workshop(harness: ServiceHarness) -> Iterator[str]:
    """A scratch room with a crafter and one recipe."""
    h = harness
    room = h.scratch_room()
    h.content.merchants["qcrafter"] = MerchantTemplate(
        id="qcrafter", name="Tüftlerin", room_id=room, greeting="", sells=[]
    )
    h.content.recipes["q_potion"] = RecipeTemplate.model_validate(
        {
            "id": "q_potion",
            "crafter_id": "qcrafter",
            "item_id": "healing_potion",
            "quantity": 2,
            "ingredients": [{"item_id": "goblin_ear", "quantity": 2}],
            "gold": 4,
        }
    )
    try:
        yield room
    finally:
        del h.content.merchants["qcrafter"]
        del h.content.recipes["q_potion"]


async def test_crafting_writes_the_result(harness: ServiceHarness, workshop: str) -> None:
    h = harness
    anna = await player(h, workshop, gold=10)
    async with h.committed.session() as db, db.begin():
        db.add(InventoryItem(player_id=anna.id, item_id="goblin_ear", quantity=3))

    texts = await act(h, anna.id, CraftAction(recipe_id="q_potion"))

    assert texts[0] == "Tüftlerin stellt dir 2× Heiltrank für 4 Gold her."
    assert (await load(h, anna.id)).gold == 6
    async with h.committed.session() as db:
        items = {
            i.item_id: i.quantity
            for i in (
                await db.scalars(
                    select(InventoryItem).where(col(InventoryItem.player_id) == anna.id)
                )
            ).all()
        }
    assert items == {"goblin_ear": 1, "healing_potion": 2}
