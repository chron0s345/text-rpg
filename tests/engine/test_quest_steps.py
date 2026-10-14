"""Quests with several steps and visit steps.

Test quests (tests/engine/conftest.py): "tour" (Elira: go to the town square, kill one
"plain", bring bread), "errand" (Elira: visit her own stand – done on accepting –, then the
dark forest).
"""

import random

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.combat import resolve_attack
from app.engine.events import ResultBuilder
from app.engine.movement import resolve_move
from app.engine.quests import count_visit, resolve_quest_accept, resolve_quest_turn_in
from app.engine.state import ActionSnapshot, PlayerSnapshot, QuestState
from tests.engine.factories import NOW, actor_texts, make_monster, make_player
from tests.engine.test_inventory_actions import give

RNG = random.Random(4)


def turn_in(actor: PlayerSnapshot, content: ContentRegistry, rules: Rules) -> list[str]:
    return actor_texts(
        resolve_quest_turn_in(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "tour")
    )


def test_steps_are_done_in_order(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room="town_market")
    resolve_quest_accept(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "tour")
    give(actor, "bread")
    assert turn_in(actor, content, rules) == [
        "Rundgang: erst noch – Gehe zu: Marktplatz von Oakhaven."
    ]

    # a kill before its step does not count
    actor.current_room_id = "dark_forest"
    plain = make_monster("plain", hp=1)
    resolve_attack(
        ActionSnapshot(actor=actor, monsters=[plain]), content, rules, NOW, RNG, plain.id
    )
    assert actor.quest("tour") == QuestState(quest_id="tour", status="active")

    actor.current_room_id = "town_market"
    moved = resolve_move(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "west")
    assert "Rundgang: Marktplatz von Oakhaven erreicht – weiter: Besiege 1× Feldmaus." in (
        actor_texts(moved)
    )
    [upsert] = moved.changes.quests_upsert
    assert (upsert.step, upsert.progress) == (1, 0)
    actor.current_room_id = "town_market"
    assert turn_in(actor, content, rules) == ["Rundgang: Schritt 2/3, erst 0/1."]

    actor.current_room_id = "dark_forest"  # the square is safe: no fights there
    plain = make_monster("plain", hp=1)
    killed = resolve_attack(
        ActionSnapshot(actor=actor, monsters=[plain]), content, rules, NOW, RNG, plain.id
    )
    assert "Rundgang: 1/1 – jetzt: Bringe 1× Frisches Brot zu Alchemistin Elira." in (
        actor_texts(killed)
    )

    actor.current_room_id = "town_market"
    texts = turn_in(actor, content, rules)
    assert texts[0] == "Auftrag erledigt: Rundgang. Alchemistin Elira: „Fein.“"
    assert not any(i.item_id == "bread" for i in actor.inventory)


def test_a_visit_to_where_one_stands_is_done_at_once(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = make_player(room="town_market")
    result = resolve_quest_accept(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "errand")
    assert actor_texts(result)[-1] == (
        "Botengang: Kaufmannsstand – schon da – weiter: Gehe zu: Dunkler Wald."
    )
    assert actor.quest("errand").step == 1  # type: ignore[union-attr]

    actor.current_room_id = "dark_forest"
    builder = ResultBuilder(actor_id=actor.id)
    count_visit(builder, actor, content)
    assert actor_texts(builder.build()) == [
        "Botengang: Dunkler Wald erreicht – zurück zu Alchemistin Elira!"
    ]

    actor.current_room_id = "town_market"
    done = resolve_quest_turn_in(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "errand")
    assert actor_texts(done)[0] == "Auftrag erledigt: Botengang. Alchemistin Elira: „Danke.“"
