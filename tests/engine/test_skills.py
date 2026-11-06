"""Skills (data/skills.yaml): stronger strikes, stuns, piercing, double hits, healing and drain,
with cooldowns counted in combat rounds and reset by every new fight."""

import dataclasses
import random
from typing import Any

import pytest

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.combat import resolve_attack
from app.engine.skills import resolve_use_skill
from app.engine.state import ActionSnapshot, MonsterSnapshot, PlayerSnapshot
from tests.engine.factories import (
    NOW,
    actor_texts,
    fight,
    make_monster,
    make_player,
    player_update,
)

RNG = random.Random(1)


@pytest.fixture(scope="module")
def content(content: ContentRegistry) -> ContentRegistry:
    """The training dummy with 100 max HP, so a few hits do not finish it."""
    dummy = content.monsters["dummy"].model_copy(update={"max_hp": 100})
    return dataclasses.replace(content, monsters={**content.monsters, "dummy": dummy})


def hero(class_id: str, level: int = 1, hp: int = 20) -> PlayerSnapshot:
    """Attack 5, defense 2, 20 max HP (the factory's base stats, whatever the class)."""
    player = make_player(hp=hp)
    player.class_id = class_id
    player.level = level
    return player


def use(
    content: ContentRegistry,
    rules: Rules,
    actor: PlayerSnapshot,
    skill_id: str,
    *monsters: MonsterSnapshot,
    target: MonsterSnapshot | None = None,
) -> Any:
    snapshot = ActionSnapshot(actor=actor, monsters=list(monsters))
    return resolve_use_skill(
        snapshot, content, rules, NOW, RNG, skill_id, target.id if target else None
    )


def monster_hp(result: Any, monster: MonsterSnapshot) -> int:
    [update] = [u for u in result.changes.monsters if u.monster_instance_id == monster.id]
    return int(update.model_dump(exclude_unset=True)["hp"])


def test_fireball_hits_twice_as_hard_and_starts_its_cooldown(
    content: ContentRegistry, rules: Rules
) -> None:
    mage = hero("mage")
    dummy = make_monster(hp=30)  # defense 0: a plain hit does 5
    result = use(content, rules, mage, "fireball", dummy, target=dummy)
    assert actor_texts(result)[:2] == [
        "Du setzt Feuerball ein und triffst Übungspuppe für 10 Schaden.",
        "Übungspuppe trifft dich für 3 Schaden.",  # it strikes back
    ]
    assert monster_hp(result, dummy) == 20
    assert player_update(result, mage.id)["skill_cooldowns"] == {"fireball": 3}
    assert mage.state == "in_combat"  # a skill opens a fight like an attack

    again = use(content, rules, mage, "fireball", dummy)  # the current opponent by default
    assert actor_texts(again) == ["Feuerball ist erst in 3 Runden wieder bereit."]


def test_cooldowns_tick_per_round_and_a_new_fight_resets_them(
    content: ContentRegistry, rules: Rules
) -> None:
    mage = hero("mage")
    dummy = make_monster(hp=100)
    fight(mage, dummy)
    mage.skill_cooldowns = {"fireball": 2}
    snapshot = ActionSnapshot(actor=mage, monsters=[dummy])
    resolve_attack(snapshot, content, rules, NOW, RNG, dummy.id)
    assert mage.skill_cooldowns == {"fireball": 1}
    resolve_attack(snapshot, content, rules, NOW, RNG, dummy.id)
    assert mage.skill_cooldowns == {}

    calm = hero("mage")
    calm.skill_cooldowns = {"fireball": 3}  # left over from the last fight
    fresh = make_monster(hp=100)
    resolve_attack(ActionSnapshot(actor=calm, monsters=[fresh]), content, rules, NOW, RNG, fresh.id)
    assert calm.skill_cooldowns == {}


def test_after_a_fight_offensive_skills_open_the_next_one_ready(
    content: ContentRegistry, rules: Rules
) -> None:
    mage = hero("mage")
    mage.skill_cooldowns = {"fireball": 2}  # left over from the last fight
    dummy = make_monster(hp=50)
    result = use(content, rules, mage, "fireball", dummy, target=dummy)
    assert actor_texts(result)[0].startswith("Du setzt Feuerball ein")
    assert mage.skill_cooldowns == {"fireball": 3}

    cleric = hero("cleric", hp=5)
    cleric.skill_cooldowns = {"heal": 2}  # healing waits for the next fight to start
    assert actor_texts(use(content, rules, cleric, "heal")) == [
        "Heilung ist erst in 2 Runden wieder bereit."
    ]


def test_shield_bash_stuns(content: ContentRegistry, rules: Rules) -> None:
    warrior = hero("warrior")
    dummy = make_monster(hp=30)
    result = use(content, rules, warrior, "shield_bash", dummy, target=dummy)
    assert actor_texts(result) == [
        "Du setzt Schildschlag ein und triffst Übungspuppe für 5 Schaden.",
        "Übungspuppe taumelt und schlägt in dieser Runde nicht zurück.",
    ]
    assert "hp" not in player_update(result, warrior.id)


