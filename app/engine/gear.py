"""Gear judgement shared by the engine and the views: what wearing an item instead of the piece in
its slot changes, which backpack pieces are upgrades (``equip_best``) and what is junk
(``sell_junk``).

Everything is computed with ``calculate_effective_stats`` on a trial inventory, so set bonuses
gained or lost count exactly as they would in the game.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from app.content.prices import sell_price
from app.content.registry import ContentRegistry
from app.content.schemas import DeliverObjective, MerchantTemplate, Rules, StatModifier
from app.engine.state import InventoryItemState, PlayerSnapshot
from app.engine.stats import EffectiveStats, calculate_effective_stats

Verdict = Literal["better", "worse", "mixed", "same"]


def _stats(
    player: PlayerSnapshot, inventory: Sequence[InventoryItemState], content: ContentRegistry
) -> EffectiveStats:
    return calculate_effective_stats(
        player.base_max_hp, player.base_attack, player.base_defense, inventory, content.items
    )


def stat_deltas(before: EffectiveStats, after: EffectiveStats) -> list[StatModifier]:
    """The stats that change, as modifiers (attack, defense, max HP; unchanged ones left out)."""
    deltas = [
        StatModifier(stat="attack", amount=after.attack - before.attack),
        StatModifier(stat="defense", amount=after.defense - before.defense),
        StatModifier(stat="max_hp", amount=after.max_hp - before.max_hp),
    ]
    return [d for d in deltas if d.amount]


def verdict_of(deltas: Sequence[StatModifier]) -> Verdict:
    up, down = any(d.amount > 0 for d in deltas), any(d.amount < 0 for d in deltas)
    return "mixed" if up and down else "better" if up else "worse" if down else "same"


@dataclass(frozen=True)
class Swap:
    deltas: list[StatModifier]
    worn: InventoryItemState | None  # the piece in that slot now; None for an empty slot

    @property
    def verdict(self) -> Verdict:
        return verdict_of(self.deltas)


def swap_for(
    player: PlayerSnapshot,
    item_id: str,
    content: ContentRegistry,
    inventory: Sequence[InventoryItemState] | None = None,
) -> Swap | None:
    """What wearing ``item_id`` instead of the piece in its slot would change. ``None`` for items
    without a slot and for bags (they add room, not stats). ``inventory`` defaults to the
    player's own (``equip_best`` passes the one it is building)."""
    item = content.items.get(item_id)
    if item is None or item.slot is None or item.slot == "bag":
        return None
    current = player.inventory if inventory is None else inventory
    worn = next((i for i in current if i.equipped_slot == item.slot), None)
    trial = [i for i in current if i is not worn]
    trial.append(
        InventoryItemState(id=uuid4(), item_id=item_id, quantity=1, equipped_slot=item.slot)
    )
    deltas = stat_deltas(_stats(player, current, content), _stats(player, trial, content))
    return Swap(deltas=deltas, worn=worn)


# --- equip_best -------------------------------------------------------------------------------


def best_upgrade(
    player: PlayerSnapshot,
    inventory: Sequence[InventoryItemState],
    content: ContentRegistry,
    skip: Collection[UUID] = (),
) -> tuple[InventoryItemState, Swap] | None:
    """The backpack piece whose swap gains the most without losing anything (a "better"
    verdict); ties go to the first in the backpack. Pieces offered in a trade and ``skip`` are
    left alone."""
    best: tuple[int, InventoryItemState, Swap] | None = None
    for entry in inventory:
        if entry.equipped_slot is not None or entry.id in player.locked_item_ids:
            continue
        if entry.id in skip:
            continue
        swap = swap_for(player, entry.item_id, content, inventory)
        if swap is None or swap.verdict != "better":
            continue
        gain = sum(d.amount for d in swap.deltas)
        if best is None or gain > best[0]:
            best = (gain, entry, swap)
    return None if best is None else (best[1], best[2])


# --- sell_junk --------------------------------------------------------------------------------


def needed_item_ids(content: ContentRegistry) -> set[str]:
    """Items some recipe or quest delivery asks for: never junk, whatever their stats."""
    needed = {i.item_id for recipe in content.recipes.values() for i in recipe.ingredients}
    needed |= {
        step.item_id
        for quest in content.quests.values()
        for step in quest.steps
        if isinstance(step, DeliverObjective)
    }
    return needed


@dataclass(frozen=True)
class JunkEntry:
    entry: InventoryItemState
    price: int  # per unit


def junk_for(
    player: PlayerSnapshot, merchant: MerchantTemplate, content: ContentRegistry, rules: Rules
) -> list[JunkEntry]:
    """Backpack stacks this merchant would buy that the character has no use for:

    - equipment no better than what is worn in its slot ("worse" or "same"; a "mixed" piece
      might still be wanted), unless it belongs to a set (pieces may complete one later);
    - misc goods (loot) that no recipe or quest delivery asks for.

    Consumables, bags, worn pieces, pieces offered in a trade and anything a recipe or quest
    needs are never junk; neither is anything the merchant pays nothing for.
    """
    needed = needed_item_ids(content)
    junk: list[JunkEntry] = []
    for entry in player.inventory:
        if entry.equipped_slot is not None or entry.id in player.locked_item_ids:
            continue
        item = content.items.get(entry.item_id)
        if item is None or item.id in needed:
            continue
        price = sell_price(item, merchant, rules)
        if not price:
            continue
        if item.kind == "misc":
            junk.append(JunkEntry(entry=entry, price=price))
        elif item.kind in ("weapon", "armor") and item.item_set is None:
            swap = swap_for(player, item.id, content)
            if swap is not None and swap.verdict in ("worse", "same"):
                junk.append(JunkEntry(entry=entry, price=price))
    return junk
