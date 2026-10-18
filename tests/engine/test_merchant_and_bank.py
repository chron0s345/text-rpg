"""Merchants (engine/merchant.py), bank (engine/bank.py) and the shared price rules."""

import random
from uuid import UUID, uuid4

import pytest

from app.content.prices import buy_price, sell_price
from app.content.registry import ContentRegistry
from app.content.schemas import MerchantOffer, Rules
from app.engine.bank import (
    resolve_bank_deposit,
    resolve_bank_deposit_materials,
    resolve_bank_withdraw,
)
from app.engine.merchant import resolve_buy, resolve_sell
from app.engine.state import ActionSnapshot, InventoryItemState
from tests.engine.factories import NOW, actor_texts, fight, make_monster, make_player, player_update

RNG = random.Random(9)
MARKET, BANK = "town_market", "town_bank"


def stack(item_id: str, quantity: int = 1, slot: str | None = None) -> InventoryItemState:
    return InventoryItemState.model_validate(
        {"id": uuid4(), "item_id": item_id, "quantity": quantity, "equipped_slot": slot}
    )


# --- prices ---


def test_prices(content: ContentRegistry, rules: Rules) -> None:
    alchemist = content.merchants["alchemist"]
    dagger, ear = content.items["rusty_dagger"], content.items["goblin_ear"]
    assert buy_price(MerchantOffer(item_id="rusty_dagger", price=40), dagger, rules) == 40
    assert buy_price(MerchantOffer(item_id="rusty_dagger"), dagger, rules) == int(
        dagger.value * rules.merchant.buy_ratio
    )
    assert sell_price(ear, alchemist, rules) == 1  # floor(4 * 0.25)
    picky = alchemist.model_copy(update={"buys": "listed", "buys_kinds": ["consumable"]})
    assert sell_price(ear, picky, rules) is None
    unsellable = ear.model_copy(update={"sellable": False})
    assert sell_price(unsellable, alchemist, rules) is None


# --- buying ---


