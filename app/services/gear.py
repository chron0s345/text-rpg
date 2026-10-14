"""How an item would change the character if worn instead of what sits in its slot now, for
display. The judgement itself lives in ``app.engine.gear`` (``equip_best`` and ``sell_junk``
use the same one)."""

from __future__ import annotations

from dataclasses import dataclass

from app.content.describe import modifiers_text
from app.content.registry import ContentRegistry
from app.engine.gear import Verdict, swap_for
from app.engine.state import PlayerSnapshot


@dataclass(frozen=True)
class Comparison:
    verdict: Verdict
    changes: str  # e.g. "+2 Angriff, −1 Verteidigung"; "" when nothing changes
    replaces: str  # name of the piece worn in that slot now; "" for an empty slot


def compare(player: PlayerSnapshot, item_id: str, content: ContentRegistry) -> Comparison | None:
    """``None`` for items that are not worn in a slot (consumables, misc, bags)."""
    swap = swap_for(player, item_id, content)
    if swap is None:
        return None
    return Comparison(
        verdict=swap.verdict,
        changes=modifiers_text(swap.deltas),
        replaces=content.items[swap.worn.item_id].name if swap.worn is not None else "",
    )
