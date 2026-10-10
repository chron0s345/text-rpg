from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.content.schemas import ItemTemplate, StatModifier
from app.engine.state import InventoryItemState, PlayerSnapshot


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
) -> EffectiveStats:
    """Base values + stat modifiers of equipped items (derived, never stored)."""
    max_hp, attack, defense = base_max_hp, base_attack, base_defense

    for entry in inventory:
        if entry.equipped_slot is None:
            continue
        item = item_templates.get(entry.item_id)
        if item is None:
            continue
        for effect in item.effects:
            if isinstance(effect, StatModifier):
                if effect.stat == "attack":
                    attack += effect.amount
                elif effect.stat == "defense":
                    defense += effect.amount
                else:
                    max_hp += effect.amount

    return EffectiveStats(max_hp=max(1, max_hp), attack=max(0, attack), defense=max(0, defense))


def stats_of(player: PlayerSnapshot, item_templates: dict[str, ItemTemplate]) -> EffectiveStats:
    return calculate_effective_stats(
        player.base_max_hp,
        player.base_attack,
        player.base_defense,
        player.inventory,
        item_templates,
    )
