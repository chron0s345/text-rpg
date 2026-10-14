import random
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from app.content.registry import ContentRegistry
from app.content.schemas import EquipSlot, Rules
from app.engine.actions import resolve_look
from app.engine.events import ActionResult
from app.engine.inventory_actions import (
    resolve_drop,
    resolve_equip,
    resolve_inventory,
    resolve_pick_up,
    resolve_unequip,
    resolve_use_item,
)
from app.engine.state import (
    ActionSnapshot,
    FloorItemSnapshot,
    InventoryItemState,
    PlayerSnapshot,
)
from app.engine.stats import stats_of
from tests.engine.factories import (
    NOW,
    ROOM,
    actor_texts,
    fight,
    make_monster,
    make_player,
    monster_update,
    player_update,
)

RNG = random.Random(1)


def give(
    player: PlayerSnapshot, item_id: str, quantity: int = 1, slot: EquipSlot | None = None
) -> InventoryItemState:
    entry = InventoryItemState(id=uuid4(), item_id=item_id, quantity=quantity, equipped_slot=slot)
    player.inventory.append(entry)
    return entry


def fill(player: PlayerSnapshot, rules: Rules, free_slots: int = 0) -> None:
    used = sum(1 for i in player.inventory if i.equipped_slot is None)
    for _ in range(rules.inventory.slots - used - free_slots):
        give(player, "goblin_ear")


def upserts(result: ActionResult) -> dict[UUID, InventoryItemState]:
    return {u.item.id: u.item for u in result.changes.inventory_upsert}


def floor(**fields: Any) -> FloorItemSnapshot:
    data: dict[str, Any] = {
        "id": uuid4(),
        "room_id": ROOM,
        "item_id": "goblin_ear",
        "quantity": 1,
        "source": "loot",
        "expires_at": NOW + timedelta(seconds=60),
    }
    data.update(fields)
    return FloorItemSnapshot.model_validate(data)


# --- equip ----------------------------------------------------------------------------------


def test_equip_frees_a_slot(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    dagger = give(actor, "rusty_dagger")

    result = resolve_equip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, dagger.id)

    assert upserts(result)[dagger.id].equipped_slot == "weapon"
    assert actor_texts(result) == ["Du rüstest Rostiger Dolch aus."]


def test_equip_swaps_with_item_in_same_slot(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    old = give(actor, "rusty_dagger", slot="weapon")
    new = give(actor, "rusty_dagger")
    fill(actor, rules)  # full inventory: the swap still fits (one in, one out)

    result = resolve_equip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, new.id)

    changed = upserts(result)
    assert (changed[old.id].equipped_slot, changed[new.id].equipped_slot) == (None, "weapon")
    assert actor_texts(result) == [
        "Du legst Rostiger Dolch ab.",
        "Du rüstest Rostiger Dolch aus.",
    ]


def test_every_slot_is_worn_at_once_and_adds_up(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    worn = [
        give(actor, item)
        for item in ("rusty_dagger", "leather_cap", "leather_armor", "leather_boots")
        + ("buckler", "amulet", "ring")
    ]
    for entry in worn:
        result = resolve_equip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, entry.id)
        assert actor_texts(result)[-1].startswith("Du rüstest"), actor_texts(result)

    slots = sorted(i.equipped_slot or "" for i in actor.inventory)
    assert slots == sorted(["weapon", "head", "chest", "feet", "offhand", "neck", "ring"])
    stats = stats_of(actor, content.items)
    base = (actor.base_max_hp, actor.base_attack, actor.base_defense)
    # dagger +2 / ring +3 attack; cap 1, leather 2, boots 1, buckler 2 defense;
    # amulet +5 / ring +4 max HP
    assert (stats.max_hp, stats.attack, stats.defense) == (base[0] + 9, base[1] + 5, base[2] + 6)


def test_equip_one_of_a_stack_splits_it(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    knives = give(actor, "knives", quantity=3)

    result = resolve_equip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, knives.id)

    changed = upserts(result)
    assert changed[knives.id].quantity == 2
    [equipped] = [i for i in changed.values() if i.id != knives.id]
    assert (equipped.item_id, equipped.quantity, equipped.equipped_slot) == ("knives", 1, "weapon")


