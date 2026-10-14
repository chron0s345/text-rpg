"""Login and logout (engine/presence.py)."""

import random
from uuid import uuid4

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.events import ActionResult, RoomAudience
from app.engine.presence import (
    CONFIRM_REQUIRED,
    LOGGED_IN,
    LOGGED_OUT,
    resolve_login,
    resolve_logout,
)
from app.engine.state import (
    ActionSnapshot,
    InventoryItemState,
    ParticipationSnapshot,
    PlayerSnapshot,
    QuestState,
)
from tests.engine.factories import (
    NOW,
    ROOM,
    actor_texts,
    fight,
    make_monster,
    make_player,
    player_update,
)

RNG_SEED = 3


def kinds(result: ActionResult) -> list[tuple[str, str]]:
    return [(e.kind, e.audience.kind) for e in result.events]


def test_login_puts_character_online_and_announces_it(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = make_player()
    result = resolve_login(
        ActionSnapshot(actor=actor),
        content,
        rules,
        NOW,
        random.Random(RNG_SEED),
        already_online=False,
    )
    assert player_update(result, actor.id) == {
        "player_id": actor.id,
        "online": True,
        "disconnected_at": None,
    }
    assert kinds(result) == [
        (LOGGED_IN, "actor"),
        ("player.hint", "actor"),  # a newcomer: see test_newcomers_are_pointed_to_the_intro
        ("player.entered_world", "room"),
    ]
    [room_event] = [e for e in result.events if e.audience.kind == "room"]
    assert room_event.text == "Anna betritt die Welt."
    assert room_event.exclude_player_ids == [actor.id]


def test_login_of_online_character_is_silent_for_the_room(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = make_player()
    result = resolve_login(
        ActionSnapshot(actor=actor),
        content,
        rules,
        NOW,
        random.Random(RNG_SEED),
        already_online=True,
    )
    assert kinds(result) == [(LOGGED_IN, "actor"), ("player.hint", "actor")]
    assert player_update(result, actor.id)["disconnected_at"] is None


def test_login_while_dead_points_to_the_checkpoint(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(hp=0)
    actor.state = "dead"
    result = resolve_login(
        ActionSnapshot(actor=actor),
        content,
        rules,
        NOW,
        random.Random(RNG_SEED),
        already_online=False,
    )
    assert actor_texts(result)[0] == "Willkommen, Anna. Du bist tot – erwache am Checkpoint."


def test_newcomers_are_pointed_to_the_intro(content: ContentRegistry, rules: Rules) -> None:
    def login_texts(actor: PlayerSnapshot) -> list[str]:
        result = resolve_login(
            ActionSnapshot(actor=actor),
            content,
            rules,
            NOW,
            random.Random(RNG_SEED),
            already_online=False,
        )
        return actor_texts(result)

    newcomer = make_player()
    assert login_texts(newcomer) == [
        f"Willkommen, Anna. Du bist in {content.get_room(ROOM).name}.",
        "Neu hier? Hauptmann Rowan hat in Marktplatz von Oakhaven einen ersten Auftrag für dich:"
        " „Willkommen in Oakhaven“.",
    ]
    started = make_player()
    started.quests = [QuestState(quest_id="intro_first_steps", status="active")]
    assert len(login_texts(started)) == 1
    veteran = make_player()
    veteran.level = 2
    assert len(login_texts(veteran)) == 1


def test_logout_returns_to_town(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(gold=50)
    result = resolve_logout(
        ActionSnapshot(actor=actor), content, rules, NOW, random.Random(RNG_SEED), False
    )
    assert player_update(result, actor.id) == {
        "player_id": actor.id,
        "current_room_id": rules.town_room_id,
        "online": False,
        "disconnected_at": None,
    }
    assert kinds(result) == [(LOGGED_OUT, "actor"), ("player.left_world", "room")]
    # the room the character was in sees it leave
    assert [e.audience.room_id for e in result.events if isinstance(e.audience, RoomAudience)] == [
        ROOM
    ]


def test_logout_in_town_stays_in_town(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room=rules.town_room_id)
    result = resolve_logout(
        ActionSnapshot(actor=actor), content, rules, NOW, random.Random(RNG_SEED), False
    )
    assert "current_room_id" not in player_update(result, actor.id)


def test_logout_in_combat_needs_confirmation(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player()
    fight(actor, make_monster())
    result = resolve_logout(
        ActionSnapshot(actor=actor), content, rules, NOW, random.Random(RNG_SEED), False
    )
    assert kinds(result) == [(CONFIRM_REQUIRED, "actor")]
    assert result.changes.players == []


def test_confirmed_logout_in_combat_kills_with_losses(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = make_player(gold=100)
    monster = make_monster()
    fight(actor, monster)
    actor.inventory = [InventoryItemState(id=uuid4(), item_id="goblin_ear", quantity=3)]
    participation = ParticipationSnapshot(
        monster_instance_id=monster.id, player_id=actor.id, party_key=actor.party_key
    )
    snapshot = ActionSnapshot(actor=actor, monsters=[monster], participations=[participation])

    result = resolve_logout(snapshot, content, rules, NOW, random.Random(RNG_SEED), True)

    update = player_update(result, actor.id)
    assert update["state"] == "dead"
    assert update["hp"] == 0
    assert update["online"] is False
    assert update["gold"] < 100  # 5-20 % gold loss
    assert "current_room_id" not in update  # dies where it stood
    assert [k for k, _ in kinds(result)][-2:] == [LOGGED_OUT, "player.left_world"]
    assert "player.died" in [k for k, _ in kinds(result)]
    # the fight is over for this character
    assert result.changes.participations_delete or result.changes.participations_clear_monster_ids


def test_dead_character_logs_out_where_it_lies(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(hp=0)
    actor.state = "dead"
    result = resolve_logout(
        ActionSnapshot(actor=actor), content, rules, NOW, random.Random(RNG_SEED), False
    )
    assert player_update(result, actor.id) == {
        "player_id": actor.id,
        "online": False,
        "disconnected_at": None,
    }
