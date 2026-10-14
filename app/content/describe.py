"""Short, player-facing summaries of what an item does (German), e.g. "Waffe · +3 Angriff".

Shown next to item names in inventory, bank and shop: the description tells the story, the
summary the numbers.
"""

from __future__ import annotations

from typing import Final

from app.content.schemas import EquipSlot, Heal, ItemTemplate, StatModifier

SLOT_NAMES: Final[dict[EquipSlot, str]] = {
    "weapon": "Waffe",
    "offhand": "Schild",
    "head": "Kopf",
    "chest": "Brust",
    "legs": "Beine",
    "feet": "Füße",
    "neck": "Amulett",
    "ring": "Ring",
    "bag": "Tasche",
}
STAT_NAMES: Final = {"attack": "Angriff", "defense": "Verteidigung", "max_hp": "max. HP"}


def _signed(amount: int) -> str:
    return f"+{amount}" if amount >= 0 else f"−{-amount}"


def item_summary(item: ItemTemplate) -> str:
    parts: list[str] = []
    for effect in item.effects:
        if isinstance(effect, StatModifier):
            parts.append(f"{_signed(effect.amount)} {STAT_NAMES[effect.stat]}")
        elif isinstance(effect, Heal):
            parts.append(f"heilt {effect.amount}")
    if item.bag_slots:
        parts.append(f"+{item.bag_slots} Plätze")
    numbers = ", ".join(parts)
    if item.slot is None:
        return numbers
    return f"{SLOT_NAMES[item.slot]} · {numbers}" if numbers else SLOT_NAMES[item.slot]
