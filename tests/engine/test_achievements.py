"""Achievements: earned once from level, quests, kills and worn sets; titles to show."""

import dataclasses
import random
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from app.content.loader import ContentValidationError, load_content
from app.content.registry import ContentRegistry
from app.content.schemas import AchievementTemplate, Rules
from app.engine.achievements import grant, resolve_set_title, title_of
from app.engine.combat import resolve_attack
from app.engine.events import ResultBuilder
from app.engine.inventory_actions import resolve_equip
from app.engine.presence import resolve_login
from app.engine.quests import resolve_quest_turn_in
from app.engine.state import ActionSnapshot, InventoryItemState, QuestState
from tests.conftest import DATA_DIR
from tests.engine.factories import NOW, actor_texts, make_monster, make_player, player_update

RNG = random.Random(1)


def achievement(
    id: str, condition: dict[str, object], title: str | None = None
) -> AchievementTemplate:
    return AchievementTemplate.model_validate(
        {
            "id": id,
            "name": id.capitalize(),
            "description": "…",
            "condition": condition,
            "title": title,
        }
    )


@pytest.fixture(scope="module")
def world(content: ContentRegistry) -> ContentRegistry:
    """Only the achievements of these tests, so the real ones stay out of the way."""
    return dataclasses.replace(
        content,
        achievements={
            a.id: a
            for a in [
                achievement("veteran", {"type": "level", "level": 2}),
                achievement("helper", {"type": "quest", "quest_id": "tails"}, "die Hilfreiche"),
                achievement("busy", {"type": "quests", "count": 2}),
                achievement("dummy_slayer", {"type": "kill", "monster_id": "plain"}, "Mäusefänger"),
                achievement("lucky", {"type": "rare"}),
                achievement("dressed", {"type": "set", "set_id": "leather"}),
            ]
        },
    )


def earned(result_texts: list[str]) -> list[str]:
    return [t for t in result_texts if t.startswith("Erfolg errungen")]


def test_kill_credit_counts_the_monster_and_its_rare_variant(
    world: ContentRegistry, rules: Rules
) -> None:
    actor = make_player()
    monster = make_monster("golden", hp=1)  # the rare variant of "plain"
    result = resolve_attack(
        ActionSnapshot(actor=actor, monsters=[monster]), world, rules, NOW, RNG, monster.id
    )
    assert earned(actor_texts(result)) == [
        "Erfolg errungen: Veteran – …",  # the golden mouse's XP make level 2
        "Erfolg errungen: Dummy_slayer – … Neuer Titel: „Mäusefänger“.",
        "Erfolg errungen: Lucky – …",
    ]
    assert player_update(result, actor.id)["achievements"] == ["veteran", "dummy_slayer", "lucky"]


def test_a_level_up_from_the_kill_counts_and_nothing_is_earned_twice(
    world: ContentRegistry, rules: Rules
) -> None:
    actor = make_player(xp=rules.level_rule(2).xp_required - 1)
    actor.achievements = ["dummy_slayer"]
    monster = make_monster("plain", hp=1)
    result = resolve_attack(
        ActionSnapshot(actor=actor, monsters=[monster]), world, rules, NOW, RNG, monster.id
    )
    assert earned(actor_texts(result)) == ["Erfolg errungen: Veteran – …"]
    assert actor.achievements == ["dummy_slayer", "veteran"]


def test_quest_turn_in_counts_single_quests_and_how_many(
    world: ContentRegistry, rules: Rules
) -> None:
    actor = make_player(room="town_market")
    actor.inventory = [InventoryItemState(id=uuid4(), item_id="goblin_ear", quantity=3)]
    actor.quests = [
        QuestState(quest_id="tails", status="active"),
        QuestState(quest_id="errand", status="completed", step=0, times_completed=1),
    ]
    result = resolve_quest_turn_in(ActionSnapshot(actor=actor), world, rules, NOW, RNG, "tails")
    assert earned(actor_texts(result)) == [
        "Erfolg errungen: Helper – … Neuer Titel: „die Hilfreiche“.",
        "Erfolg errungen: Busy – …",
    ]


def test_completing_a_set_by_equipping(world: ContentRegistry, rules: Rules) -> None:
    pieces = ["leather_cap", "leather_armor", "leather_leggings"]
    actor = make_player()
    actor.inventory = [
        InventoryItemState(id=uuid4(), item_id=i, quantity=1, equipped_slot=world.items[i].slot)
        for i in pieces
    ]
    boots = InventoryItemState(id=uuid4(), item_id="leather_boots", quantity=1)
    actor.inventory.append(boots)
    result = resolve_equip(ActionSnapshot(actor=actor), world, rules, NOW, RNG, boots.id)
    assert earned(actor_texts(result)) == ["Erfolg errungen: Dressed – …"]


def test_login_catches_up_on_new_achievements(world: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    actor.level = 3
    result = resolve_login(
        ActionSnapshot(actor=actor), world, rules, NOW, RNG, already_online=False
    )
    assert earned(actor_texts(result)) == ["Erfolg errungen: Veteran – …"]
    again = resolve_login(ActionSnapshot(actor=actor), world, rules, NOW, RNG, already_online=False)
    assert earned(actor_texts(again)) == []


def test_achievements_removed_from_the_content_are_kept_but_ignored(
    world: ContentRegistry,
) -> None:
    actor = make_player()
    actor.achievements = ["gone"]
    actor.title = "gone"
    builder = ResultBuilder(actor_id=actor.id)
    grant(builder, actor, world)
    assert actor.achievements == ["gone"]
    assert title_of(actor, world) is None


def test_choosing_and_dropping_a_title(world: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    actor.achievements = ["helper", "veteran"]

    def choose(achievement_id: str | None) -> list[str]:
        result = resolve_set_title(
            ActionSnapshot(actor=actor), world, rules, NOW, RNG, achievement_id
        )
        return actor_texts(result)

    assert choose("dummy_slayer") == ["Diesen Erfolg hast du nicht errungen."]
    assert choose("veteran") == ["Dieser Erfolg bringt keinen Titel."]
    assert choose(None) == ["Du führst keinen Titel."]
    assert choose("helper") == ["Du führst jetzt den Titel „die Hilfreiche“."]
    assert actor.title == "helper" and title_of(actor, world) == "die Hilfreiche"
    assert choose("helper") == ["Diesen Titel führst du bereits."]
    actor.state = "dead"  # titles can be changed in any state
    assert choose(None) == ["Du legst deinen Titel ab."]
    assert actor.title is None


def test_content_validation_rejects_unknown_references(tmp_path: Path) -> None:
    for name in DATA_DIR.iterdir():
        if name.suffix == ".yaml":
            (tmp_path / name.name).write_text(name.read_text(encoding="utf-8"), encoding="utf-8")
    bad = [
        {
            "id": "x",
            "name": "X",
            "description": "…",
            "condition": {"type": "kill", "monster_id": "nobody"},
        }
    ]
    (tmp_path / "achievements.yaml").write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ContentValidationError, match="unknown monster 'nobody'"):
        load_content(tmp_path)


def test_real_achievements_load(content: ContentRegistry) -> None:
    real = load_content(DATA_DIR)
    assert len(real.achievements) >= 15
    assert all(a.title is None or a.title.strip() for a in real.achievements.values())
