"""Gear comparison: what an item would change if worn instead of the piece in its slot."""

from pathlib import Path
from uuid import uuid4

from app.content.loader import load_content
from app.engine.state import InventoryItemState
from app.services.gear import compare
from tests.engine.factories import make_player

CONTENT = load_content(Path("data"))


def wearing(*item_ids: str) -> list[InventoryItemState]:
    return [
        InventoryItemState(id=uuid4(), item_id=i, quantity=1, equipped_slot=CONTENT.items[i].slot)
        for i in item_ids
    ]


def test_better_worse_and_empty_slots() -> None:
    player = make_player()
    player.inventory = wearing("short_sword")  # +4 attack

    better = compare(player, "long_sword", CONTENT)  # +6
    assert better is not None
    assert (better.verdict, better.changes, better.replaces) == (
        "better",
        "+2 Angriff",
        "Kurzschwert",
    )
    worse = compare(player, "rusty_dagger", CONTENT)  # +2
    assert worse is not None and (worse.verdict, worse.changes) == ("worse", "−2 Angriff")
    empty = compare(player, "leather_cap", CONTENT)
    assert empty is not None and (empty.verdict, empty.replaces) == ("better", "")


def test_mixed_and_same() -> None:
    player = make_player()
    player.inventory = wearing("drake_shield")  # +5 defense, +15 max HP
    weaker = compare(player, "obsidian_shield", CONTENT)  # +4 defense
    assert weaker is not None and weaker.verdict == "worse"
    nacre = compare(player, "nacre_shield", CONTENT)  # +5 defense, +10 max HP
    assert nacre is not None and (nacre.verdict, nacre.changes) == ("worse", "−5 max. HP")
    same = compare(player, "drake_shield", CONTENT)
    assert same is not None and (same.verdict, same.changes) == ("same", "")
    player.inventory = wearing("valen_ring")  # +2 attack, +5 max HP
    ruby = compare(player, "ruby_ring", CONTENT)  # +3 attack
    assert ruby is not None and (ruby.verdict, ruby.changes) == (
        "mixed",
        "+1 Angriff, −5 max. HP",
    )


def test_set_bonuses_count() -> None:
    """Swapping a set piece also gains or loses the set bonus."""
    player = make_player()
    player.inventory = wearing("leather_cap", "leather_armor", "leather_leggings")
    # the fourth leather piece: +1 defense itself, plus the 4-piece bonus (+1 defense, +4 HP)
    boots = compare(player, "leather_boots", CONTENT)
    assert boots is not None and boots.changes == "+2 Verteidigung, +4 max. HP"
    # an iron helmet instead of the leather cap: +1 defense, but the 2/3-piece bonus stays
    helm = compare(player, "iron_helm", CONTENT)
    assert helm is not None and (helm.verdict, helm.replaces) == ("better", "Lederkappe")


def test_items_without_a_slot_are_not_compared() -> None:
    player = make_player()
    assert compare(player, "healing_potion", CONTENT) is None
    assert compare(player, "small_bag", CONTENT) is None
    assert compare(player, "nope", CONTENT) is None