def test_buy(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(gold=40, room=MARKET)
    actor.inventory = [stack("bread", 9)]
    result = resolve_buy(
        ActionSnapshot(actor=actor), content, rules, NOW, RNG, "alchemist", "bread", 3
    )
    assert actor_texts(result) == ["Du kaufst 3x Frisches Brot für 9 Gold."]
    assert player_update(result, actor.id)["gold"] == 31
    # stacks are topped up first (max_stack 10), then a new stack
    assert sorted(u.item.quantity for u in result.changes.inventory_upsert) == [2, 10]


@pytest.mark.parametrize(
    ("gold", "merchant", "item", "quantity", "message"),
    [
        (100, "nobody", "bread", 1, "Dieser Händler ist nicht hier."),
        (100, "alchemist", "rusty_dagger", 1, "Alchemistin Elira verkauft das nicht."),
        (10, "alchemist", "healing_potion", 1, "Das kostet 15 Gold, du hast nur 10."),
    ],
)
def test_buy_rejections(
    content: ContentRegistry,
    rules: Rules,
    gold: int,
    merchant: str,
    item: str,
    quantity: int,
    message: str,
) -> None:
    actor = make_player(gold=gold, room=MARKET)
    result = resolve_buy(
        ActionSnapshot(actor=actor), content, rules, NOW, RNG, merchant, item, quantity
    )
    assert actor_texts(result) == [message]
    assert result.changes.players == []


def test_buy_needs_room_for_everything(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(gold=1000, room=MARKET)
    actor.inventory = [stack("goblin_ear", 1) for _ in range(rules.inventory.slots)]
    result = resolve_buy(
        ActionSnapshot(actor=actor), content, rules, NOW, RNG, "alchemist", "bread"
    )
    assert actor_texts(result) == ["Dafür ist in deinem Inventar kein Platz."]
    assert result.changes.players == []  # no gold taken


def test_buy_only_at_the_merchant_and_not_in_combat(content: ContentRegistry, rules: Rules) -> None:
    away = make_player(gold=100, room="town_square")
    result = resolve_buy(ActionSnapshot(actor=away), content, rules, NOW, RNG, "alchemist", "bread")
    assert actor_texts(result) == ["Dieser Händler ist nicht hier."]
    fighting = make_player(gold=100, room=MARKET)
    fight(fighting, make_monster())
    result = resolve_buy(
        ActionSnapshot(actor=fighting), content, rules, NOW, RNG, "alchemist", "bread"
    )
    assert actor_texts(result) == ["Das geht nicht im Kampf."]


# --- selling ---


def test_sell_part_of_a_stack(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(gold=5, room=MARKET)
    ears = stack("goblin_ear", 6)
    actor.inventory = [ears]
    result = resolve_sell(
        ActionSnapshot(actor=actor), content, rules, NOW, RNG, "alchemist", ears.id, 4
    )
    assert actor_texts(result) == ["Du verkaufst 4x Goblinohr für 4 Gold."]
    assert player_update(result, actor.id)["gold"] == 9
    [left] = result.changes.inventory_upsert
    assert left.item.quantity == 2


def test_sell_whole_stack_deletes_it(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room=MARKET)
    dagger = stack("rusty_dagger")
    actor.inventory = [dagger]
    result = resolve_sell(
        ActionSnapshot(actor=actor), content, rules, NOW, RNG, "alchemist", dagger.id
    )
    assert result.changes.inventory_delete == [dagger.id]
    assert player_update(result, actor.id)["gold"] == 2


def test_sell_rejections(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room=MARKET)
    worn = stack("rusty_dagger", slot="weapon")
    offered = stack("goblin_ear", 3)
    actor.inventory = [worn, offered]
    actor.locked_item_ids = [offered.id]

    def sell(item_id: UUID, quantity: int = 1) -> list[str]:
        return actor_texts(
            resolve_sell(
                ActionSnapshot(actor=actor),
                content,
                rules,
                NOW,
                RNG,
                "alchemist",
                item_id,
                quantity,
            )
        )

    assert sell(uuid4()) == ["Diesen Gegenstand hast du nicht."]
    assert sell(worn.id) == ["Lege den Gegenstand zuerst ab."]
    assert sell(offered.id) == ["Dieser Gegenstand ist im Handel angeboten."]
    actor.locked_item_ids = []
    assert sell(offered.id, 4) == ["So viele hast du nicht."]


# --- bank ---


def test_deposit_and_withdraw(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room=BANK)
    ears = stack("goblin_ear", 15)
    actor.inventory = [ears]
    stored = stack("goblin_ear", 10)
    snapshot = ActionSnapshot(actor=actor, bank=[stored])

    result = resolve_bank_deposit(snapshot, content, rules, NOW, RNG, ears.id, 15)
    assert actor_texts(result) == ["Du lagerst 15x Goblinohr ein."]
    assert result.changes.inventory_delete == [ears.id]
    # bank stacks fill up first (max_stack 20): 10 -> 20, plus a new stack of 5
    assert sorted(u.item.quantity for u in result.changes.bank_upsert) == [5, 20]

    actor.inventory = []
    snapshot = ActionSnapshot(actor=actor, bank=[stack("goblin_ear", 20)])
    bank_id = snapshot.bank[0].id
    result = resolve_bank_withdraw(snapshot, content, rules, NOW, RNG, bank_id, 20)
    assert result.changes.bank_delete == [bank_id]
    assert [u.item.quantity for u in result.changes.inventory_upsert] == [20]


def test_deposit_moves_only_what_fits(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room=BANK)
    dagger = stack("rusty_dagger")
    actor.inventory = [dagger]
    full = [stack("rusty_dagger") for _ in range(rules.bank.slots)]
    result = resolve_bank_deposit(
        ActionSnapshot(actor=actor, bank=full), content, rules, NOW, RNG, dagger.id
    )
    assert actor_texts(result) == ["Deine Bank ist voll."]

    ears = stack("goblin_ear", 15)
    actor.inventory = [ears]
    almost = [stack("rusty_dagger") for _ in range(rules.bank.slots - 1)] + [
        stack("goblin_ear", 12)
    ]
    result = resolve_bank_deposit(
        ActionSnapshot(actor=actor, bank=almost), content, rules, NOW, RNG, ears.id, 15
    )
    assert actor_texts(result) == ["Du lagerst 8x Goblinohr ein. Kein Platz für 7 weitere."]
    [left] = result.changes.inventory_upsert
    assert left.item.quantity == 7


def test_bank_only_in_bank_rooms_and_not_equipped(content: ContentRegistry, rules: Rules) -> None:
    worn = stack("leather_armor", slot="chest")
    away = make_player(room=MARKET)
    away.inventory = [worn]
    result = resolve_bank_deposit(ActionSnapshot(actor=away), content, rules, NOW, RNG, worn.id)
    assert actor_texts(result) == ["Hier gibt es keine Bank."]
    here = make_player(room=BANK)
    here.inventory = [worn]
    result = resolve_bank_deposit(ActionSnapshot(actor=here), content, rules, NOW, RNG, worn.id)
    assert actor_texts(result) == ["Lege den Gegenstand zuerst ab."]


def test_withdraw_into_a_full_inventory(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room=BANK)
    actor.inventory = [stack("rusty_dagger") for _ in range(rules.inventory.slots)]
    stored = stack("bread", 2)
    result = resolve_bank_withdraw(
        ActionSnapshot(actor=actor, bank=[stored]), content, rules, NOW, RNG, stored.id, 2
    )
    assert actor_texts(result) == ["Dein Inventar ist voll."]
    assert result.changes.bank_upsert == [] and result.changes.bank_delete == []


def test_deposit_materials_stores_loot_but_keeps_gear_and_potions(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = make_player(room=BANK)
    ears, dust, potion, dagger = (
        stack("goblin_ear", 5),
        stack("bone_dust", 3),
        stack("healing_potion", 2),
        stack("rusty_dagger"),
    )
    offered = stack("crab_shell", 2)
    actor.inventory = [ears, dust, potion, dagger, offered]
    actor.locked_item_ids = [offered.id]
    snapshot = ActionSnapshot(actor=actor, bank=[stack("goblin_ear", 18)])

    result = resolve_bank_deposit_materials(snapshot, content, rules, NOW, RNG)

    assert actor_texts(result) == ["Du lagerst 5x Goblinohr, 3x Knochenstaub ein."]
    assert sorted(result.changes.inventory_delete) == sorted([ears.id, dust.id])
    assert sorted(i.item_id for i in actor.inventory) == [
        "crab_shell",
        "healing_potion",
        "rusty_dagger",
    ]
    assert sorted(u.item.quantity for u in result.changes.bank_upsert) == [3, 3, 20]


def test_deposit_materials_rejections_and_a_full_bank(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = make_player(room=BANK)
    actor.inventory = [stack("healing_potion")]
    nothing = resolve_bank_deposit_materials(ActionSnapshot(actor=actor), content, rules, NOW, RNG)
    assert actor_texts(nothing) == ["Du hast kein Material dabei."]

    actor.inventory = [stack("goblin_ear", 4)]
    full = [stack("rusty_dagger") for _ in range(rules.bank.slots)]
    result = resolve_bank_deposit_materials(
        ActionSnapshot(actor=actor, bank=full), content, rules, NOW, RNG
    )
    assert actor_texts(result) == ["Deine Bank ist voll."]

    elsewhere = make_player()  # dark forest: no bank
    elsewhere.inventory = [stack("goblin_ear", 4)]
    result = resolve_bank_deposit_materials(
        ActionSnapshot(actor=elsewhere), content, rules, NOW, RNG
    )
    assert actor_texts(result) == ["Hier gibt es keine Bank."]
