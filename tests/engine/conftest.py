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
    QuestTemplate,
    RareVariant,
    RecipeTemplate,
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
    plain = dummy.model_copy(
        update={
            "id": "plain",
            "name": "Feldmaus",
            "rare_variant": RareVariant(monster_id="golden", chance=0.25),
        }
    )
    golden = dummy.model_copy(
        update={"id": "golden", "name": "Goldene Feldmaus", "max_hp": 30, "xp_reward": 300}
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
    quests = {
        "tails": QuestTemplate.model_validate(
            {
                "id": "tails",
                "name": "Rattenplage",
                "giver_id": "alchemist",
                "description": "Bring mir drei Goblinohren.",
                "completion_text": "Danke!",
                "objective": {"type": "deliver", "item_id": "goblin_ear", "quantity": 3},
                "reward": {"xp": 40, "gold": 12, "items": [{"item_id": "healing_potion"}]},
            }
        ),
        "hunt": QuestTemplate.model_validate(
            {
                "id": "hunt",
                "name": "Puppenjagd",
                "giver_id": "alchemist",
                "description": "Zerschlage zwei Übungspuppen.",
                "completion_text": "Gut gemacht.",
                "objective": {"type": "kill", "monster_id": "plain", "count": 2},
                "reward": {"gold": 5},
                "requires": ["tails"],
                "repeatable": True,
            }
        ),
        "veteran": QuestTemplate.model_validate(
            {
                "id": "veteran",
                "name": "Nur für Erfahrene",
                "giver_id": "smith",
                "description": "...",
                "completion_text": "...",
                "objective": {"type": "deliver", "item_id": "bread", "quantity": 1},
                "min_level": 5,
            }
        ),
    }
    recipes = {
        "brew": RecipeTemplate.model_validate(
            {
                "id": "brew",
                "crafter_id": "alchemist",
                "item_id": "healing_potion",
                "quantity": 2,
                "ingredients": [{"item_id": "goblin_ear", "quantity": 2}, {"item_id": "bread"}],
                "gold": 5,
            }
        ),
        "blade": RecipeTemplate.model_validate(
            {
                "id": "blade",
                "crafter_id": "smith",
                "item_id": "short_sword",
                "ingredients": [{"item_id": "goblin_ear", "quantity": 3}],
                "min_level": 3,
            }
        ),
    }
    return dataclasses.replace(
        real,
        recipes={**real.recipes, **recipes},
        monsters={
            **real.monsters,
            "dummy": dummy,
            "troll": troll,
            "hoard": hoard,
            "ears": ears,
            "ambusher": ambusher,
            "plain": plain,
            "golden": golden,
        },
        quests={**real.quests, **quests},
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
