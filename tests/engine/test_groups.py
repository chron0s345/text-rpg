from uuid import uuid4

from app.engine.events import ResultBuilder
from app.engine.groups import leave_group
from app.engine.state import ActionSnapshot, GroupSnapshot, PlayerSnapshot
from tests.engine.factories import make_player


def group_of(*members: PlayerSnapshot, leader: PlayerSnapshot | None = None) -> GroupSnapshot:
    group_id = uuid4()
    for member in members:
        member.group_id = group_id
    return GroupSnapshot(
        id=group_id, leader_id=(leader or members[0]).id, member_ids=[m.id for m in members]
    )


def test_member_leaves() -> None:
    anna, ben, cleo = make_player("Anna"), make_player("Ben"), make_player("Cleo")
    group = group_of(anna, ben, cleo)
    snapshot = ActionSnapshot(actor=ben, room_players=[anna, cleo], groups=[group])
    builder = ResultBuilder(actor_id=ben.id)

    leave_group(builder, snapshot, ben)
    result = builder.build()

    assert ben.group_id is None and group.member_ids == [anna.id, cleo.id]
    assert [(u.player_id, u.group_id) for u in result.changes.players] == [(ben.id, None)]
    assert result.changes.groups == [] and result.changes.groups_delete == []
    [to_group] = [e for e in result.events if e.audience.kind == "group"]
    assert to_group.text == "Ben hat die Gruppe verlassen."
    assert to_group.exclude_player_ids == [ben.id]


def test_leader_leaves_and_the_oldest_member_leads() -> None:
    anna, ben, cleo = make_player("Anna"), make_player("Ben"), make_player("Cleo")
    group = group_of(anna, ben, cleo)  # join order: Anna (leader), Ben, Cleo
    snapshot = ActionSnapshot(actor=anna, room_players=[ben, cleo], groups=[group])
    builder = ResultBuilder(actor_id=anna.id)

    leave_group(builder, snapshot, anna)
    result = builder.build()

    assert [g.model_dump(exclude_unset=True) for g in result.changes.groups] == [
        {"group_id": group.id, "leader_id": ben.id}
    ]
    texts = [e.text for e in result.events if e.audience.kind == "group"]
    assert texts == ["Anna hat die Gruppe verlassen. Ben führt jetzt die Gruppe."]


def test_new_leader_elsewhere_is_not_named() -> None:
    anna, ben, cleo = make_player("Anna"), make_player("Ben"), make_player("Cleo")
    group = group_of(anna, ben, cleo)
    snapshot = ActionSnapshot(actor=anna, groups=[group])  # Ben and Cleo are in another room
    builder = ResultBuilder(actor_id=anna.id)

    leave_group(builder, snapshot, anna)

    texts = [e.text for e in builder.build().events if e.audience.kind == "group"]
    assert texts == ["Anna hat die Gruppe verlassen. Die Gruppe hat eine neue Führung."]


def test_last_two_dissolve_the_group() -> None:
    anna, ben = make_player("Anna"), make_player("Ben")
    group = group_of(anna, ben)
    snapshot = ActionSnapshot(actor=anna, groups=[group])  # Ben is elsewhere
    builder = ResultBuilder(actor_id=anna.id)

    leave_group(builder, snapshot, anna)
    result = builder.build()

    assert {(u.player_id, u.group_id, u.group_joined_at) for u in result.changes.players} == {
        (anna.id, None, None),
        (ben.id, None, None),
    }
    assert result.changes.groups_delete == [group.id]
    assert snapshot.groups == []
    [to_ben] = [e for e in result.events if e.audience.kind == "players"]
    assert to_ben.audience.model_dump()["player_ids"] == [ben.id]
    assert to_ben.text == "Anna hat die Gruppe verlassen. Die Gruppe ist aufgelöst."


def test_without_group_nothing_happens() -> None:
    anna = make_player()
    builder = ResultBuilder(actor_id=anna.id)
    leave_group(builder, ActionSnapshot(actor=anna), anna)
    result = builder.build()
    assert result.changes.players == [] and result.events == []
