import random
from datetime import timedelta
from uuid import uuid4

import pytest

from app.content.registry import ContentRegistry
from app.content.schemas import PlayerState, Rules
from app.engine.actions import resolve_look, resolve_respawn, resolve_say
from app.engine.permissions import ALLOWED_STATES, check_state, is_allowed
from app.engine.state import ActionSnapshot, InventoryItemState
from tests.engine.factories import (
    NOW,
    actor_texts,
    fight,
    make_monster,
    make_player,
    player_update,
)

RNG = random.Random(1)


# --- permissions -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "allowed"),
    [
        ("move", {"exploring"}),
        ("attack", {"exploring", "in_combat"}),
        ("flee", {"in_combat"}),
        ("use_item", {"exploring", "in_combat"}),
        ("equip", {"exploring"}),
        ("pick_up", {"exploring"}),
        ("say", {"exploring", "in_combat"}),
        ("group_invite", {"exploring", "in_combat"}),
        ("group_leave", {"exploring", "in_combat", "dead"}),
        ("group_kick", {"exploring"}),
        ("trade_request", {"exploring"}),
        ("bank_deposit", {"exploring"}),
        ("buy", {"exploring"}),
        ("respawn", {"dead"}),
        ("logout", {"exploring", "in_combat", "dead"}),
        ("look", {"exploring", "in_combat", "dead"}),
    ],
)
def test_state_table_matches_architecture(action: str, allowed: set[str]) -> None:
    states: list[PlayerState] = ["exploring", "in_combat", "dead"]
    for state in states:
        assert is_allowed(action, state) is (state in allowed)


def test_every_action_type_has_an_entry() -> None:
    assert len(ALLOWED_STATES) == 28


def test_check_state_messages() -> None:
    player = make_player()
    assert check_state("look", player) is None
    respawn = check_state("respawn", player)
    assert respawn is not None and respawn.events[0].text == "Du lebst noch."


# --- say -----------------------------------------------------------------------------------


def test_say_reaches_room_without_actor(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    result = resolve_say(ActionSnapshot(actor=actor), content, rules, NOW, RNG, "  Hallo\n  Welt ")

    assert actor_texts(result) == ["Du sagst: Hallo Welt"]
    [room] = [e for e in result.events if e.audience.kind == "room"]
    assert room.text == "Anna sagt: Hallo Welt"
    assert room.exclude_player_ids == [actor.id]
    assert result.changes.players == []


def test_say_keeps_markup_as_plain_text(content: ContentRegistry, rules: Rules) -> None:
    result = resolve_say(
        ActionSnapshot(actor=make_player()), content, rules, NOW, RNG, "<script>x</script>"
    )
    # escaping happens at render time, the engine must not mangle or interpret it
    assert result.events[0].data["message"] == "<script>x</script>"


def test_say_rejects_blank_and_dead(content: ContentRegistry, rules: Rules) -> None:
    assert actor_texts(
        resolve_say(ActionSnapshot(actor=make_player()), content, rules, NOW, RNG, " \n ")
    ) == ["Was möchtest du sagen?"]
    dead = make_player()
    dead.state = "dead"
    assert actor_texts(resolve_say(ActionSnapshot(actor=dead), content, rules, NOW, RNG, "hi")) == [
        "Du bist tot."
    ]


# --- look ------------------------------------------------------------------------------------


def test_look_lists_room_contents(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    other = make_player("Ben")
    troll = make_monster("troll", hp=2, last_combat_at=NOW - timedelta(seconds=15))
    dead = make_monster(alive=False)

    result = resolve_look(
        ActionSnapshot(actor=actor, room_players=[other], monsters=[troll, dead]),
        content,
        rules,
        NOW,
        RNG,
    )

    [event] = result.events
    assert event.kind == "room.look"
    assert event.data["name"] == "Dunkler Wald"
    # 15 s since contact, 10 s delay, 1 HP/s -> 2 + 5 = 7 (display only)
    assert event.data["monsters"] == [{"id": str(troll.id), "name": "Troll", "hp": 7, "max_hp": 10}]
    assert [p["name"] for p in event.data["players"]] == ["Ben"]
    assert result.changes.monsters == []
    assert "Gegner: Troll (7 HP)" in event.text


def test_look_shows_merchants_and_works_when_dead(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room="town_market")
    actor.state = "dead"
    [event] = resolve_look(ActionSnapshot(actor=actor), content, rules, NOW, RNG).events
    assert event.data["merchants"] == [{"id": "alchemist", "name": "Alchemistin Elira"}]


# --- respawn -----------------------------------------------------------------------------------


def test_respawn_at_checkpoint_with_full_hp(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(hp=0)
    actor.state = "dead"
    actor.inventory = [
        InventoryItemState(id=uuid4(), item_id="amulet", quantity=1, equipped_slot="head")
    ]

    result = resolve_respawn(ActionSnapshot(actor=actor), content, rules, NOW, RNG)

    assert player_update(result, actor.id) == {
        "player_id": actor.id,
        "hp": 25,  # base 20 + amulet 5
        "state": "exploring",
        "combat_target_id": None,
        "current_room_id": "town_square",
    }
    assert actor_texts(result) == ["Du erwachst in Marktplatz von Oakhaven."]
    [room] = [e for e in result.events if e.audience.kind == "room"]
    assert room.audience.room_id == "town_square"  # type: ignore[union-attr]


def test_respawn_only_when_dead(content: ContentRegistry, rules: Rules) -> None:
    fighting = make_player()
    fight(fighting, make_monster())
    for player in (make_player(), fighting):
        result = resolve_respawn(ActionSnapshot(actor=player), content, rules, NOW, RNG)
        assert actor_texts(result) == ["Du lebst noch."]
        assert result.changes.players == []
