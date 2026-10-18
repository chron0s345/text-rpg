import random
from datetime import timedelta
from uuid import uuid4

import pytest

from app.content.registry import ContentRegistry
from app.content.schemas import PlayerState, Rules
from app.engine.actions import (
    resolve_group_say,
    resolve_inspect,
    resolve_look,
    resolve_respawn,
    resolve_say,
    resolve_whisper,
)
from app.engine.events import GroupAudience, PlayersAudience, RoomAudience
from app.engine.permissions import ALLOWED_STATES, check_state, is_allowed
from app.engine.state import ActionSnapshot, InventoryItemState, PlayerRef
from tests.engine.factories import (
    NOW,
    ROOM,
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
        ("travel", {"exploring"}),
        ("attack", {"exploring", "in_combat"}),
        ("flee", {"in_combat"}),
        ("use_item", {"exploring", "in_combat"}),
        ("equip", {"exploring"}),
        ("equip_best", {"exploring"}),
        ("pick_up", {"exploring"}),
        ("say", {"exploring", "in_combat"}),
        ("group_say", {"exploring", "in_combat", "dead"}),
        ("whisper", {"exploring", "in_combat", "dead"}),
        ("group_invite", {"exploring", "in_combat"}),
        ("group_leave", {"exploring", "in_combat", "dead"}),
        ("group_kick", {"exploring"}),
        ("trade_request", {"exploring"}),
        ("bank_deposit", {"exploring"}),
        ("bank_deposit_materials", {"exploring"}),
        ("buy", {"exploring"}),
        ("respawn", {"dead"}),
        ("logout", {"exploring", "in_combat", "dead"}),
        ("look", {"exploring", "in_combat", "dead"}),
        ("rest", {"exploring"}),
        ("unequip", {"exploring"}),
        ("drop", {"exploring"}),
        ("group_accept", {"exploring", "in_combat"}),
        ("group_decline", {"exploring", "in_combat", "dead"}),
        ("trade_accept", {"exploring"}),
        ("trade_offer_item", {"exploring"}),
        ("trade_offer_gold", {"exploring"}),
        ("trade_remove_item", {"exploring"}),
        ("trade_confirm", {"exploring"}),
        ("trade_cancel", {"exploring"}),
        ("bank_withdraw", {"exploring"}),
        ("sell", {"exploring"}),
        ("sell_junk", {"exploring"}),
        ("inventory", {"exploring", "in_combat", "dead"}),
        ("quest_accept", {"exploring"}),
        ("quest_turn_in", {"exploring"}),
        ("quest_abandon", {"exploring", "in_combat", "dead"}),
        ("craft", {"exploring"}),
        ("inspect", {"exploring", "in_combat", "dead"}),
        ("set_title", {"exploring", "in_combat", "dead"}),
    ],
)
def test_state_table_matches_architecture(action: str, allowed: set[str]) -> None:
    states: list[PlayerState] = ["exploring", "in_combat", "dead"]
    for state in states:
        assert is_allowed(action, state) is (state in allowed)


def test_every_action_type_has_an_entry() -> None:
    # every row of the table above is checked in test_state_table_matches_architecture
    assert len(ALLOWED_STATES) == 41


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


# --- group chat & whispers -----------------------------------------------------------------


