"""How an item would change the character if worn instead of what sits in its slot now.

Computed with the engine's own ``calculate_effective_stats`` on a trial inventory, so set
bonuses gained or lost count exactly as they would in the game.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from app.content.describe import modifiers_text
from app.content.registry import ContentRegistry
from app.content.schemas import StatModifier
from app.engine.state import InventoryItemState, PlayerSnapshot
from app.engine.stats import calculate_effective_stats, stats_of

Verdict = Literal["better", "worse", "mixed", "same"]


@dataclass(frozen=True)
class Comparison:
    verdict: Verdict
    changes: str  # e.g. "+2 Angriff, −1 Verteidigung"; "" when nothing changes
    replaces: str  # name of the piece worn in that slot now; "" for an empty slot


def compare(player: PlayerSnapshot, item_id: str, content: ContentRegistry) -> Comparison | None:
    """``None`` for items that are not worn in a slot (consumables, misc, bags)."""
    item = content.items.get(item_id)
    if item is None or item.slot is None or item.slot == "bag":
        return None
    worn = next((i for i in player.inventory if i.equipped_slot == item.slot), None)
    trial = [i for i in player.inventory if i is not worn]
    trial.append(
        InventoryItemState(id=uuid4(), item_id=item_id, quantity=1, equipped_slot=item.slot)
    )
    before = stats_of(player, content.items)
    after = calculate_effective_stats(
        player.base_max_hp, player.base_attack, player.base_defense, trial, content.items
    )
    deltas = [
        StatModifier(stat="attack", amount=after.attack - before.attack),
        StatModifier(stat="defense", amount=after.defense - before.defense),
        StatModifier(stat="max_hp", amount=after.max_hp - before.max_hp),
    ]
    changed = [d for d in deltas if d.amount]
    up, down = any(d.amount > 0 for d in changed), any(d.amount < 0 for d in changed)
    verdict: Verdict = "mixed" if up and down else "better" if up else "worse" if down else "same"
    return Comparison(
        verdict=verdict,
        changes=modifiers_text(changed),
        replaces=content.items[worn.item_id].name if worn is not None else "",
    )
