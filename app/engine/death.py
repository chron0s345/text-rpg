from __future__ import annotations

import random
from dataclasses import dataclass
from uuid import UUID

from app.content.schemas import DeathRules, ItemTemplate
from app.engine.inventory import InventoryItemState


@dataclass
class DeathLossResult:
    gold_lost: int
    new_gold: int
    lost_item_ids: list[UUID]
    lost_item_names: list[str]
    remaining_items: list[InventoryItemState]


def resolve_death_losses(
    items: list[InventoryItemState],
    gold: int,
    death_rules: DeathRules,
    item_templates: dict[str, ItemTemplate],
    rng: random.Random,
) -> DeathLossResult:
    """Calculates lost gold and lost items upon player death.

    Rules from ARCHITECTURE.md:
    - Gold loss % randomly chosen from death_rules.gold_loss_percent (min..max)
    - Each unequipped inventory stack: rolled against death_rules.item_loss_chance
    - Each equipped item except bags: rolled against death_rules.equipped_loss_chance
    - Items with keep_on_death == True are immune
    - At most death_rules.max_items_lost are lost
    - Bags never lost
    """
    # 1. Gold loss
    min_pct, max_pct = death_rules.gold_loss_percent
    loss_pct = rng.randint(min_pct, max_pct)
    gold_lost = int(gold * (loss_pct / 100.0))
    new_gold = max(0, gold - gold_lost)

    # 2. Eligible items for loss
    candidates: list[InventoryItemState] = []
    for it in items:
        tmpl = item_templates.get(it.item_id)
        if tmpl and tmpl.keep_on_death:
            continue
        if it.equipped_slot == "bag":
            continue

        if it.equipped_slot is not None:
            # Equipped item (non-bag)
            if rng.random() < death_rules.equipped_loss_chance:
                candidates.append(it)
        else:
            # Unequipped inventory stack
            if rng.random() < death_rules.item_loss_chance:
                candidates.append(it)

    # Cap lost items to max_items_lost
    if len(candidates) > death_rules.max_items_lost:
        rng.shuffle(candidates)
        candidates = candidates[: death_rules.max_items_lost]

    lost_ids = {it.id for it in candidates}
    lost_names: list[str] = []
    for it in candidates:
        tmpl = item_templates.get(it.item_id)
        name = tmpl.name if tmpl else it.item_id
        if it.quantity > 1:
            lost_names.append(f"{it.quantity}x {name}")
        else:
            lost_names.append(name)

    remaining_items = [it for it in items if it.id not in lost_ids]

    return DeathLossResult(
        gold_lost=gold_lost,
        new_gold=new_gold,
        lost_item_ids=list(lost_ids),
        lost_item_names=lost_names,
        remaining_items=remaining_items,
    )