def test_aimed_shot_ignores_armor(content: ContentRegistry, rules: Rules) -> None:
    armored = content.monsters["dummy"].model_copy(update={"defense": 4})
    local = dataclasses.replace(content, monsters={**content.monsters, "dummy": armored})
    ranger = hero("ranger")
    dummy = make_monster(hp=30)
    result = use(local, rules, ranger, "aimed_shot", dummy, target=dummy)
    # 5 attack against 0 defense, times 1.5 = 7.5 -> 8 (a plain hit would do 1)
    assert actor_texts(result)[0].endswith("für 8 Schaden.")


def test_double_shot_needs_level_six_and_hits_twice(content: ContentRegistry, rules: Rules) -> None:
    dummy = make_monster(hp=30)
    early = use(content, rules, hero("ranger"), "double_shot", dummy, target=dummy)
    assert actor_texts(early) == ["Doppelschuss lernst du erst auf Stufe 6."]
    result = use(content, rules, hero("ranger", level=6), "double_shot", dummy, target=dummy)
    hits = [t for t in actor_texts(result) if t.startswith("Du setzt Doppelschuss ein")]
    assert len(hits) == 2
    assert monster_hp(result, dummy) == 20


def test_a_killing_skill_ends_the_fight_without_a_counter(
    content: ContentRegistry, rules: Rules
) -> None:
    mage = hero("mage")
    dummy = make_monster(hp=8)
    result = use(content, rules, mage, "fireball", dummy, target=dummy)
    texts = actor_texts(result)
    assert "Du hast Übungspuppe besiegt." in texts
    assert not any("trifft dich" in t for t in texts)


def test_healing_works_outside_a_fight(content: ContentRegistry, rules: Rules) -> None:
    cleric = hero("cleric", hp=10)
    result = use(content, rules, cleric, "heal")
    assert actor_texts(result) == ["Du setzt Heilung ein und heilst dich um 8 HP."]  # 40 % of 20
    assert player_update(result, cleric.id)["hp"] == 18
    assert cleric.skill_cooldowns == {"heal": 4}
    whole = hero("cleric")
    assert actor_texts(use(content, rules, whole, "heal")) == ["Du bist unverletzt."]


def test_healing_in_a_fight_costs_the_round(content: ContentRegistry, rules: Rules) -> None:
    cleric = hero("cleric", hp=10)
    dummy = make_monster(hp=30)
    fight(cleric, dummy)
    result = use(content, rules, cleric, "heal", dummy)
    assert actor_texts(result) == [
        "Du setzt Heilung ein und heilst dich um 8 HP.",
        "Übungspuppe trifft dich für 3 Schaden.",
    ]
    assert player_update(result, cleric.id)["hp"] == 15


def test_holy_light_drains(content: ContentRegistry, rules: Rules) -> None:
    cleric = hero("cleric", level=6, hp=10)
    dummy = make_monster(hp=30)
    result = use(content, rules, cleric, "holy_light", dummy, target=dummy)
    texts = actor_texts(result)
    # 5 * 1.3 = 6.5 -> 7 damage, half of it (3.5 -> 4) heals
    assert texts[:2] == [
        "Du setzt Heiliges Licht ein und triffst Übungspuppe für 7 Schaden.",
        "Heiliges Licht heilt dich um 4 HP.",
    ]


@pytest.mark.parametrize(
    ("actor", "skill_id", "room", "message"),
    [
        (hero("mage"), "shield_bash", None, "Diese Fähigkeit beherrschst du nicht."),
        (hero("mage"), "nope", None, "Diese Fähigkeit beherrschst du nicht."),
        (hero("mage"), "fireball", None, "Gegen wen willst du Feuerball einsetzen?"),
        (hero("mage"), "fireball", "town_square", "Hier wird nicht gekämpft."),
    ],
)
def test_rejections(
    content: ContentRegistry,
    rules: Rules,
    actor: PlayerSnapshot,
    skill_id: str,
    room: str | None,
    message: str,
) -> None:
    dummy = make_monster(hp=30)
    target = None
    if room is not None:
        actor.current_room_id = room
        dummy = make_monster(hp=30, room_id=room)
        target = dummy
    assert actor_texts(use(content, rules, actor, skill_id, dummy, target=target)) == [message]


def test_every_class_has_skills_and_they_load(content: ContentRegistry) -> None:
    for class_id in content.classes:
        skills = content.skills_of(class_id)
        assert skills and skills[0].level == 1, class_id


def test_skills_must_do_something() -> None:
    from pydantic import ValidationError

    from app.content.schemas import SkillTemplate

    with pytest.raises(ValidationError, match="neither hits nor heals"):
        SkillTemplate(id="x", name="X", description="", class_id="mage", cooldown=1)
    with pytest.raises(ValidationError, match="need damage"):
        SkillTemplate(
            id="x", name="X", description="", class_id="mage", cooldown=1, heal=5, stun=True
        )
