from datetime import timedelta

from app.content.registry import ContentRegistry
from app.engine.respawn import respawn_due_monsters
from app.engine.state import ActionSnapshot
from tests.engine.factories import NOW, actor_texts, make_monster, make_player, monster_update


def test_due_monster_comes_back_fresh(content: ContentRegistry) -> None:
    actor = make_player()
    due = make_monster(
        hp=0,
        alive=False,
        respawn_at=NOW,
        last_combat_at=NOW - timedelta(minutes=1),
        tagged_by_party="solo:x",
    )
    snapshot = ActionSnapshot(actor=actor, monsters=[due])

    result = respawn_due_monsters(snapshot, content, NOW)

    assert monster_update(result, due.id) == {
        "monster_instance_id": due.id,
        "alive": True,
        "hp": 10,
        "respawn_at": None,
        "last_combat_at": None,
        "tagged_by_party": None,
    }
    assert due.alive and due.hp == 10  # the snapshot is updated for the following action
    assert actor_texts(result) == ["Übungspuppe taucht auf."]
    [room] = [e for e in result.events if e.audience.kind == "room"]
    assert room.exclude_player_ids == [actor.id]


def test_not_yet_due_or_alive_monsters_are_untouched(content: ContentRegistry) -> None:
    later = make_monster(hp=0, alive=False, respawn_at=NOW + timedelta(seconds=1))
    alive = make_monster()
    result = respawn_due_monsters(
        ActionSnapshot(actor=make_player(), monsters=[later, alive]), content, NOW
    )
    assert result.changes.monsters == []
    assert result.events == []