def test_equip_split_with_swap_needs_a_free_slot(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    give(actor, "rusty_dagger", slot="weapon")
    knives = give(actor, "knives", quantity=3)
    fill(actor, rules)  # the stack stays, the dagger comes back -> one slot too many

    result = resolve_equip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, knives.id)

    assert actor_texts(result) == ["Kein Platz im Inventar."]
    assert result.changes.inventory_upsert == []


def test_equip_rejections(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    potion = give(actor, "healing_potion")
    dagger = give(actor, "rusty_dagger")
    worn = give(actor, "leather_armor", slot="chest")

    def equip(item_id: UUID, player: PlayerSnapshot = actor) -> list[str]:
        return actor_texts(
            resolve_equip(ActionSnapshot(actor=player), content, rules, NOW, RNG, item_id)
        )

    assert equip(potion.id) == ["Das kann man nicht ausrüsten."]
    assert equip(worn.id) == ["Das trägst du bereits."]
    assert equip(uuid4()) == ["Diesen Gegenstand hast du nicht."]
    actor.locked_item_ids = [dagger.id]
    assert equip(dagger.id) == ["Dieser Gegenstand ist im Handel angeboten."]
    fighting = make_player()
    fight(fighting, make_monster())
    sword = give(fighting, "rusty_dagger")
    assert equip(sword.id, fighting) == ["Das geht nicht im Kampf."]


def test_bag_slots_are_limited(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    for _ in range(rules.inventory.bag_slots):
        give(actor, "small_bag", slot="bag")
    extra = give(actor, "small_bag")

    result = resolve_equip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, extra.id)

    assert actor_texts(result) == ["Alle Taschenplätze sind belegt."]


def test_equip_bag_increases_capacity(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    bag = give(actor, "small_bag")
    resolve_equip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, bag.id)
    [view] = resolve_inventory(ActionSnapshot(actor=actor), content, rules, NOW, RNG).events
    assert (view.data["used"], view.data["capacity"], view.data["bags"]) == (
        0,
        rules.inventory.slots + 2,
        1,
    )


# --- unequip ---------------------------------------------------------------------------------


def test_unequip_needs_a_free_slot(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    dagger = give(actor, "rusty_dagger", slot="weapon")
    fill(actor, rules, free_slots=1)
    ok = resolve_unequip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, dagger.id)
    assert upserts(ok)[dagger.id].equipped_slot is None

    actor = make_player()
    dagger = give(actor, "rusty_dagger", slot="weapon")
    fill(actor, rules)
    full = resolve_unequip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, dagger.id)
    assert actor_texts(full) == ["Kein Platz im Inventar."]


def test_unequip_bag_only_when_everything_still_fits(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = make_player()
    bag = give(actor, "small_bag", slot="bag")
    fill(actor, rules)  # uses the base slots; the bag's 2 extra slots are still free
    give(actor, "goblin_ear")  # now one extra slot is in use

    result = resolve_unequip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, bag.id)
    assert actor_texts(result) == ["Leere die Tasche zuerst."]

    actor = make_player()
    bag = give(actor, "small_bag", slot="bag")
    result = resolve_unequip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, bag.id)
    assert upserts(result)[bag.id].equipped_slot is None


def test_unequip_caps_hp_to_new_maximum(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(hp=25)
    amulet = give(actor, "amulet", slot="neck")

    result = resolve_unequip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, amulet.id)

    assert player_update(result, actor.id)["hp"] == 20


def test_unequip_rejects_items_not_worn(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    dagger = give(actor, "rusty_dagger")
    result = resolve_unequip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, dagger.id)
    assert actor_texts(result) == ["Das trägst du nicht."]


# --- use ------------------------------------------------------------------------------------