def test_group_say_reaches_the_group_wherever_it_is(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    actor.group_id = uuid4()
    actor.state = "dead"  # the dead may still talk to their group

    result = resolve_group_say(
        ActionSnapshot(actor=actor), content, rules, NOW, RNG, " auf  geht's "
    )

    assert actor_texts(result) == ["[Gruppe] Du: auf geht's"]
    [group] = [e for e in result.events if e.audience.kind == "group"]
    assert group.audience == GroupAudience(group_id=actor.group_id)
    assert (group.kind, group.text) == ("chat.group", "[Gruppe] Anna: auf geht's")
    assert group.exclude_player_ids == [actor.id]
    assert result.changes.players == []


def test_group_say_needs_a_group_and_words(content: ContentRegistry, rules: Rules) -> None:
    lonely = ActionSnapshot(actor=make_player())
    assert actor_texts(resolve_group_say(lonely, content, rules, NOW, RNG, "hallo")) == [
        "Du bist in keiner Gruppe."
    ]
    grouped = make_player()
    grouped.group_id = uuid4()
    assert actor_texts(
        resolve_group_say(ActionSnapshot(actor=grouped), content, rules, NOW, RNG, "  ")
    ) == ["Was möchtest du sagen?"]


def test_whisper_reaches_only_the_recipient(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    ben = PlayerRef(id=uuid4(), name="Ben")

    result = resolve_whisper(ActionSnapshot(actor=actor), content, rules, NOW, RNG, ben, "psst <b>")

    assert actor_texts(result) == ["Du flüsterst Ben zu: psst <b>"]
    [whisper] = [e for e in result.events if e.audience.kind == "players"]
    assert whisper.audience == PlayersAudience(player_ids=[ben.id])
    assert (whisper.kind, whisper.text) == ("chat.whisper", "Anna flüstert dir zu: psst <b>")
    assert whisper.data["from_name"] == "Anna"  # for replying


def test_whisper_rejections(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    snapshot = ActionSnapshot(actor=actor)
    ben = PlayerRef(id=uuid4(), name="Ben")
    myself = PlayerRef(id=actor.id, name=actor.name)

    def answer(recipient: PlayerRef | None, text: str) -> list[str]:
        return actor_texts(resolve_whisper(snapshot, content, rules, NOW, RNG, recipient, text))

    assert answer(None, "hallo") == ["Niemand mit diesem Namen ist gerade im Spiel."]
    assert answer(myself, "hallo") == ["Du murmelst etwas vor dich hin."]
    assert answer(ben, " ") == ["Was möchtest du flüstern?"]


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
    rooms = {
        (e.kind, e.audience.room_id) for e in result.events if isinstance(e.audience, RoomAudience)
    }
    # the checkpoint room sees her wake up, the room she died in sees her vanish
    assert rooms == {("player.respawned", "town_square"), ("player.left", ROOM)}


def test_respawn_only_when_dead(content: ContentRegistry, rules: Rules) -> None:
    fighting = make_player()
    fight(fighting, make_monster())
    for player in (make_player(), fighting):
        result = resolve_respawn(ActionSnapshot(actor=player), content, rules, NOW, RNG)
        assert actor_texts(result) == ["Du lebst noch."]
        assert result.changes.players == []


def test_look_never_shows_other_rooms(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    elsewhere = make_player("Ben", room="goblin_camp")
    far_monster = make_monster()
    far_monster.room_id = "goblin_camp"
    [event] = resolve_look(
        ActionSnapshot(actor=actor, room_players=[elsewhere], monsters=[far_monster]),
        content,
        rules,
        NOW,
        RNG,
    ).events
    assert event.data["players"] == []
    assert event.data["monsters"] == []


def test_look_names_exits_in_german(content: ContentRegistry, rules: Rules) -> None:
    [event] = resolve_look(ActionSnapshot(actor=make_player()), content, rules, NOW, RNG).events
    assert "Ausgänge: Norden, Osten" in event.text


def test_respawn_updates_group_panel(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(hp=0, group_id=uuid4())
    actor.state = "dead"
    result = resolve_respawn(ActionSnapshot(actor=actor), content, rules, NOW, RNG)
    group = {e.kind: e.data for e in result.events if isinstance(e.audience, GroupAudience)}
    assert group["group.member_hp"]["hp"] == 20
    assert group["group.member_moved"]["room_id"] == "town_square"


# --- inspect --------------------------------------------------------------------------------


def test_inspect_tells_what_an_item_is_and_does(content: ContentRegistry, rules: Rules) -> None:
    dead = make_player()
    dead.state = "dead"  # reading is always possible
    result = resolve_inspect(ActionSnapshot(actor=dead), content, rules, NOW, RNG, "short_sword")

    [line] = actor_texts(result)
    sword = content.get_item("short_sword")
    assert line == f"{sword.name}: {sword.description} (Waffe · +4 Angriff)"
    assert result.events[0].kind == "item.inspected"
    assert result.changes.players == []


def test_inspect_without_numbers_and_unknown_items(content: ContentRegistry, rules: Rules) -> None:
    snapshot = ActionSnapshot(actor=make_player())
    ear = content.get_item("goblin_ear")
    assert actor_texts(resolve_inspect(snapshot, content, rules, NOW, RNG, "goblin_ear")) == [
        f"{ear.name}: {ear.description}"
    ]
    assert actor_texts(resolve_inspect(snapshot, content, rules, NOW, RNG, "nope")) == [
        "So etwas kennst du nicht."
    ]
