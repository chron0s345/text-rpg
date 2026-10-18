from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.content.schemas import ItemSet, ItemTemplate, StatModifier
from app.engine.state import ActiveBuff, InventoryItemState, PlayerSnapshot


@dataclass(frozen=True)
class EffectiveStats:
    max_hp: int
    attack: int
    defense: int


def calculate_effective_stats(
    base_max_hp: int,
    base_attack: int,
    base_defense: int,
    inventory: Sequence[InventoryItemState],
    item_templates: dict[str, ItemTemplate],
    buffs: Sequence[ActiveBuff] = (),
) -> EffectiveStats:
    """Base values + stat modifiers of equipped items + set bonuses + running buffs (derived,
    never stored)."""
    max_hp, attack, defense = base_max_hp, base_attack, base_defense
    modifiers: list[StatModifier] = []
    for entry in inventory:
        item = item_templates.get(entry.item_id)
        if entry.equipped_slot is not None and item is not None:
            modifiers += [e for e in item.effects if isinstance(e, StatModifier)]
    for item_set, count in worn_sets(inventory, item_templates):
        modifiers += active_set_effects(item_set, count)
    modifiers += [StatModifier(stat=b.stat, amount=b.amount) for b in buffs]

    for effect in modifiers:
        if effect.stat == "attack":
            attack += effect.amount
        elif effect.stat == "defense":
            defense += effect.amount
        else:
            max_hp += effect.amount

    return EffectiveStats(max_hp=max(1, max_hp), attack=max(0, attack), defense=max(0, defense))


def active_set_effects(item_set: ItemSet, pieces: int) -> list[StatModifier]:
    """All bonuses reached with this many pieces (they add up)."""
    return [e for bonus in item_set.bonuses if pieces >= bonus.pieces for e in bonus.effects]


def worn_sets(
    inventory: Sequence[InventoryItemState], item_templates: dict[str, ItemTemplate]
) -> list[tuple[ItemSet, int]]:
    """(set, equipped pieces) for every set the character wears at least one piece of."""
    worn: dict[str, tuple[ItemSet, int]] = {}
    for entry in inventory:
        item = item_templates.get(entry.item_id)
        if entry.equipped_slot is None or item is None or item.item_set is None:
            continue
        _, count = worn.get(item.item_set.id, (item.item_set, 0))
        worn[item.item_set.id] = (item.item_set, count + 1)
    return list(worn.values())


def stats_of(player: PlayerSnapshot, item_templates: dict[str, ItemTemplate]) -> EffectiveStats:
    return calculate_effective_stats(
        player.base_max_hp,
        player.base_attack,
        player.base_defense,
        player.inventory,
        item_templates,
        player.buffs,
    )