def test_use_potion_heals_up_to_max_and_consumes(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(hp=12)
    potions = give(actor, "healing_potion", quantity=2)

    result = resolve_use_item(ActionSnapshot(actor=actor), content, rules, NOW, RNG, potions.id)

    assert player_update(result, actor.id) == {"player_id": actor.id, "hp": 20}
    assert upserts(result)[potions.id].quantity == 1
    assert actor_texts(result) == ["Du benutzt Heiltrank und erhältst 8 HP."]


def test_using_last_item_deletes_the_entry(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(hp=5)
    bread = give(actor, "bread")
    result = resolve_use_item(ActionSnapshot(actor=actor), content, rules, NOW, RNG, bread.id)
    assert result.changes.inventory_delete == [bread.id]
    assert player_update(result, actor.id)["hp"] == 11


def test_use_rejections(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    potion = give(actor, "healing_potion")
    dagger = give(actor, "rusty_dagger")

    def use(item_id: UUID) -> list[str]:
        return actor_texts(
            resolve_use_item(ActionSnapshot(actor=actor), content, rules, NOW, RNG, item_id)
        )

    assert use(potion.id) == ["Du bist bereits unverletzt."]
    assert use(dagger.id) == ["Das kann man nicht benutzen."]
    actor.hp = 5
    actor.locked_item_ids = [potion.id]
    assert use(potion.id) == ["Dieser Gegenstand ist im Handel angeboten."]


def test_use_in_combat_costs_the_round(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(hp=5)
    potion = give(actor, "healing_potion")
    monster = make_monster()
    fight(actor, monster)

    result = resolve_use_item(
        ActionSnapshot(actor=actor, monsters=[monster]), content, rules, NOW, RNG, potion.id
    )

    # healed to 20, then the monster hits for 5 - 2 = 3
    assert player_update(result, actor.id)["hp"] == 17
    assert monster_update(result, monster.id)["last_combat_at"] == NOW


# --- pick up ----------------------------------------------------------------------------------


def test_pick_up_partial_leaves_rest_on_floor(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    for _ in range(rules.inventory.slots - 1):
        give(actor, "rusty_dagger")  # one free slot = one stack of at most 20 ears
    pile = floor(quantity=30)

    result = resolve_pick_up(
        ActionSnapshot(actor=actor, floor_items=[pile]), content, rules, NOW, RNG, pile.id
    )

    [new_stack] = upserts(result).values()
    assert (new_stack.item_id, new_stack.quantity) == ("goblin_ear", 20)
    assert [(u.id, u.quantity) for u in result.changes.floor_items_update] == [(pile.id, 10)]
    assert result.changes.floor_items_delete == []
    assert actor_texts(result) == ["Du hebst 20x Goblinohr auf. Kein Platz für 10 weitere."]


def test_pick_up_requested_quantity(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    pile = floor(quantity=5)
    result = resolve_pick_up(
        ActionSnapshot(actor=actor, floor_items=[pile]), content, rules, NOW, RNG, pile.id, 2
    )
    assert [u.quantity for u in upserts(result).values()] == [2]
    assert [(u.id, u.quantity) for u in result.changes.floor_items_update] == [(pile.id, 3)]


def test_pick_up_everything_deletes_floor_item(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    pile = floor(quantity=3)
    result = resolve_pick_up(
        ActionSnapshot(actor=actor, floor_items=[pile]), content, rules, NOW, RNG, pile.id
    )
    assert result.changes.floor_items_delete == [pile.id]
    assert result.changes.item_spawns == []  # loot never respawns
    room_events = [e for e in result.events if e.audience.kind == "room"]
    assert [e.exclude_player_ids for e in room_events] == [[actor.id]]


def test_pick_up_spawn_item_schedules_respawn(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room="goblin_camp")
    bread = floor(
        room_id="goblin_camp", item_id="bread", quantity=2, source="spawn", expires_at=None
    )

    result = resolve_pick_up(
        ActionSnapshot(actor=actor, floor_items=[bread]), content, rules, NOW, RNG, bread.id
    )

    [spawn] = result.changes.item_spawns
    assert (spawn.room_id, spawn.item_id, spawn.next_spawn_at) == (
        "goblin_camp",
        "bread",
        NOW + timedelta(seconds=120),
    )


def test_pick_up_blocks_items_of_own_account(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    own = floor(source="drop", dropped_by_account_id=actor.account_id)
    foreign = floor(source="drop", dropped_by_account_id=uuid4())
    snapshot = ActionSnapshot(actor=actor, floor_items=[own, foreign])

    blocked = resolve_pick_up(snapshot, content, rules, NOW, RNG, own.id)
    allowed = resolve_pick_up(snapshot, content, rules, NOW, RNG, foreign.id)

    assert actor_texts(blocked) == [
        "Diesen Gegenstand hat einer deiner Charaktere fallen gelassen."
    ]
    assert allowed.changes.floor_items_delete == [foreign.id]


def test_pick_up_ignores_expired_and_other_rooms(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    expired = floor(expires_at=NOW)
    elsewhere = floor(room_id="goblin_camp")
    snapshot = ActionSnapshot(actor=actor, floor_items=[expired, elsewhere])
    for item in (expired, elsewhere):
        result = resolve_pick_up(snapshot, content, rules, NOW, RNG, item.id)
        assert actor_texts(result) == ["Das liegt hier nicht."]


def test_pick_up_with_full_inventory(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    for _ in range(rules.inventory.slots):
        give(actor, "rusty_dagger")
    pile = floor()
    result = resolve_pick_up(
        ActionSnapshot(actor=actor, floor_items=[pile]), content, rules, NOW, RNG, pile.id
    )
    assert actor_texts(result) == ["Dein Inventar ist voll."]
    assert result.changes.floor_items_update == []


# --- drop -------------------------------------------------------------------------------------


def test_drop_part_of_a_stack(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    potions = give(actor, "healing_potion", quantity=5)

    result = resolve_drop(ActionSnapshot(actor=actor), content, rules, NOW, RNG, potions.id, 2)

    assert upserts(result)[potions.id].quantity == 3
    [created] = result.changes.floor_items_create
    assert (created.room_id, created.item_id, created.quantity, created.source) == (
        ROOM,
        "healing_potion",
        2,
        "drop",
    )
    assert created.dropped_by_account_id == actor.account_id
    assert created.expires_at == NOW + timedelta(seconds=rules.timings.floor_item_ttl_seconds)


def test_drop_whole_entry_deletes_it(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    dagger = give(actor, "rusty_dagger")
    result = resolve_drop(ActionSnapshot(actor=actor), content, rules, NOW, RNG, dagger.id)
    assert result.changes.inventory_delete == [dagger.id]


def test_drop_rejections(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    worn = give(actor, "rusty_dagger", slot="weapon")
    potions = give(actor, "healing_potion", quantity=2)

    def drop(item_id: UUID, quantity: int | None = None) -> list[str]:
        return actor_texts(
            resolve_drop(ActionSnapshot(actor=actor), content, rules, NOW, RNG, item_id, quantity)
        )

    assert drop(worn.id) == ["Lege den Gegenstand zuerst ab."]
    assert drop(potions.id, 3) == ["So viele hast du nicht."]
    assert drop(potions.id, 0) == ["So viele hast du nicht."]
    actor.locked_item_ids = [potions.id]
    assert drop(potions.id) == ["Dieser Gegenstand ist im Handel angeboten."]


# --- views ------------------------------------------------------------------------------------


def test_inventory_view(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(gold=12)
    actor.state = "dead"  # allowed in every state
    give(actor, "small_bag", slot="bag")
    give(actor, "healing_potion", quantity=3)

    [view] = resolve_inventory(ActionSnapshot(actor=actor), content, rules, NOW, RNG).events

    assert view.text == f"Inventar: 1/{rules.inventory.slots + 2} Plätze, Taschen 1/4, 12 Gold."
    assert [(i["name"], i["quantity"]) for i in view.data["items"]] == [
        ("Kleine Tasche", 1),
        ("Heiltrank", 3),
    ]


def test_look_lists_visible_floor_items(content: ContentRegistry, rules: Rules) -> None:
    visible = floor(quantity=2)
    snapshot = ActionSnapshot(
        actor=make_player(),
        floor_items=[visible, floor(expires_at=NOW), floor(room_id="goblin_camp")],
    )
    [event] = resolve_look(snapshot, content, rules, NOW, RNG).events
    assert [f["id"] for f in event.data["floor_items"]] == [str(visible.id)]
    assert "Am Boden: 2x Goblinohr" in event.text


# --- review follow-ups -------------------------------------------------------------------------


def test_potion_stack_lost_on_death_is_not_brought_back(
    content: ContentRegistry, rules: Rules
) -> None:
    harsh = rules.model_copy(
        update={
            "death": rules.death.model_copy(update={"item_loss_chance": 1.0, "max_items_lost": 5})
        }
    )
    actor = make_player(hp=1)
    potions = give(actor, "healing_potion", quantity=3)
    troll = make_monster("troll")
    fight(actor, troll)

    result = resolve_use_item(
        ActionSnapshot(actor=actor, monsters=[troll]), content, harsh, NOW, RNG, potions.id
    )

    assert player_update(result, actor.id)["state"] == "dead"
    assert potions.id in result.changes.inventory_delete
    upserted = {u.item.id for u in result.changes.inventory_upsert}
    assert upserted.isdisjoint(result.changes.inventory_delete)


def test_drop_announces_floor_item_and_updates_snapshot(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = make_player()
    dagger = give(actor, "rusty_dagger")
    snapshot = ActionSnapshot(actor=actor)

    result = resolve_drop(snapshot, content, rules, NOW, RNG, dagger.id)

    [created] = result.changes.floor_items_create
    assert [f.id for f in snapshot.floor_items] == [created.id]
    [room] = [e for e in result.events if e.kind == "floor.dropped"]
    assert room.data == {"floor_item_id": str(created.id), "item_id": "rusty_dagger", "quantity": 1}


def test_unequip_merges_split_piece_back_into_stack(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    stack = give(actor, "knives", quantity=2)
    worn = give(actor, "knives", slot="weapon")

    result = resolve_unequip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, worn.id)

    assert upserts(result)[stack.id].quantity == 3
    assert result.changes.inventory_delete == [worn.id]
    assert [i.id for i in actor.inventory] == [stack.id]


def test_unequip_merge_even_with_full_inventory(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    give(actor, "knives", quantity=2)
    worn = give(actor, "knives", slot="weapon")
    fill(actor, rules)
    result = resolve_unequip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, worn.id)
    assert result.changes.inventory_delete == [worn.id]


def test_unequip_does_not_merge_into_full_or_locked_stack(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = make_player()
    full = give(actor, "knives", quantity=10)
    locked = give(actor, "knives", quantity=1)
    actor.locked_item_ids = [locked.id]
    worn = give(actor, "knives", slot="weapon")

    result = resolve_unequip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, worn.id)

    assert upserts(result)[worn.id].equipped_slot is None
    assert full.id not in upserts(result) and locked.id not in upserts(result)


def test_equip_rejects_locked_item(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    dagger = give(actor, "rusty_dagger")
    actor.locked_item_ids = [dagger.id]
    result = resolve_equip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, dagger.id)
    assert actor_texts(result) == ["Dieser Gegenstand ist im Handel angeboten."]


def test_pick_up_tops_up_existing_stack_first(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    ears = give(actor, "goblin_ear", quantity=18)
    pile = floor(quantity=5)
    result = resolve_pick_up(
        ActionSnapshot(actor=actor, floor_items=[pile]), content, rules, NOW, RNG, pile.id
    )
    changed = upserts(result)
    assert changed[ears.id].quantity == 20
    assert sorted(i.quantity for i in changed.values()) == [3, 20]


def test_pick_up_quantity_bounds(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    pile = floor(quantity=2)
    snapshot = ActionSnapshot(actor=actor, floor_items=[pile])
    zero = resolve_pick_up(snapshot, content, rules, NOW, RNG, pile.id, 0)
    assert actor_texts(zero) == ["Ungültige Menge."]
    too_many = resolve_pick_up(snapshot, content, rules, NOW, RNG, pile.id, 99)
    assert [u.quantity for u in upserts(too_many).values()] == [2]
    assert too_many.changes.floor_items_delete == [pile.id]


def test_spawn_item_respawn_only_on_full_pickup_with_timer(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = make_player(room="goblin_camp")
    bread = floor(
        room_id="goblin_camp", item_id="bread", quantity=2, source="spawn", expires_at=None
    )
    partial = resolve_pick_up(
        ActionSnapshot(actor=actor, floor_items=[bread]), content, rules, NOW, RNG, bread.id, 1
    )
    assert partial.changes.item_spawns == []

    # dark_forest has no item spawn rule for goblin ears -> nothing to schedule
    actor = make_player()
    ear = floor(source="spawn", expires_at=None)
    result = resolve_pick_up(
        ActionSnapshot(actor=actor, floor_items=[ear]), content, rules, NOW, RNG, ear.id
    )
    assert result.changes.floor_items_delete == [ear.id]
    assert result.changes.item_spawns == []


def test_unequip_rejects_locked_entry(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    give(actor, "knives", quantity=2)
    worn = give(actor, "knives", slot="weapon")
    actor.locked_item_ids = [worn.id]
    result = resolve_unequip(ActionSnapshot(actor=actor), content, rules, NOW, RNG, worn.id)
    assert actor_texts(result) == ["Dieser Gegenstand ist im Handel angeboten."]
    assert result.changes.inventory_delete == []
