"""Short item summaries for the UI ("Waffe · +3 Angriff", "heilt 15")."""

from __future__ import annotations

from app.content.describe import item_summary
from app.content.schemas import Heal, ItemTemplate, StatModifier


def item(**fields: object) -> ItemTemplate:
    return ItemTemplate.model_validate({"id": "x", "name": "X", "description": "", **fields})


def test_equipment_shows_slot_and_stats() -> None:
    sword = item(kind="weapon", slot="weapon", effects=[StatModifier(stat="attack", amount=3)])
    assert item_summary(sword) == "Waffe · +3 Angriff"
    amulet = item(
        kind="armor",
        slot="neck",
        effects=[
            StatModifier(stat="max_hp", amount=20),
            StatModifier(stat="defense", amount=1),
            StatModifier(stat="attack", amount=-1),
        ],
    )
    assert item_summary(amulet) == "Amulett · +20 max. HP, +1 Verteidigung, −1 Angriff"
    assert item_summary(item(kind="armor", slot="offhand")) == "Schild"


def test_bags_consumables_and_misc() -> None:
    assert item_summary(item(kind="bag", slot="bag", bag_slots=4)) == "Tasche · +4 Plätze"
    potion = item(kind="consumable", stackable=True, max_stack=5, effects=[Heal(amount=15)])
    assert item_summary(potion) == "heilt 15"
    assert item_summary(item(kind="misc")) == ""


def test_set_pieces_and_bonus_texts() -> None:
    from pathlib import Path

    from app.content.describe import modifiers_text, set_bonuses_text
    from app.content.loader import load_content

    leather = load_content(Path("data")).items["leather_cap"]
    assert item_summary(leather) == "Kopf · +1 Verteidigung · Set „Jägerleder“"
    assert leather.item_set is not None
    assert set_bonuses_text(leather.item_set) == (
        "2 Teile: +4 max. HP; 4 Teile: +1 Verteidigung, +4 max. HP"
    )
    effects = [e for b in leather.item_set.bonuses for e in b.effects]
    assert modifiers_text(effects, combine=True) == "+1 Verteidigung, +8 max. HP"
