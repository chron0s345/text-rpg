import random
from datetime import timedelta

from app.content.registry import ContentRegistry
from app.engine.respawn import respawn_due_monsters, respawn_monsters
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

    result = respawn_due_monsters(snapshot, content, NOW, random.Random(1))

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
        ActionSnapshot(actor=make_player(), monsters=[later, alive]), content, NOW, random.Random(1)
    )
    assert result.changes.monsters == []
    assert result.events == []


class Roll(random.Random):
    """``random()`` returns the given values in turn."""

    def __init__(self, *values: float) -> None:
        super().__init__(0)
        self.values = list(values)

    def random(self) -> float:
        return self.values.pop(0)


def test_a_respawn_can_bring_the_rare_variant(content: ContentRegistry) -> None:
    dead = make_monster("plain", hp=0, alive=False, respawn_at=NOW)

    result = respawn_monsters([dead], content, NOW, Roll(0.2))  # 0.2 < chance 0.25

    update = monster_update(result, dead.id)
    assert (update["monster_id"], update["hp"]) == ("golden", 30)
    assert (dead.monster_id, dead.hp) == ("golden", 30)
    [event] = result.events
    assert event.text == "Goldene Feldmaus taucht auf – ein seltener Anblick!"
    assert event.data["rare"] is True


def test_otherwise_the_base_form_and_a_dead_variant_falls_back(content: ContentRegistry) -> None:
    common = make_monster("plain", hp=0, alive=False, respawn_at=NOW)
    result = respawn_monsters([common], content, NOW, Roll(0.25))  # not below the chance
    assert "monster_id" not in monster_update(result, common.id)  # stays what it was
    assert result.events[0].text == "Feldmaus taucht auf."

    killed_variant = make_monster("golden", hp=0, alive=False, respawn_at=NOW)
    result = respawn_monsters([killed_variant], content, NOW, Roll(0.9))
    update = monster_update(result, killed_variant.id)
    assert (update["monster_id"], update["hp"]) == ("plain", 10)


def test_monsters_without_a_variant_roll_nothing(content: ContentRegistry) -> None:
    dead = make_monster(hp=0, alive=False, respawn_at=NOW)
    respawn_monsters([dead], content, NOW, Roll())  # popping from an empty roll would fail
    assert dead.monster_id == "dummy"


def test_base_monster_of_a_variant(content: ContentRegistry) -> None:
    assert content.base_monster_id("golden") == "plain"
    assert content.base_monster_id("plain") == "plain"
    assert content.base_monster_id("dummy") == "dummy"
