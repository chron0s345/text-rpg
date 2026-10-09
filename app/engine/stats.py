from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.content.schemas import ItemTemplate, StatModifier


@dataclass(frozen=True)
class EffectiveStats:
    max_hp: int
    attack: int
    defense: int


def calculate_effective_stats(
    base_max_hp: int,
    base_attack: int,
    base_defense: int,
    equipped_items: Sequence[tuple[str, str | None]],  # list of (item_id, equipped_slot)
    item_templates: dict[str, ItemTemplate],
) -> EffectiveStats:
    """Calculates player effective stats by combining base stats with equipped item modifiers."""
    max_hp = base_max_hp
    attack = base_attack
    defense = base_defense

    for item_id, slot in equipped_items:
        if slot is None:
            continue
        item = item_templates.get(item_id)
        if item is None:
            continue

        for effect in item.effects:
            if isinstance(effect, StatModifier):
                if effect.stat == "attack":
                    attack += effect.amount
                elif effect.stat == "defense":
                    defense += effect.amount
                elif effect.stat == "max_hp":
                    max_hp += effect.amount

    return EffectiveStats(
        max_hp=max(1, max_hp),
        attack=max(0, attack),
        defense=max(0, defense),
    )
