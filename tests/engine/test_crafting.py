"""Crafting in the engine: ingredients from the free backpack plus a fee become the result.

Test recipes (tests/engine/conftest.py): "brew" (Elira: 2 goblin ears + bread + 5 gold ->
2 healing potions), "blade" (Brandt: 3 goblin ears -> short sword, from level 3).
"""

import random

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.crafting import resolve_craft
from app.engine.state import ActionSnapshot, PlayerSnapshot
from tests.engine.factories import NOW, actor_texts, make_player, player_update
from tests.engine.test_inventory_actions import fill, give

RNG = random.Random(5)


def at(room: str, level: int = 1, gold: int = 0) -> PlayerSnapshot:
    actor = make_player(room=room, gold=gold)
    actor.level = level
    return actor


def count(actor: PlayerSnapshot, item_id: str) -> int:
    return sum(i.quantity for i in actor.inventory if i.item_id == item_id)


def test_crafting_takes_ingredients_and_fee(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room="town_market", gold=8)
    give(actor, "goblin_ear", 1)
    give(actor, "goblin_ear", 2)  # two stacks
    give(actor, "bread")

    result = resolve_craft(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "brew")

    assert actor_texts(result) == ["Alchemistin Elira stellt dir 2× Heiltrank für 5 Gold her."]
    assert (count(actor, "goblin_ear"), count(actor, "bread")) == (1, 0)
    assert count(actor, "healing_potion") == 2
    assert player_update(result, actor.id)["gold"] == 3


def test_crafting_needs_the_crafter_level_ingredients_and_gold(
    content: ContentRegistry, rules: Rules
) -> None:
    def craft(actor: PlayerSnapshot, recipe_id: str) -> list[str]:
        return actor_texts(
            resolve_craft(ActionSnapshot(actor=actor), content, rules, NOW, RNG, recipe_id)
        )

    assert craft(make_player(), "brew") == ["Alchemistin Elira ist nicht hier."]
    assert craft(make_player(room="town_market"), "nope") == ["Dieses Rezept gibt es nicht."]
    assert craft(at("town_smithy", level=2), "blade") == [
        "Kurzschwert kannst du erst ab Stufe 3 herstellen."
    ]

    poor = make_player(room="town_market", gold=4)
    give(poor, "goblin_ear", 1)
    assert craft(poor, "brew") == ["Für Heiltrank fehlt dir noch: Goblinohr, Frisches Brot."]
    give(poor, "goblin_ear", 1)
    give(poor, "bread")
    assert craft(poor, "brew") == ["Alchemistin Elira verlangt 5 Gold, du hast 4."]


def test_equipped_or_offered_ingredients_do_not_count(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = at("town_smithy", level=3)
    offered = give(actor, "goblin_ear", 3)
    actor.locked_item_ids = [offered.id]
    result = resolve_craft(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "blade")
    assert actor_texts(result) == ["Für Kurzschwert fehlt dir noch: 3× Goblinohr."]


def test_result_must_fit_or_nothing_is_taken(content: ContentRegistry, rules: Rules) -> None:
    actor = at("town_smithy", level=3)
    give(actor, "goblin_ear", 4)  # one ear stays: no slot frees up
    fill(actor, rules)
    result = resolve_craft(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "blade")
    assert actor_texts(result) == ["Für Kurzschwert ist kein Platz im Inventar."]
    assert result.changes.inventory_upsert == [] and result.changes.inventory_delete == []


def test_a_used_up_stack_frees_its_slot_for_the_result(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = at("town_smithy", level=3)
    give(actor, "goblin_ear", 3)
    fill(actor, rules)
    result = resolve_craft(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "blade")
    assert actor_texts(result) == ["Schmied Brandt stellt dir Kurzschwert her."]
    assert count(actor, "short_sword") == 1
