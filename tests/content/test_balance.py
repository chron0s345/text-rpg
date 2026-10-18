"""Balancing guard rails for all monsters, computed from the real data.

A fight is estimated without randomness: the player strikes first, the monster strikes back
after every hit it survives, damage is ``attack - defense`` (at least ``combat.min_damage``).
The player has the level of the monster and wears, per slot, the best item worth at most the
gear budget of the monster's tier: what one can afford by the time one gets there.

- Normal monsters can be beaten alone, and a fight costs a noticeable but not ruinous share
  of the HP (``NORMAL_LOSS``); rare forms cost more (``RARE_LOSS``).
- Up to level 5 a normal monster can be beaten even without any gear.
- Bosses cannot (a group or potions are needed), but a full group can.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.content.loader import load_content
from app.content.schemas import MonsterTemplate
from app.engine.gear import needed_item_ids
from app.engine.stats import active_set_effects

# (lowest level of the tier, gear budget per item in gold): from level 3 the cheap drops and
# shop pieces (leather, wooden shield, pitchfork), from 6 iron, from 11 obsidian and beyond
TIERS = [(1, 0), (3, 15), (6, 60), (11, 300)]
NORMAL_LOSS = (0.15, 0.45)  # share of max HP an even fight costs, normal monsters
RARE_LOSS = (0.25, 0.65)
BOSSES = {
    "cave_troll",
    "grave_lord",
    "sea_witch",
    "corrupted_warden",
    "spider_queen",
    "ice_drake",
    "ignaros",
    "kraken",
    "morvath",
    "wolf_king",
}

CONTENT = load_content(Path(__file__).resolve().parents[2] / "data")


def gear_budget(level: int) -> int:
    return max(budget for lowest, budget in TIERS if level >= lowest)


def gear_bonus(stat: str, budget: int) -> int:
    """Per slot the best value of ``stat`` among affordable items, plus the best set bonus the
    affordable pieces of one set reach. An overestimate: the best attack and the best defense
    of a slot may sit on different items, and set pieces are rarely the best in every slot."""
    best: dict[str, int] = {}
    for item in CONTENT.items.values():
        if item.slot is None or item.slot == "bag" or item.value > budget:
            continue
        amount = sum(e.amount for e in item.effects if e.type == "stat" and e.stat == stat)
        best[item.slot] = max(best.get(item.slot, 0), amount)
    set_bonus = 0
    for item_set in CONTENT.item_sets.values():
        pieces = sum(1 for i in item_set.items if CONTENT.items[i].value <= budget)
        effects = active_set_effects(item_set, pieces)
        set_bonus = max(set_bonus, sum(e.amount for e in effects if e.stat == stat))
    return sum(best.values()) + set_bonus


def damage_taken(monster: MonsterTemplate, geared: bool = True) -> tuple[int, int]:
    """(damage the player takes until the monster dies, the player's max HP)."""
    rules = CONTENT.rules
    level = rules.level_rule(monster.level)
    budget = gear_budget(monster.level) if geared else 0
    attack = level.attack + gear_bonus("attack", budget)
    defense = level.defense + gear_bonus("defense", budget)
    minimum = rules.combat.min_damage
    hits_needed = -(-monster.max_hp // max(minimum, attack - monster.defense))
    per_hit = max(minimum, monster.attack - defense)
    return (hits_needed - 1) * per_hit, level.max_hp + gear_bonus("max_hp", budget)


MONSTERS = sorted(CONTENT.monsters.values(), key=lambda m: (m.level, m.id))


def test_every_level_has_monsters_and_bosses_exist() -> None:
    top = max(rule.level for rule in CONTENT.rules.levels)
    assert {m.level for m in MONSTERS} == set(range(1, top + 1))
    assert BOSSES == {m.id for m in CONTENT.monsters.values() if m.boss}  # flagged in the data


@pytest.mark.parametrize("monster", MONSTERS, ids=lambda m: m.id)
def test_monsters_can_be_beaten_and_bosses_need_help(monster: MonsterTemplate) -> None:
    taken, max_hp = damage_taken(monster)
    if monster.id in BOSSES:
        assert max_hp < taken < CONTENT.rules.max_group_size * max_hp
    else:
        assert taken < max_hp
        assert taken > 0, "a monster that cannot hurt its own level is no threat"
        rare = CONTENT.base_monster_id(monster.id) != monster.id
        low, high = RARE_LOSS if rare else NORMAL_LOSS
        assert low <= taken / max_hp <= high, f"{taken}/{max_hp} HP"
        if not rare and monster.level <= 5:
            bare, bare_max = damage_taken(monster, geared=False)
            assert bare < bare_max, "early monsters stay beatable without gear"


def test_rare_variants_are_worth_the_luck() -> None:
    """A rare form is stronger than its base and rewards more: more XP and guaranteed loot."""
    variants = [m for m in CONTENT.monsters.values() if m.rare_variant is not None]
    assert len(variants) >= 5
    for base in variants:
        assert base.rare_variant is not None
        rare = CONTENT.monsters[base.rare_variant.monster_id]
        assert rare.max_hp > base.max_hp, rare.id
        assert rare.xp_reward > base.xp_reward, rare.id
        assert any(entry.chance == 1.0 for entry in rare.loot_table), rare.id
        assert base.rare_variant.chance <= 0.1, "rare means rare"


def test_lore_items_are_story_not_money() -> None:
    lore = [i for i in CONTENT.items.values() if i.icon == "scroll"]
    assert len(lore) >= 10
    assert all(not item.sellable and item.value == 0 for item in lore)


def test_every_recipe_ingredient_can_be_found() -> None:
    """Ingredients drop, are sold, lie around, are quest rewards or are crafted themselves."""
    sources = {e.item_id for m in CONTENT.monsters.values() for e in m.loot_table}
    sources |= {o.item_id for m in CONTENT.merchants.values() for o in m.sells}
    sources |= {i.item_id for r in CONTENT.rooms.values() for i in r.items}
    sources |= {i.item_id for q in CONTENT.quests.values() for i in q.reward.items}
    sources |= {r.item_id for r in CONTENT.recipes.values()}
    for recipe in CONTENT.recipes.values():
        for ingredient in recipe.ingredients:
            assert ingredient.item_id in sources, (recipe.id, ingredient.item_id)


def test_boss_trophies_are_worth_more_than_their_price() -> None:
    """What a boss drops besides gear is wanted by some recipe or quest, never mere vendor
    trash."""
    ingredients = needed_item_ids(CONTENT)
    for monster in CONTENT.monsters.values():
        if not monster.boss:
            continue
        for loot in monster.loot_table:
            item = CONTENT.items[loot.item_id]
            if item.kind == "misc" and item.sellable:
                assert loot.item_id in ingredients, (monster.id, loot.item_id)


def test_crafting_is_no_money_machine() -> None:
    """Selling what was crafted brings at most a little more than selling the ingredients
    (convenience, not profit), and nothing that can be bought crafts into a profit."""
    ratio = CONTENT.rules.merchant
    buy: dict[str, int] = {}
    for merchant in CONTENT.merchants.values():
        for offer in merchant.sells:
            price = offer.price or round(CONTENT.items[offer.item_id].value * ratio.buy_ratio)
            buy[offer.item_id] = min(buy.get(offer.item_id, price), price)

    def sell(item_id: str) -> int:
        return int(CONTENT.items[item_id].value * ratio.sell_ratio)

    for recipe in CONTENT.recipes.values():
        result = sell(recipe.item_id) * recipe.quantity
        parts = [(i.item_id, i.quantity) for i in recipe.ingredients]
        assert result <= sum(sell(i) * n for i, n in parts) + recipe.gold + 5, recipe.id
        if all(i in buy for i, _ in parts):
            assert result < sum(buy[i] * n for i, n in parts) + recipe.gold, recipe.id
