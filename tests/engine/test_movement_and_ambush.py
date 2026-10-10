import random
from datetime import timedelta
from typing import Any
from uuid import uuid4

from app.content.registry import ContentRegistry
from app.content.schemas import Direction, PlayerState, Rules
from app.engine.ambush import resolve_idle_ambush
from app.engine.events import ActionResult, PlayersAudience, RoomAudience
from app.engine.movement import resolve_move
from app.engine.state import ActionSnapshot, MonsterSnapshot, ParticipationSnapshot
from tests.engine.factories import (
    NOW,
    actor_texts,
    fight,
    make_monster,
    make_player,
    monster_update,
    player_update,
)


def move(
    snapshot: ActionSnapshot, content: ContentRegistry, rules: Rules, direction: Direction
) -> ActionResult:
    return resolve_move(snapshot, content, rules, NOW, random.Random(1), direction)


def ambusher(**fields: Any) -> MonsterSnapshot:
    return make_monster("ambusher", **fields)


# --- move ------------------------------------------------------------------------------


def test_move_changes_room_and_notifies_both_rooms(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room="forest_gate")

    result = move(ActionSnapshot(actor=actor), content, rules, "south")

    assert player_update(result, actor.id) == {
        "player_id": actor.id,
        "current_room_id": "dark_forest",
    }
    rooms = {
        (e.kind, e.audience.room_id) for e in result.events if isinstance(e.audience, RoomAudience)
    }
    assert rooms == {("move.leave", "forest_gate"), ("move.enter", "dark_forest")}
    assert all(actor.id in e.exclude_player_ids for e in result.events if e.audience.kind == "room")
    assert actor_texts(result) == ["Du gehst nach Süden: Dunkler Wald."]


def test_move_without_exit_is_rejected(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room="forest_gate")
    result = move(ActionSnapshot(actor=actor), content, rules, "east")
    assert result.changes.players == []
    assert actor_texts(result) == ["Es gibt keinen Weg nach Osten."]


def test_move_not_allowed_in_combat_or_dead(content: ContentRegistry, rules: Rules) -> None:
    fighting = make_player(room="forest_gate")
    fight(fighting, make_monster())
    assert actor_texts(move(ActionSnapshot(actor=fighting), content, rules, "south")) == [
        "Das geht nicht im Kampf."
    ]
    dead = make_player(room="forest_gate")
    dead.state = "dead"
    assert actor_texts(move(ActionSnapshot(actor=dead), content, rules, "south")) == [
        "Du bist tot."
    ]


def test_entering_checkpoint_binds_it(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room="forest_gate")
    actor.checkpoint_room_id = "somewhere_else"

    result = move(ActionSnapshot(actor=actor), content, rules, "north")

    assert player_update(result, actor.id)["checkpoint_room_id"] == "town_square"
    assert "Du fühlst dich mit diesem Ort verbunden." in actor_texts(result)


def test_entering_already_bound_checkpoint_is_quiet(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room="forest_gate")  # checkpoint is town_square already
    result = move(ActionSnapshot(actor=actor), content, rules, "north")
    assert "checkpoint_room_id" not in player_update(result, actor.id)
    assert "Du fühlst dich mit diesem Ort verbunden." not in actor_texts(result)


# --- ambush on entering ------------------------------------------------------------------


def test_ambush_on_enter_strikes_first_and_tags(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room="forest_gate")
    monster = ambusher(hp=4, last_combat_at=NOW - timedelta(seconds=5))

    result = move(ActionSnapshot(actor=actor, monsters=[monster]), content, rules, "south")

    update = player_update(result, actor.id)
    assert update["state"] == "in_combat"
    assert update["combat_target_id"] == monster.id
    assert update["hp"] == 17  # attack 5 - defense 2
    assert monster_update(result, monster.id) == {
        "monster_instance_id": monster.id,
        "hp": 4,  # dummy has no regeneration
        "last_combat_at": NOW,
        "tagged_by_party": actor.party_key,
    }
    [participation] = result.changes.participations_upsert
    assert (participation.player_id, participation.damage_dealt) == (actor.id, 0)
    assert "Lauerer überfällt dich!" in actor_texts(result)
    assert any(e.kind == "ambush" and e.audience.kind == "room" for e in result.events)


