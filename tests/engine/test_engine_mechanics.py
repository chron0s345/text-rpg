import random
from datetime import datetime
from uuid import uuid4

from app.content.schemas import (
    DeathRules,
    ItemTemplate,
    MonsterTemplate,
    Regeneration,
    StatModifier,
)
from app.engine.death import resolve_death_losses
from app.engine.inventory import (
    InventoryItemState,
    add_item_to_inventory,
    calculate_inventory_capacity,
    can_unequip_bag,
    get_occupied_slots,
)
from app.engine.regeneration import calculate_current_monster_hp
from app.engine.stats import calculate_effective_stats


def test_effective_stats_calculation() -> None:
    dagger = ItemTemplate(
        id="dagger",
        name="Dolch",
        description="",
        kind="weapon",
        slot="weapon",
        effects=[StatModifier(stat="attack", amount=5)],
    )
    armor = ItemTemplate(
        id="armor",
        name="Rüstung",
        description="",
        kind="armor",
        slot="chest",
        effects=[StatModifier(stat="defense", amount=3)],
    )
    templates = {"dagger": dagger, "armor": armor}

    stats = calculate_effective_stats(
        base_max_hp=20,
        base_attack=5,
        base_defense=2,
        equipped_items=[("dagger", "weapon"), ("armor", "chest")],
        item_templates=templates,
    )
    assert stats.max_hp == 20
    assert stats.attack == 10
    assert stats.defense == 5


def test_monster_regeneration() -> None:
    template = MonsterTemplate(
        id="troll",
        name="Troll",
        max_hp=50,
        attack=10,
        defense=5,
        xp_reward=50,
        respawn_seconds=100,
        regen=Regeneration(delay_seconds=10, hp_per_second=2.0),
    )
    now = datetime(2026, 1, 1, 12, 0, 30)

    # Within delay (only 5s elapsed) -> no regen
    last_combat = datetime(2026, 1, 1, 12, 0, 25)
    hp = calculate_current_monster_hp(template, stored_hp=20, last_combat_at=last_combat, now=now)
    assert hp == 20

    # 20s elapsed -> 10s effective delay -> 10s * 2 hp/s = +20 HP -> 20 + 20 = 40 HP
    last_combat_earlier = datetime(2026, 1, 1, 12, 0, 10)
    hp2 = calculate_current_monster_hp(
        template, stored_hp=20, last_combat_at=last_combat_earlier, now=now
    )
    assert hp2 == 40


def test_inventory_stacking_and_capacity() -> None:
    potion = ItemTemplate(
        id="potion",
        name="Trank",
        description="",
        kind="consumable",
        stackable=True,
        max_stack=5,
    )
    bag = ItemTemplate(
        id="bag",
        name="Tasche",
        description="",
        kind="bag",
        slot="bag",
        bag_slots=4,
    )
    templates = {"potion": potion, "bag": bag}

    items: list[InventoryItemState] = [
        InventoryItemState(id=uuid4(), item_id="bag", quantity=1, equipped_slot="bag"),
        InventoryItemState(id=uuid4(), item_id="potion", quantity=3, equipped_slot=None),
    ]

    # Capacity = 10 base + 4 from bag = 14
    cap = calculate_inventory_capacity(10, items, templates)
    assert cap == 14
    assert get_occupied_slots(items) == 1

    # Adding 4 potions: 2 fit into existing stack (up to 5), 2 go into a new stack
    added = add_item_to_inventory(items, potion, 4, cap)
    assert added == 4
    assert get_occupied_slots(items) == 2
    assert items[1].quantity == 5  # filled
    assert items[2].quantity == 2  # new stack


def test_can_unequip_bag_check() -> None:
    bag = ItemTemplate(
        id="bag",
        name="Tasche",
        description="",
        kind="bag",
        slot="bag",
        bag_slots=2,
    )
    templates = {"bag": bag}

    # Base capacity 2, with bag 4. If 2 items in inventory, unequipped bag needs 1 slot,
    # but capacity without bag is 2 -> 2 + 1 = 3 slots > 2 -> Cannot unequip bag!
    items = [
        InventoryItemState(id=uuid4(), item_id="bag", quantity=1, equipped_slot="bag"),
        InventoryItemState(id=uuid4(), item_id="item1", quantity=1, equipped_slot=None),
        InventoryItemState(id=uuid4(), item_id="item2", quantity=1, equipped_slot=None),
    ]
    assert can_unequip_bag(items, bag, base_slots=2, item_templates=templates) is False


def test_death_loss_resolution() -> None:
    rules = DeathRules(
        gold_loss_percent=(10, 10),
        item_loss_chance=1.0,
        equipped_loss_chance=1.0,
        max_items_lost=2,
    )
    keep_item = ItemTemplate(
        id="quest",
        name="Quest Ring",
        description="",
        kind="misc",
        keep_on_death=True,
    )
    sword = ItemTemplate(
        id="sword",
        name="Schwert",
        description="",
        kind="weapon",
        slot="weapon",
    )
    bag = ItemTemplate(
        id="bag",
        name="Rucksack",
        description="",
        kind="bag",
        slot="bag",
        bag_slots=4,
    )
    templates = {"quest": keep_item, "sword": sword, "bag": bag}

    items = [
        InventoryItemState(id=uuid4(), item_id="quest", quantity=1, equipped_slot=None),
        InventoryItemState(id=uuid4(), item_id="sword", quantity=1, equipped_slot="weapon"),
        InventoryItemState(id=uuid4(), item_id="bag", quantity=1, equipped_slot="bag"),
    ]

    rng = random.Random(42)
    res = resolve_death_losses(
        items, gold=100, death_rules=rules, item_templates=templates, rng=rng
    )

    assert res.gold_lost == 10
    assert res.new_gold == 90
    # Quest item and bag are never lost
    assert len(res.lost_item_ids) == 1
    assert "Schwert" in res.lost_item_names[0]
