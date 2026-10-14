"""Short, player-facing summaries of what an item does (German), e.g. "Waffe · +3 Angriff".

Shown next to item names in inventory, bank and shop: the description tells the story, the
summary the numbers.
"""

from __future__ import annotations

from typing import Final

from app.content.schemas import EquipSlot, Heal, ItemSet, ItemTemplate, StatModifier

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
    summary = f"{SLOT_NAMES[item.slot]} · {numbers}" if numbers else SLOT_NAMES[item.slot]
    return summary + (f" · Set „{item.item_set.name}“" if item.item_set else "")


def modifiers_text(effects: list[StatModifier], combine: bool = False) -> str:
    """E.g. "+1 Verteidigung, +4 max. HP"; ``combine`` adds up modifiers of the same stat."""
    if combine:
        totals: dict[str, int] = {}
        for e in effects:
            totals[e.stat] = totals.get(e.stat, 0) + e.amount
        return ", ".join(
            f"{_signed(totals[stat])} {name}" for stat, name in STAT_NAMES.items() if stat in totals
        )
    return ", ".join(f"{_signed(e.amount)} {STAT_NAMES[e.stat]}" for e in effects)


def set_bonuses_text(item_set: ItemSet) -> str:
    """E.g. "2 Teile: +1 Verteidigung; 4 Teile: +2 Verteidigung, +20 max. HP"."""
    return "; ".join(
        f"{bonus.pieces} Teile: {modifiers_text(bonus.effects)}"
        for bonus in sorted(item_set.bonuses, key=lambda b: b.pieces)
    )
