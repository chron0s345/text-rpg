"""Shared fixtures for engine tests: real content plus deterministic test monsters/items."""

import dataclasses

import pytest

from app.content.loader import load_content
from app.content.registry import ContentRegistry
from app.content.schemas import (
    Ambush,
    CombatRules,
    ItemTemplate,
    LootEntry,
    MonsterTemplate,
    Regeneration,
    Rules,
    StatModifier,
)
from tests.conftest import DATA_DIR


@pytest.fixture(scope="module")
def content() -> ContentRegistry:
    real = load_content(DATA_DIR)
    dummy = MonsterTemplate(
        id="dummy",
        name="Übungspuppe",
        max_hp=10,
        attack=5,
        defense=0,
        xp_reward=30,
        gold_reward=(7, 7),
        respawn_seconds=60,
        loot_table=[LootEntry(item_id="goblin_ear", chance=1.0, min_qty=2, max_qty=2)],
    )
    troll = dummy.model_copy(
        update={
            "id": "troll",
            "name": "Troll",
            "attack": 100,
            "regen": Regeneration(delay_seconds=10, hp_per_second=1.0),
        }
    )
    hoard = dummy.model_copy(
        update={
            "id": "hoard",
            "gold_reward": (0, 0),
            "loot_table": [
                LootEntry(item_id="goblin_ear", chance=1.0, min_qty=2, max_qty=2),
                LootEntry(item_id="healing_potion", chance=1.0),
                LootEntry(item_id="bread", chance=1.0),
            ],
        }
    )
    ears = dummy.model_copy(
        update={
            "id": "ears",
            "gold_reward": (0, 0),
            "loot_table": [LootEntry(item_id="goblin_ear", chance=1.0, min_qty=30, max_qty=30)],
        }
    )
    ambusher = dummy.model_copy(
        update={
            "id": "ambusher",
            "name": "Lauerer",
            "ambush": Ambush(chance_on_enter=1.0, chance_per_tick=1.0, max_level_difference=2),
        }
    )
    amulet = ItemTemplate(
        id="amulet",
        name="Amulett",
        description="",
        kind="armor",
        slot="neck",
        effects=[StatModifier(stat="max_hp", amount=5)],
    )
    buckler = ItemTemplate(
        id="buckler",
        name="Faustschild",
        description="",
        kind="armor",
        slot="offhand",
        effects=[StatModifier(stat="defense", amount=2)],
    )
    ring = ItemTemplate(
        id="ring",
        name="Ring der Kraft",
        description="",
        kind="armor",
        slot="ring",
        effects=[StatModifier(stat="attack", amount=3), StatModifier(stat="max_hp", amount=4)],
    )
    knives = ItemTemplate(
        id="knives",
        name="Wurfmesser",
        description="",
        kind="weapon",
        slot="weapon",
        stackable=True,
        max_stack=10,
    )
    return dataclasses.replace(
        real,
        monsters={
            **real.monsters,
            "dummy": dummy,
            "troll": troll,
            "hoard": hoard,
            "ears": ears,
            "ambusher": ambusher,
        },
        items={
            **real.items,
            "amulet": amulet,
            "buckler": buckler,
            "ring": ring,
            "knives": knives,
        },
    )


@pytest.fixture
def rules(content: ContentRegistry) -> Rules:
    """Real rules, but deterministic damage (no variance)."""
    return content.rules.model_copy(
        update={"combat": CombatRules(min_damage=1, damage_variance=0, flee_chance=0.5)}
    )
