"""Character classes: the level table's base stats, scaled by the class, at every level-up."""

import dataclasses
import random

import pytest

from app.content.registry import ContentRegistry
from app.content.schemas import ClassBonus, ClassTemplate, LevelRule, Rules
from app.engine.actions import resolve_inspect_player
from app.engine.classes import base_stats, bonus_text
from app.engine.combat import resolve_attack
from app.engine.progression import calculate_level_progression
from app.engine.state import ActionSnapshot
from tests.engine.factories import NOW, actor_texts, make_monster, make_player, player_update

RNG = random.Random(1)
RULE = LevelRule(level=10, xp_required=0, max_hp=118, attack=31, defense=14)
WARRIOR = ClassTemplate(
    id="warrior",
    name="Krieger",
    description="",
    bonus=ClassBonus(max_hp=10, attack=-10, defense=15),
)
MAGE = ClassTemplate(
    id="mage", name="Magier", description="", bonus=ClassBonus(max_hp=-15, attack=30, defense=-15)
)


def test_base_stats_scale_the_level_table_rounding_half_up() -> None:
    assert base_stats(RULE, None) == base_stats(
        RULE, ClassTemplate(id="a", name="A", description="")
    )
    warrior = base_stats(RULE, WARRIOR)
    assert (warrior.max_hp, warrior.attack, warrior.defense) == (130, 28, 16)  # 129.8, 27.9, 16.1
    mage = base_stats(RULE, MAGE)
    assert (mage.max_hp, mage.attack, mage.defense) == (100, 40, 12)  # 100.3, 40.3, 11.9
    tiny = LevelRule(level=1, xp_required=0, max_hp=1, attack=1, defense=1)
    assert (
        base_stats(
            tiny, ClassTemplate(id="x", name="X", description="", bonus=ClassBonus(attack=-50))
        ).attack
        == 1
    )  # never below 1


@pytest.mark.parametrize(
    ("player_class", "text"),
    [
        (WARRIOR, "+10 % Leben · −10 % Angriff · +15 % Verteidigung"),
        (ClassTemplate(id="a", name="A", description=""), "ausgeglichen"),
    ],
)
def test_bonus_text(player_class: ClassTemplate, text: str) -> None:
    assert bonus_text(player_class) == text


def test_a_level_up_uses_the_class(rules: Rules) -> None:
    rules = rules.model_copy(
        update={
            "levels": [
                LevelRule(level=1, xp_required=0, max_hp=20, attack=5, defense=2),
                LevelRule(level=2, xp_required=100, max_hp=30, attack=10, defense=4),
            ]
        }
    )
    result = calculate_level_progression(120, 1, 20, 5, 2, rules, MAGE)
    assert (result.new_base_max_hp, result.new_base_attack, result.new_base_defense) == (26, 13, 3)


def test_kill_xp_levels_up_with_the_class_stats(content: ContentRegistry, rules: Rules) -> None:
    local = dataclasses.replace(content, classes={"warrior": WARRIOR})
    actor = make_player(xp=rules.level_rule(2).xp_required - 1)
    actor.class_id = "warrior"
    monster = make_monster(hp=1)
    result = resolve_attack(
        ActionSnapshot(actor=actor, monsters=[monster]), local, rules, NOW, RNG, monster.id
    )
    expected = base_stats(rules.level_rule(2), WARRIOR)
    update = player_update(result, actor.id)
    assert update["level"] == 2
    assert (update["base_max_hp"], update["base_attack"], update["base_defense"]) == (
        expected.max_hp,
        expected.attack,
        expected.defense,
    )


def test_inspecting_names_the_class(content: ContentRegistry, rules: Rules) -> None:
    local = dataclasses.replace(content, classes={"mage": MAGE})
    actor, other = make_player("Anna"), make_player("Bert")
    other.class_id = "mage"
    snapshot = ActionSnapshot(actor=actor, room_players=[other])
    result = resolve_inspect_player(snapshot, local, rules, NOW, RNG, other.id)
    assert actor_texts(result)[0].splitlines()[0] == "Bert – Stufe 1 Magier."
