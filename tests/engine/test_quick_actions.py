"""Quick actions: ``equip_best`` (put on every better backpack piece) and ``sell_junk`` (sell
what the character has no use for to the merchant at hand)."""

import dataclasses
import random
from uuid import uuid4

import pytest

from app.content.prices import sell_price
from app.content.registry import ContentRegistry
from app.content.schemas import ItemTemplate, Rules, StatModifier
from app.engine.gear import junk_for
from app.engine.inventory_actions import resolve_equip_best
from app.engine.merchant import resolve_sell_junk
from app.engine.state import ActionSnapshot, InventoryItemState, PlayerSnapshot
from app.engine.stats import stats_of
from tests.engine.factories import NOW, actor_texts, make_player, player_update

RNG = random.Random(1)


@pytest.fixture(scope="module")
def content(content: ContentRegistry) -> ContentRegistry:
    """Plus loot nobody asks for (the real world has none left: bosses' trophies are crafted)."""
    trinket = ItemTemplate(id="trinket", name="Tand", description="", kind="misc", value=20)
    return dataclasses.replace(content, items={**content.items, "trinket": trinket})


def entry(item_id: str, slot: str | None = None, quantity: int = 1) -> InventoryItemState:
    return InventoryItemState(
        id=uuid4(),
        item_id=item_id,
        quantity=quantity,
        equipped_slot=slot,  # type: ignore[arg-type]
    )


def worn(player: PlayerSnapshot) -> dict[str, str]:
    return {i.equipped_slot: i.item_id for i in player.inventory if i.equipped_slot}


# --- equip_best -------------------------------------------------------------------------------


def test_equip_best_puts_on_every_upgrade(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    actor.inventory = [
        entry("rusty_dagger", "weapon"),  # +2 attack
        entry("pitchfork"),  # +3: better, but the short sword is better still
        entry("short_sword"),  # +4
        entry("amulet"),  # empty neck slot: +5 max HP
        entry("healing_potion", quantity=2),
    ]
    result = resolve_equip_best(ActionSnapshot(actor=actor), content, rules, NOW, RNG)

    assert actor_texts(result) == [  # biggest gain first
        "Du rüstest Amulett aus.",
        "Du rüstest Kurzschwert aus und legst Rostiger Dolch ab.",
    ]
    assert worn(actor) == {"weapon": "short_sword", "neck": "amulet"}
    assert stats_of(actor, content.items).attack == 5 + 4
    upserted = {u.item.item_id: u.item.equipped_slot for u in result.changes.inventory_upsert}
    assert upserted == {"short_sword": "weapon", "rusty_dagger": None, "amulet": "neck"}


def test_equip_best_counts_set_bonuses(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    actor.inventory = [entry("leather_armor", "chest"), entry("leather_cap")]
    resolve_equip_best(ActionSnapshot(actor=actor), content, rules, NOW, RNG)
    stats = stats_of(actor, content.items)
    assert (stats.defense, stats.max_hp) == (2 + 2 + 1, 20 + 4)  # 2 pieces: +4 max HP


def test_equip_best_skips_mixed_locked_and_worse(content: ContentRegistry, rules: Rules) -> None:
    spiked = ItemTemplate(
        id="spiked_ring",
        name="Stachelring",
        description="",
        kind="armor",
        slot="ring",
        effects=[StatModifier(stat="attack", amount=9), StatModifier(stat="max_hp", amount=-1)],
    )
    local = dataclasses.replace(content, items={**content.items, "spiked_ring": spiked})
    actor = make_player()
    locked = entry("long_sword")
    actor.inventory = [
        entry("short_sword", "weapon"),
        entry("ring", "ring"),
        entry("spiked_ring"),  # +6 attack but −5 max HP against the worn ring: mixed
        entry("rusty_dagger"),
        locked,  # offered in a trade
    ]
    actor.locked_item_ids = [locked.id]

    result = resolve_equip_best(ActionSnapshot(actor=actor), local, rules, NOW, RNG)
    assert actor_texts(result) == ["Nichts im Rucksack ist besser als deine Ausrüstung."]
    assert result.changes.inventory_upsert == []


def test_equip_best_takes_one_piece_off_a_stack(content: ContentRegistry, rules: Rules) -> None:
    sharp = content.items["knives"].model_copy(
        update={"effects": [StatModifier(stat="attack", amount=1)]}
    )
    local = dataclasses.replace(content, items={**content.items, "knives": sharp})
    actor = make_player()
    actor.inventory = [entry("knives", quantity=3)]
    resolve_equip_best(ActionSnapshot(actor=actor), local, rules, NOW, RNG)
    assert sorted((i.quantity, i.equipped_slot or "") for i in actor.inventory) == [
        (1, "weapon"),
        (2, ""),
    ]


def test_equip_best_only_while_exploring(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    actor.inventory = [entry("short_sword")]
    actor.state = "in_combat"
    result = resolve_equip_best(ActionSnapshot(actor=actor), content, rules, NOW, RNG)
    assert result.changes.inventory_upsert == []


# --- sell_junk --------------------------------------------------------------------------------


def test_sell_junk_sells_old_gear_and_unneeded_loot(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room="town_market", gold=10)  # the alchemist buys everything
    locked = entry("rusty_dagger")
    actor.inventory = [
        entry("short_sword", "weapon"),
        entry("pitchfork"),  # worse than the short sword
        entry("short_sword"),  # same as the worn one
        entry("trinket", quantity=2),  # loot no recipe or quest asks for
        entry("goblin_ear", quantity=3),  # a recipe ingredient
        entry("healing_potion"),  # consumables are never junk
        entry("leather_cap"),  # empty head slot: an upgrade
        entry("long_sword"),  # better
        locked,
    ]
    actor.locked_item_ids = [locked.id]
    alchemist = content.get_merchant("alchemist")
    expected = sum(
        (sell_price(content.items[i], alchemist, rules) or 0) * q
        for i, q in (("pitchfork", 1), ("short_sword", 1), ("trinket", 2))
    )

    result = resolve_sell_junk(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "alchemist")

    assert actor_texts(result) == [
        f"Du verkaufst Mistgabel, Kurzschwert, 2x Tand für {expected} Gold."
    ]
    assert player_update(result, actor.id)["gold"] == 10 + expected
    left = sorted(i.item_id for i in actor.inventory)
    assert left == sorted(
        [
            "short_sword",
            "goblin_ear",
            "healing_potion",
            "leather_cap",
            "long_sword",
            "rusty_dagger",
        ]
    )
    assert len(result.changes.inventory_delete) == 3


def test_sell_junk_keeps_set_pieces_and_what_the_merchant_does_not_buy(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = make_player(room="town_smithy")  # the smith buys weapons and armor only
    actor.inventory = [
        entry("leather_armor", "chest"),
        entry("leather_armor"),  # a spare set piece
        entry("trinket"),
        entry("pitchfork"),
    ]
    junk = junk_for(actor, content.get_merchant("smith"), content, rules)
    assert [j.entry.item_id for j in junk] == []  # pitchfork: empty weapon slot, an upgrade
    result = resolve_sell_junk(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "smith")
    assert actor_texts(result) == ["Du hast keinen Ramsch, den Schmied Brandt kauft."]


def test_sell_junk_needs_the_merchant_here(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()  # dark forest
    actor.inventory = [entry("trinket")]
    result = resolve_sell_junk(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "alchemist")
    assert actor_texts(result) == ["Dieser Händler ist nicht hier."]
