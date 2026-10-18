"""Boosting potions: a stat bonus for the next few kills (engine/buffs.py)."""

import random
from uuid import uuid4

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.combat import resolve_attack
from app.engine.inventory_actions import resolve_use_item
from app.engine.state import ActionSnapshot, ActiveBuff, InventoryItemState
from app.engine.stats import stats_of
from tests.engine.factories import NOW, actor_texts, make_monster, make_player, player_update

RNG = random.Random(1)


def potion(item_id: str, quantity: int = 1) -> InventoryItemState:
    return InventoryItemState(id=uuid4(), item_id=item_id, quantity=quantity)


def test_drinking_a_boost_raises_the_stat(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()  # full HP: a boost is still welcome
    strength = potion("strength_potion", 2)
    actor.inventory = [strength]
    result = resolve_use_item(ActionSnapshot(actor=actor), content, rules, NOW, RNG, strength.id)

    assert actor_texts(result) == [
        "Du benutzt Trank der Stärke und spürst die Wirkung: +3 Angriff für 3 Siege."
    ]
    assert stats_of(actor, content.items).attack == 5 + 3
    assert player_update(result, actor.id)["buffs"] == [
        {"item_id": "strength_potion", "stat": "attack", "amount": 3, "fights_left": 3}
    ]
    assert strength.quantity == 1


def test_the_same_potion_starts_over_and_different_ones_add_up(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = make_player()
    actor.buffs = [ActiveBuff(item_id="strength_potion", stat="attack", amount=3, fights_left=1)]
    again, skin = potion("strength_potion"), potion("stoneskin_potion")
    actor.inventory = [again, skin]
    resolve_use_item(ActionSnapshot(actor=actor), content, rules, NOW, RNG, again.id)
    resolve_use_item(ActionSnapshot(actor=actor), content, rules, NOW, RNG, skin.id)
    assert [(b.item_id, b.fights_left) for b in actor.buffs] == [
        ("strength_potion", 3),
        ("stoneskin_potion", 3),
    ]
    stats = stats_of(actor, content.items)
    assert (stats.attack, stats.defense) == (5 + 3, 2 + 3)


def test_a_max_hp_boost_makes_room_for_its_own_heal(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(hp=20)  # full: 20/20
    vigor = potion("vigor_potion")
    actor.inventory = [vigor]
    result = resolve_use_item(ActionSnapshot(actor=actor), content, rules, NOW, RNG, vigor.id)
    assert actor_texts(result)[0] == (
        "Du benutzt Trank der Lebenskraft und erhältst 20 HP und spürst die Wirkung: "
        "+20 max. HP für 3 Siege."
    )
    assert (actor.hp, stats_of(actor, content.items).max_hp) == (40, 40)


def test_each_kill_credit_wears_a_boost_down(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(hp=40)
    actor.buffs = [
        ActiveBuff(item_id="vigor_potion", stat="max_hp", amount=20, fights_left=1),
        ActiveBuff(item_id="strength_potion", stat="attack", amount=3, fights_left=2),
    ]
    monster = make_monster(hp=1)
    result = resolve_attack(
        ActionSnapshot(actor=actor, monsters=[monster]), content, rules, NOW, RNG, monster.id
    )
    assert "Die Wirkung von Trank der Lebenskraft lässt nach." in actor_texts(result)
    assert [(b.item_id, b.fights_left) for b in actor.buffs] == [("strength_potion", 1)]
    assert player_update(result, actor.id)["hp"] == 20  # the extra HP went with the boost


def test_death_ends_every_boost(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(hp=1)
    actor.buffs = [ActiveBuff(item_id="stoneskin_potion", stat="defense", amount=3, fights_left=3)]
    troll = make_monster("troll", hp=100)
    actor.state, actor.combat_target_id = "in_combat", troll.id
    result = resolve_attack(
        ActionSnapshot(actor=actor, monsters=[troll]), content, rules, NOW, RNG, troll.id
    )
    assert player_update(result, actor.id)["state"] == "dead"
    assert player_update(result, actor.id)["buffs"] == []


def test_boost_potions_are_sold_and_described(content: ContentRegistry) -> None:
    from app.content.describe import item_summary

    assert item_summary(content.items["dragon_blood"]) == (
        "+5 Angriff für 4 Siege, +3 Verteidigung für 4 Siege"
    )
    offers = {o.item_id for o in content.merchants["alchemist"].sells}
    assert {"strength_potion", "stoneskin_potion", "vigor_potion"} <= offers
