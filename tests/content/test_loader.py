from pathlib import Path

import pytest

from app.content.loader import ContentValidationError, load_content, validate_content
from app.content.schemas import (
    ItemTemplate,
    MerchantOffer,
    MerchantTemplate,
    MonsterTemplate,
    RoomTemplate,
    Rules,
    SpawnRule,
)


def test_load_real_content_data() -> None:
    data_dir = Path("data")
    registry = load_content(data_dir)
    assert "town_square" in registry.rooms
    assert "town_bank" in registry.rooms
    assert registry.rooms["town_bank"].bank is True
    assert "goblin" in registry.monsters
    assert "healing_potion" in registry.items
    assert registry.items["healing_potion"].icon == "potion"
    assert registry.items["rusty_dagger"].icon == "dagger"
    assert "alchemist" in registry.merchants
    assert registry.rules.start_room_id == "town_square"


def test_missing_bank_room() -> None:
    rooms = {
        "town_square": RoomTemplate(
            id="town_square",
            name="Town",
            description="",
            safe=True,
            checkpoint=True,
            bank=False,
        )
    }
    with pytest.raises(ContentValidationError, match="No room with bank: true"):
        validate_content(
            rooms, {}, {}, {}, Rules(start_room_id="town_square", town_room_id="town_square")
        )


def test_safe_room_with_spawns() -> None:
    rooms = {
        "town_square": RoomTemplate(
            id="town_square",
            name="Town",
            description="",
            safe=True,
            checkpoint=True,
            bank=True,
            spawns=[SpawnRule(monster_id="goblin", count=1)],
        )
    }
    monsters = {
        "goblin": MonsterTemplate(
            id="goblin",
            name="Goblin",
            max_hp=10,
            attack=2,
            defense=1,
            xp_reward=10,
            respawn_seconds=30,
        )
    }
    with pytest.raises(ContentValidationError, match="cannot have monster spawns"):
        validate_content(
            rooms, monsters, {}, {}, Rules(start_room_id="town_square", town_room_id="town_square")
        )


def test_infinite_money_loop_detection() -> None:
    rooms = {
        "town_square": RoomTemplate(
            id="town_square",
            name="Town",
            description="",
            safe=True,
            checkpoint=True,
            bank=True,
        )
    }
    items = {
        "gold_bar": ItemTemplate(
            id="gold_bar",
            name="Gold Bar",
            description="",
            kind="misc",
            value=100,
            sellable=True,
        )
    }
    # Merchant sells for 10g, value is 100g with sell_ratio 0.25 -> sell_revenue = 25g (exploit)
    merchants = {
        "cheap_merchant": MerchantTemplate(
            id="cheap_merchant",
            name="Cheap",
            room_id="town_square",
            greeting="",
            sells=[MerchantOffer(item_id="gold_bar", price=10)],
            buys="all",
            sell_ratio=0.25,
        )
    }
    rules = Rules(start_room_id="town_square", town_room_id="town_square")
    with pytest.raises(ContentValidationError, match="Infinite money loop detected"):
        validate_content(rooms, {}, items, merchants, rules)
