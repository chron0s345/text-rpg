"""Group skills: healing that reaches the group in the room, and taunt (the warrior takes the
monster's strikes at its group)."""

import dataclasses
import random
from typing import Any
from uuid import uuid4

import pytest

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.combat import resolve_attack
from app.engine.skills import resolve_use_skill
from app.engine.state import ActionSnapshot, PlayerSnapshot
from tests.engine.factories import NOW, actor_texts, fight, make_monster, make_player

RNG = random.Random(1)
GROUP = uuid4()


@pytest.fixture(scope="module")
def content(content: ContentRegistry) -> ContentRegistry:
    dummy = content.monsters["dummy"].model_copy(update={"max_hp": 100})
    return dataclasses.replace(content, monsters={**content.monsters, "dummy": dummy})


def member(name: str, class_id: str = "adventurer", hp: int = 20, **fields: Any) -> PlayerSnapshot:
    player = make_player(name, group_id=fields.pop("group_id", GROUP), hp=hp)
    player.class_id = class_id
    for key, value in fields.items():
        setattr(player, key, value)
    return player


def texts_for(result: Any, player: PlayerSnapshot) -> list[str]:
    return [
        e.text
        for e in result.events
        if e.audience.kind == "players" and player.id in e.audience.player_ids
    ]


def test_healing_reaches_the_group_in_the_room(content: ContentRegistry, rules: Rules) -> None:
    cleric = member("Anna", "cleric", hp=10)
    bert = member("Bert", hp=5)
    stranger = member("Carl", hp=5, group_id=None)
    fallen = member("Dora", hp=0, state="dead")
    snapshot = ActionSnapshot(actor=cleric, room_players=[bert, stranger, fallen])
    result = resolve_use_skill(snapshot, content, rules, NOW, RNG, "heal")

    assert actor_texts(result) == [
        "Du setzt Heilung ein und heilst dich um 7 HP.",  # 35 % of 20
        "Heilung heilt Bert um 7 HP.",
    ]
    assert texts_for(result, bert) == ["Anna heilt dich mit Heilung um 7 HP."]
    assert (cleric.hp, bert.hp, stranger.hp, fallen.hp) == (17, 12, 5, 0)


def test_group_healing_needs_someone_hurt(content: ContentRegistry, rules: Rules) -> None:
    cleric = member("Anna", "cleric")
    bert = member("Bert", hp=5)
    snapshot = ActionSnapshot(actor=cleric, room_players=[bert])
    result = resolve_use_skill(snapshot, content, rules, NOW, RNG, "heal")
    assert actor_texts(result) == ["Heilung heilt Bert um 7 HP."]  # the cleric is unhurt

    whole = ActionSnapshot(actor=member("Anna", "cleric"), room_players=[member("Bert")])
    result = resolve_use_skill(whole, content, rules, NOW, RNG, "heal")
    assert actor_texts(result) == ["Niemand ist verletzt."]


def test_holy_light_drains_for_the_group(content: ContentRegistry, rules: Rules) -> None:
    cleric = member("Anna", "cleric", hp=10, level=6)
    bert = member("Bert", hp=5)
    dummy = make_monster(hp=50)
    snapshot = ActionSnapshot(actor=cleric, room_players=[bert], monsters=[dummy])
    result = resolve_use_skill(snapshot, content, rules, NOW, RNG, "holy_light", dummy.id)
    assert texts_for(result, bert) == ["Anna heilt dich mit Heiliges Licht um 4 HP."]
    assert bert.hp == 9


def test_taunt_makes_the_warrior_take_the_strikes_at_its_group(
    content: ContentRegistry, rules: Rules
) -> None:
    warrior = member("Anna", "warrior", level=3)
    bert = member("Bert")
    dummy = make_monster(hp=50)
    fight(bert, dummy)
    taunted = resolve_use_skill(
        ActionSnapshot(actor=warrior, room_players=[bert], monsters=[dummy]),
        content,
        rules,
        NOW,
        RNG,
        "taunt",
        dummy.id,
    )
    assert (
        "Übungspuppe wendet sich gegen dich: Du fängst die nächsten 3 Schläge gegen deine "
        "Gruppe ab." in actor_texts(taunted)
    )
    assert warrior.guarding == 3
    warrior_hp = warrior.hp

    result = resolve_attack(
        ActionSnapshot(actor=bert, room_players=[warrior], monsters=[dummy]),
        content,
        rules,
        NOW,
        RNG,
        dummy.id,
    )
    assert "Anna fängt den Schlag von Übungspuppe ab." in actor_texts(result)
    assert texts_for(result, warrior)[:2] == [
        "Du fängst den Schlag von Übungspuppe für Bert ab.",
        "Übungspuppe trifft dich für 3 Schaden.",
    ]
    assert bert.hp == 20 and warrior.hp == warrior_hp - 3
    assert warrior.guarding == 2


@pytest.mark.parametrize("why", ["other_group", "other_monster", "used_up"])
def test_taunt_only_guards_its_group_in_the_same_fight(
    content: ContentRegistry, rules: Rules, why: str
) -> None:
    dummy, other = make_monster(hp=50), make_monster(hp=50)
    warrior = member("Anna", "warrior", level=3, guarding=0 if why == "used_up" else 2)
    fight(warrior, other if why == "other_monster" else dummy)
    bert = member("Bert", group_id=uuid4() if why == "other_group" else GROUP)
    fight(bert, dummy)
    resolve_attack(
        ActionSnapshot(actor=bert, room_players=[warrior], monsters=[dummy, other]),
        content,
        rules,
        NOW,
        RNG,
        dummy.id,
    )
    assert bert.hp == 17 and warrior.hp == 20  # Bert takes his own strike


def test_a_new_fight_ends_the_taunt(content: ContentRegistry, rules: Rules) -> None:
    warrior = member("Anna", "warrior", level=3, guarding=2)
    dummy = make_monster(hp=50)
    resolve_attack(
        ActionSnapshot(actor=warrior, monsters=[dummy]), content, rules, NOW, RNG, dummy.id
    )
    assert warrior.guarding == 0


def test_group_skill_rules() -> None:
    from pydantic import ValidationError

    from app.content.schemas import SkillTemplate

    with pytest.raises(ValidationError, match="group needs heal or drain"):
        SkillTemplate(
            id="x", name="X", description="", class_id="a", cooldown=1, damage=1, group=True
        )
    with pytest.raises(ValidationError, match="need damage"):
        SkillTemplate(id="x", name="X", description="", class_id="a", cooldown=1, heal=5, taunt=2)
