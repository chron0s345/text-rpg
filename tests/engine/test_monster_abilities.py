"""Monster abilities: crushing blows hit harder, life drain heals the monster; both count in the
fight estimate of "inspect monster" (and of the balance test)."""

import dataclasses
import random
from typing import Any

import pytest
from pydantic import ValidationError

from app.content.registry import ContentRegistry
from app.content.schemas import MonsterTemplate, Rules
from app.engine.actions import resolve_inspect_monster
from app.engine.combat import resolve_attack
from app.engine.encounter import FightEstimate, estimate_fight
from app.engine.state import ActionSnapshot
from tests.engine.factories import NOW, actor_texts, make_monster, make_player, player_update

RNG = random.Random(1)


def with_dummy(content: ContentRegistry, **fields: Any) -> ContentRegistry:
    """The training dummy (attack 5, defense 0) with other fields, e.g. abilities."""
    dummy = MonsterTemplate.model_validate({**content.monsters["dummy"].model_dump(), **fields})
    return dataclasses.replace(content, monsters={**content.monsters, "dummy": dummy})


def monster_hp(result: Any, monster_id: Any) -> int:
    [update] = [u for u in result.changes.monsters if u.monster_instance_id == monster_id]
    return int(update.model_dump(exclude_unset=True)["hp"])


def test_a_crushing_blow_multiplies_the_damage(content: ContentRegistry, rules: Rules) -> None:
    local = with_dummy(
        content, abilities=[{"type": "crushing_blow", "chance": 1.0, "multiplier": 2}]
    )
    actor = make_player()  # attack 5, defense 2, 20 HP: a plain strike does 3
    dummy = make_monster(hp=10)
    result = resolve_attack(
        ActionSnapshot(actor=actor, monsters=[dummy]), local, rules, NOW, RNG, dummy.id
    )
    assert actor_texts(result)[1] == (
        "Übungspuppe holt zu einem Wuchtschlag aus und trifft dich für 6 Schaden!"
    )
    assert player_update(result, actor.id)["hp"] == 14
    [room] = [e for e in result.events if e.kind == "combat.hit_taken" and e.exclude_player_ids]
    assert room.text == "Übungspuppe holt zu einem Wuchtschlag aus und trifft Anna für 6 Schaden!"


def test_life_drain_heals_the_monster(content: ContentRegistry, rules: Rules) -> None:
    local = with_dummy(content, max_hp=30, abilities=[{"type": "life_drain", "share": 1.0}])
    actor = make_player()
    dummy = make_monster(hp=20)
    result = resolve_attack(
        ActionSnapshot(actor=actor, monsters=[dummy]), local, rules, NOW, RNG, dummy.id
    )
    assert actor_texts(result)[1:] == [
        "Übungspuppe trifft dich für 3 Schaden.",
        "Übungspuppe saugt dir Lebenskraft ab und heilt sich um 3 HP.",
    ]
    assert monster_hp(result, dummy.id) == 20 - 5 + 3


def test_life_drain_stops_at_max_hp_and_at_the_damage_dealt(
    content: ContentRegistry, rules: Rules
) -> None:
    local = with_dummy(
        content, max_hp=30, defense=4, abilities=[{"type": "life_drain", "share": 1.0}]
    )
    actor = make_player()  # hits the dummy for 1 now
    dummy = make_monster(hp=30)
    result = resolve_attack(
        ActionSnapshot(actor=actor, monsters=[dummy]), local, rules, NOW, RNG, dummy.id
    )
    assert monster_hp(result, dummy.id) == 30
    assert actor_texts(result)[-1] == "Übungspuppe saugt dir Lebenskraft ab und heilt sich um 1 HP."

    actor = make_player()
    actor.hp = 2  # only 2 HP to take
    dummy = make_monster(hp=20)
    result = resolve_attack(
        ActionSnapshot(actor=actor, monsters=[dummy]), local, rules, NOW, RNG, dummy.id
    )
    assert monster_hp(result, dummy.id) == 20 - 1 + 2


def test_no_ability_no_roll(content: ContentRegistry, rules: Rules) -> None:
    """Monsters without abilities strike exactly as before (no extra random numbers)."""
    actor = make_player()
    dummy = make_monster(hp=10)
    result = resolve_attack(
        ActionSnapshot(actor=actor, monsters=[dummy]), content, rules, NOW, RNG, dummy.id
    )
    assert actor_texts(result)[1] == "Übungspuppe trifft dich für 3 Schaden."
    assert [e for e in result.events if e.kind == "combat.drain"] == []


def test_the_estimate_counts_abilities_on_average(content: ContentRegistry, rules: Rules) -> None:
    combat = rules.combat
    plain = content.monsters["dummy"]
    assert estimate_fight(5, 2, 10, plain, combat) == FightEstimate(5, 3, 2, 3)
    crushing = with_dummy(
        content, abilities=[{"type": "crushing_blow", "chance": 0.5, "multiplier": 2}]
    ).monsters["dummy"]
    fight = estimate_fight(5, 2, 20, crushing, combat)  # 4 hits, 3 strikes of 4.5 on average
    assert (fight.my_hit, fight.its_hit, fight.hits, fight.taken) == (5, 3, 4, 14)
    draining = with_dummy(content, abilities=[{"type": "life_drain", "share": 0.5}]).monsters[
        "dummy"
    ]
    fight = estimate_fight(5, 2, 20, draining, combat)  # 15 left, then 3.5 net per round: 5
    assert (fight.hits, fight.taken) == (6, 15)
    assert estimate_fight(5, 2, 5, draining, combat).taken == 0  # falls to the first hit
    hopeless = estimate_fight(2, 0, 20, draining, combat)  # heals 2.5 against hits of 2
    assert (hopeless.hits, hopeless.taken) == (None, None)


def test_inspect_lists_abilities(content: ContentRegistry, rules: Rules) -> None:
    local = with_dummy(
        content,
        max_hp=40,
        abilities=[
            {"type": "crushing_blow", "chance": 0.25, "multiplier": 1.5},
            {"type": "life_drain", "share": 2 / 3},
        ],
    )
    actor = make_player()
    actor.base_attack = 2  # 2 per hit against 2 healed per strike
    dummy = make_monster(hp=40)
    result = resolve_inspect_monster(
        ActionSnapshot(actor=actor, monsters=[dummy]), local, rules, NOW, RNG, dummy.id
    )
    lines = actor_texts(result)[0].splitlines()
    assert lines[1:3] == [
        "Fähigkeiten: Wuchtschlag (25 %: anderthalbfacher Schaden), "
        "Lebensraub (heilt sich um 67 % des Schadens).",
        "Du triffst für etwa 2, Übungspuppe für etwa 3. "
        "Er heilt sich schneller, als du ihm schadest: aussichtslos.",
    ]


def test_an_ability_may_be_listed_once(content: ContentRegistry) -> None:
    with pytest.raises(ValidationError, match="lists an ability twice"):
        with_dummy(
            content,
            abilities=[{"type": "life_drain", "share": 0.2}, {"type": "life_drain", "share": 0.3}],
        )