def test_ambush_notifies_group(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room="forest_gate", group_id=uuid4())
    result = move(ActionSnapshot(actor=actor, monsters=[ambusher()]), content, rules, "south")
    assert any(e.kind == "group.member_attacked" for e in result.events)


def test_no_ambush_by_monster_already_fighting(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room="forest_gate")
    other = make_player("Ben")
    monster = ambusher()
    fight(other, monster)
    snapshot = ActionSnapshot(
        actor=actor,
        room_players=[other],
        monsters=[monster],
        participations=[
            ParticipationSnapshot(
                monster_instance_id=monster.id, player_id=other.id, party_key=other.party_key
            )
        ],
    )
    result = move(snapshot, content, rules, "south")
    assert player_update(result, actor.id) == {
        "player_id": actor.id,
        "current_room_id": "dark_forest",
    }


def test_no_ambush_against_much_stronger_player(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room="forest_gate")
    actor.level = 4  # monster level 1, max_level_difference 2 -> 3 > 2
    result = move(ActionSnapshot(actor=actor, monsters=[ambusher()]), content, rules, "south")
    assert "state" not in player_update(result, actor.id)

    actor = make_player(room="forest_gate")
    actor.level = 3  # 2 <= 2 -> still attacked
    result = move(ActionSnapshot(actor=actor, monsters=[ambusher()]), content, rules, "south")
    assert player_update(result, actor.id)["state"] == "in_combat"


def test_no_ambush_in_safe_room_or_by_dead_or_peaceful_monster(
    content: ContentRegistry, rules: Rules
) -> None:
    actor = make_player(room="forest_gate")
    safe = move(ActionSnapshot(actor=actor, monsters=[ambusher()]), content, rules, "north")
    assert "state" not in player_update(safe, actor.id)

    actor = make_player(room="forest_gate")
    dead_or_peaceful = [ambusher(alive=False), make_monster("dummy")]
    result = move(ActionSnapshot(actor=actor, monsters=dead_or_peaceful), content, rules, "south")
    assert "state" not in player_update(result, actor.id)


def test_ambush_can_kill_on_entry(content: ContentRegistry, rules: Rules) -> None:
    actor = make_player(room="forest_gate", hp=2)
    monster = ambusher()

    result = move(ActionSnapshot(actor=actor, monsters=[monster]), content, rules, "south")

    update = player_update(result, actor.id)
    assert (update["state"], update["hp"], update["combat_target_id"]) == ("dead", 0, None)
    assert monster_update(result, monster.id)["tagged_by_party"] is None
    assert result.changes.participations_upsert == []


# --- ambush while idling (world ticker) ---------------------------------------------------


def test_idle_ambush_uses_player_channel(content: ContentRegistry, rules: Rules) -> None:
    victim = make_player()
    monster = ambusher()

    result = resolve_idle_ambush(
        ActionSnapshot(actor=victim, monsters=[monster]), content, rules, NOW, random.Random(1)
    )

    assert player_update(result, victim.id)["state"] == "in_combat"
    # nobody acted, so nothing goes via an HTTP response
    assert not any(e.audience.kind == "actor" for e in result.events)
    personal = [
        (e.text, e.audience.player_ids)
        for e in result.events
        if isinstance(e.audience, PlayersAudience)
    ]
    assert ("Lauerer überfällt dich!", [victim.id]) in personal
    assert all(ids == [victim.id] for _, ids in personal)


def test_idle_ambush_skips_fighting_or_dead_victims(content: ContentRegistry, rules: Rules) -> None:
    states: list[PlayerState] = ["in_combat", "dead"]
    for state in states:
        victim = make_player()
        victim.state = state
        result = resolve_idle_ambush(
            ActionSnapshot(actor=victim, monsters=[ambusher()]),
            content,
            rules,
            NOW,
            random.Random(1),
        )
        assert result.changes.players == []
        assert result.events == []
