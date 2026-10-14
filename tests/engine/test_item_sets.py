"""Item sets: bonuses from the pieces worn, added up per threshold, shown on inspect."""

from __future__ import annotations

import random
from uuid import uuid4

import pytest

from app.content.loader import ContentValidationError, attach_sets, validate_sets
from app.content.registry import ContentRegistry
from app.content.schemas import ItemSet, Rules
from app.engine.actions import resolve_inspect
from app.engine.state import ActionSnapshot, InventoryItemState
from app.engine.stats import calculate_effective_stats, worn_sets
from tests.engine.factories import NOW, actor_texts, make_player

TEST_SET = ItemSet.model_validate(
    {
        "id": "test_set",
        "name": "Prüfset",
        "items": ["leather_cap", "leather_armor", "leather_boots"],
        "bonuses": [
            {"pieces": 2, "effects": [{"stat": "max_hp", "amount": 5}]},
            {"pieces": 3, "effects": [{"stat": "defense", "amount": 2}]},
        ],
    }
)


def worn(content: ContentRegistry, *item_ids: str, carried: str = "") -> list[InventoryItemState]:
    items = [
        InventoryItemState(id=uuid4(), item_id=i, quantity=1, equipped_slot=content.items[i].slot)
        for i in item_ids
    ]
    if carried:
        items.append(InventoryItemState(id=uuid4(), item_id=carried, quantity=1))
    return items


@pytest.fixture
def items(content: ContentRegistry) -> dict:  # type: ignore[type-arg]
    plain = {k: v.model_copy(update={"item_set": None}) for k, v in content.items.items()}
    return attach_sets({"test_set": TEST_SET}, plain)


def test_bonuses_add_up_with_the_pieces_worn(content: ContentRegistry, items: dict) -> None:  # type: ignore[type-arg]
    def stats(inventory: list[InventoryItemState]) -> tuple[int, int]:
        s = calculate_effective_stats(20, 3, 1, inventory, items)
        return s.max_hp, s.defense

    cap, armor, boots = 1, 2, 1  # leather: +1, +2, +1 defense
    assert stats(worn(content, "leather_cap")) == (20, 1 + cap)
    assert stats(worn(content, "leather_cap", "leather_armor")) == (25, 1 + cap + armor)
    full = worn(content, "leather_cap", "leather_armor", "leather_boots")
    assert stats(full) == (25, 1 + cap + armor + boots + 2)
    # carried pieces do not count
    assert stats(worn(content, "leather_cap", carried="leather_armor")) == (20, 1 + cap)
    assert [(s.id, n) for s, n in worn_sets(full, items)] == [("test_set", 3)]


def test_the_real_sets_reach_the_pieces(content: ContentRegistry) -> None:
    obsidian = content.items["obsidian_helm"].item_set
    assert obsidian is not None and obsidian.name == "Obsidian"
    assert all(content.items[i].item_set == obsidian for i in obsidian.items)


def test_inspect_lists_the_set(content: ContentRegistry, rules: Rules) -> None:
    result = resolve_inspect(
        ActionSnapshot(actor=make_player()), content, rules, NOW, random.Random(1), "valen_ring"
    )
    [text] = actor_texts(result)
    assert text.endswith(
        "(Ring · +2 Angriff, +5 max. HP · Set „Erbe der Valen“) Set-Teile: Valens Klinge,"
        " Ring des Grabfürsten. Boni: 2 Teile: +1 Angriff, +5 max. HP."
    )


def test_sets_are_validated(content: ContentRegistry) -> None:
    def check(**fields: object) -> None:
        data = {**TEST_SET.model_dump(), **fields}
        validate_sets({"s": ItemSet.model_validate(data)}, content.items)

    check()
    with pytest.raises(ContentValidationError, match="unknown item"):
        check(items=["leather_cap", "nope"])
    with pytest.raises(ContentValidationError, match="cannot be equipped"):
        check(items=["leather_cap", "goblin_ear"])
    with pytest.raises(ContentValidationError, match="two pieces for the slot"):
        check(items=["leather_cap", "iron_helm"])
    with pytest.raises(ContentValidationError, match="same piece count"):
        check(bonuses=[TEST_SET.bonuses[0], TEST_SET.bonuses[0]])
    with pytest.raises(ContentValidationError, match="more pieces than it has"):
        check(items=["leather_cap", "leather_armor"])
    second = TEST_SET.model_copy(update={"id": "other"})
    with pytest.raises(ContentValidationError, match="already belongs"):
        validate_sets({"a": TEST_SET, "b": second}, content.items)
