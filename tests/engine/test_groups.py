"""Groups (engine/groups.py): invite, accept, decline, leave, kick, leader succession,
dissolving, and kill credit of a group dissolved mid-fight."""

import random
from datetime import timedelta
from uuid import UUID, uuid4

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.events import ActionResult, GroupAudience, PlayersAudience
from app.engine.groups import (
    resolve_group_accept,
    resolve_group_decline,
    resolve_group_invite,
    resolve_group_kick,
    resolve_group_leave,
)
from app.engine.rewards import party_members_in_room
from app.engine.state import (
    ActionSnapshot,
    GroupActionSnapshot,
    GroupSnapshot,
    InviteSnapshot,
    ParticipationSnapshot,
    PlayerSnapshot,
)
from tests.engine.factories import NOW, actor_texts, fight, make_monster, make_player

RNG = random.Random(4)


def texts_for(result: ActionResult, player_id: UUID) -> list[str]:
    return [
        e.text
        for e in result.events
        if isinstance(e.audience, PlayersAudience) and player_id in e.audience.player_ids
    ]


def group_of(*members: PlayerSnapshot) -> GroupSnapshot:
    group = GroupSnapshot(id=uuid4(), leader_id=members[0].id, member_ids=[m.id for m in members])
    for member in members:
        member.group_id = group.id
    return group


def invite(inviter: PlayerSnapshot, player: PlayerSnapshot, **fields: object) -> InviteSnapshot:
    data: dict[str, object] = {
        "inviter_id": inviter.id,
        "player_id": player.id,
        "group_id": inviter.group_id,
        "expires_at": NOW + timedelta(seconds=30),
    }
    data.update(fields)
    return InviteSnapshot.model_validate(data)


def player_changes(result: ActionResult) -> dict[UUID, dict[str, object]]:
    return {
        u.player_id: u.model_dump(exclude_unset=True, exclude={"player_id"})
        for u in result.changes.players
    }


# --- invite ---


def test_invite_by_name(content: ContentRegistry, rules: Rules) -> None:
    anna, ben = make_player("Anna"), make_player("Ben")
    snapshot = GroupActionSnapshot(actor=anna, players=[ben])

    result = resolve_group_invite(snapshot, content, rules, NOW, RNG, "  bEn ")

    [created] = result.changes.invites_upsert
    assert (created.inviter_id, created.player_id, created.group_id) == (anna.id, ben.id, None)
    assert created.expires_at == NOW + timedelta(seconds=rules.timings.group_invite_ttl_seconds)
    assert actor_texts(result) == ["Du lädst Ben in deine Gruppe ein."]
    assert texts_for(result, ben.id) == ["Anna lädt dich in eine Gruppe ein."]


def test_invite_rejections(content: ContentRegistry, rules: Rules) -> None:
    anna, ben, cleo = make_player("Anna"), make_player("Ben"), make_player("Cleo")
    offline = make_player("Dora")
    offline.online = False

    def invite_text(
        actor: PlayerSnapshot,
        name: str,
        *others: PlayerSnapshot,
        groups: tuple[GroupSnapshot, ...] = (),
    ) -> str:
        snap = GroupActionSnapshot(actor=actor, players=list(others), groups=list(groups))
        [text] = actor_texts(resolve_group_invite(snap, content, rules, NOW, RNG, name))
        return text

    assert invite_text(anna, "anna") == "Du kannst dich nicht selbst einladen."
    assert invite_text(anna, "Niemand") == "Niemand mit diesem Namen ist online."
    assert invite_text(anna, "Dora", offline) == "Niemand mit diesem Namen ist online."

    group = group_of(anna, ben)
    assert invite_text(anna, "Ben", ben, groups=(group,)) == "Ben ist schon in deiner Gruppe."
    assert (
        invite_text(ben, "Cleo", cleo, groups=(group,)) == "Nur die Gruppenleitung kann einladen."
    )
    other = group_of(cleo)
    assert (
        invite_text(anna, "Cleo", cleo, groups=(group, other)) == "Cleo ist schon in einer Gruppe."
    )

    full = group_of(*(make_player(f"M{i}") for i in range(rules.max_group_size)))
    leader_id = full.leader_id
    leader = make_player("Lea")
    leader.id = leader_id
    leader.group_id = full.id
    newcomer = make_player("Nora")
    assert invite_text(leader, "Nora", newcomer, groups=(full,)) == "Die Gruppe ist voll."


# --- accept / decline ---


def test_accept_creates_the_group_with_the_inviter_as_leader(
    content: ContentRegistry, rules: Rules
) -> None:
    anna, ben, cleo = make_player("Anna"), make_player("Ben"), make_player("Cleo")
    snapshot = GroupActionSnapshot(
        actor=ben,
        players=[anna, cleo],
        invites=[invite(anna, ben), invite(cleo, ben), invite(ben, cleo)],
    )

    result = resolve_group_accept(snapshot, content, rules, NOW, RNG, anna.id)

    [group] = result.changes.groups_create
    assert (group.leader_id, group.created_at) == (anna.id, NOW)
    changes = player_changes(result)
    assert changes[anna.id] == {"group_id": group.id, "group_joined_at": NOW}
    assert changes[ben.id] == {"group_id": group.id, "group_joined_at": NOW}
    # every invitation to or from the new member is gone
    assert {(d.inviter_id, d.player_id) for d in result.changes.invites_delete} == {
        (anna.id, ben.id),
        (cleo.id, ben.id),
        (ben.id, cleo.id),
    }
    assert actor_texts(result) == ["Du trittst der Gruppe von Anna bei."]
    assert texts_for(result, anna.id) == ["Du leitest jetzt eine Gruppe mit Ben."]


def test_accept_joins_an_existing_group(content: ContentRegistry, rules: Rules) -> None:
    anna, ben, cleo = make_player("Anna"), make_player("Ben"), make_player("Cleo")
    group = group_of(anna, ben)
    snapshot = GroupActionSnapshot(
        actor=cleo, players=[anna, ben], groups=[group], invites=[invite(anna, cleo)]
    )
    result = resolve_group_accept(snapshot, content, rules, NOW, RNG, anna.id)
    assert result.changes.groups_create == []
    assert player_changes(result) == {cleo.id: {"group_id": group.id, "group_joined_at": NOW}}
    [joined] = [e for e in result.events if e.kind == "group.member_joined"]
    assert isinstance(joined.audience, GroupAudience) and joined.audience.group_id == group.id


def test_accept_rejections(content: ContentRegistry, rules: Rules) -> None:
    anna, ben = make_player("Anna"), make_player("Ben")

    def accept_text(snapshot: GroupActionSnapshot) -> str:
        [text] = actor_texts(resolve_group_accept(snapshot, content, rules, NOW, RNG, anna.id))
        return text

    expired = invite(anna, ben, expires_at=NOW)
    assert accept_text(GroupActionSnapshot(actor=ben, players=[anna], invites=[expired])) == (
        "Diese Einladung gibt es nicht (mehr)."
    )
    # the inviter joined another group since inviting: the invitation is stale
    stale = invite(anna, ben)
    elsewhere = group_of(make_player("Cleo"), anna)
    snapshot = GroupActionSnapshot(actor=ben, players=[anna], groups=[elsewhere], invites=[stale])
    assert accept_text(snapshot) == "Diese Einladung ist nicht mehr gültig."

    gone = make_player("Anna")
    gone.id, gone.online = anna.id, False
    snapshot = GroupActionSnapshot(actor=ben, players=[gone], invites=[invite(anna, ben)])
    assert accept_text(snapshot) == "Wer dich eingeladen hat, ist nicht mehr online."

    ben.state = "dead"
    assert accept_text(GroupActionSnapshot(actor=ben, players=[anna])) == "Du bist tot."


def test_accept_into_a_full_group(content: ContentRegistry, rules: Rules) -> None:
    members = [make_player(f"M{i}") for i in range(rules.max_group_size)]
    group = group_of(*members)
    newcomer = make_player("Nora")
    snapshot = GroupActionSnapshot(
        actor=newcomer, players=members, groups=[group], invites=[invite(members[0], newcomer)]
    )
    result = resolve_group_accept(snapshot, content, rules, NOW, RNG, members[0].id)
    assert actor_texts(result) == ["Die Gruppe ist voll."]


def test_decline(content: ContentRegistry, rules: Rules) -> None:
    anna, ben = make_player("Anna"), make_player("Ben")
    snapshot = GroupActionSnapshot(actor=ben, players=[anna], invites=[invite(anna, ben)])
    result = resolve_group_decline(snapshot, content, rules, NOW, RNG, anna.id)
    assert [(d.inviter_id, d.player_id) for d in result.changes.invites_delete] == [
        (anna.id, ben.id)
    ]
    assert texts_for(result, anna.id) == ["Ben lehnt deine Einladung ab."]


# --- leave / kick ---


def test_member_leaves(content: ContentRegistry, rules: Rules) -> None:
    anna, ben, cleo = make_player("Anna"), make_player("Ben"), make_player("Cleo")
    group = group_of(anna, ben, cleo)
    snapshot = GroupActionSnapshot(actor=cleo, players=[anna, ben], groups=[group])
    result = resolve_group_leave(snapshot, content, rules, NOW, RNG)
    assert player_changes(result) == {cleo.id: {"group_id": None, "group_joined_at": None}}
    assert result.changes.groups == [] and result.changes.groups_delete == []
    [left] = [e for e in result.events if e.kind == "group.member_left"]
    assert left.text == "Cleo verlässt die Gruppe."


def test_leader_leaves_and_the_longest_member_leads(content: ContentRegistry, rules: Rules) -> None:
    anna, ben, cleo = make_player("Anna"), make_player("Ben"), make_player("Cleo")
    group = group_of(anna, ben, cleo)  # join order: Anna, Ben, Cleo
    snapshot = GroupActionSnapshot(actor=anna, players=[ben, cleo], groups=[group])
    result = resolve_group_leave(snapshot, content, rules, NOW, RNG)
    [update] = result.changes.groups
    assert update.leader_id == ben.id
    assert "Ben leitet jetzt die Gruppe." in [e.text for e in result.events]


def test_last_two_members_dissolve_the_group(content: ContentRegistry, rules: Rules) -> None:
    anna, ben = make_player("Anna"), make_player("Ben")
    group = group_of(anna, ben)
    ben.state = "dead"  # leaving works in every state
    snapshot = GroupActionSnapshot(actor=ben, players=[anna], groups=[group])
    result = resolve_group_leave(snapshot, content, rules, NOW, RNG)
    assert result.changes.groups_delete == [group.id]
    assert player_changes(result) == {
        ben.id: {"group_id": None, "group_joined_at": None},
        anna.id: {"group_id": None, "group_joined_at": None},
    }
    assert texts_for(result, anna.id) == ["Ben verlässt die Gruppe. Die Gruppe ist aufgelöst."]


def test_leave_without_group(content: ContentRegistry, rules: Rules) -> None:
    result = resolve_group_leave(GroupActionSnapshot(actor=make_player()), content, rules, NOW, RNG)
    assert actor_texts(result) == ["Du bist in keiner Gruppe."]


def test_kick(content: ContentRegistry, rules: Rules) -> None:
    anna, ben, cleo = make_player("Anna"), make_player("Ben"), make_player("Cleo")
    group = group_of(anna, ben, cleo)

    def kick(actor: PlayerSnapshot, target: UUID) -> ActionResult:
        others = [p for p in (anna, ben, cleo) if p is not actor]
        snap = GroupActionSnapshot(actor=actor, players=others, groups=[group])
        return resolve_group_kick(snap, content, rules, NOW, RNG, target)

    assert actor_texts(kick(ben, cleo.id)) == ["Nur die Gruppenleitung kann jemanden entfernen."]
    assert actor_texts(kick(anna, anna.id)) == ["Um zu gehen, verlasse die Gruppe."]
    assert actor_texts(kick(anna, uuid4())) == ["Diese Person ist nicht in deiner Gruppe."]

    result = kick(anna, cleo.id)
    assert player_changes(result) == {cleo.id: {"group_id": None, "group_joined_at": None}}
    assert texts_for(result, cleo.id) == ["Du wurdest aus der Gruppe entfernt."]

    fight(anna, make_monster())
    assert actor_texts(kick(anna, ben.id)) == ["Das geht nicht im Kampf."]


# --- kill credit after dissolving ---


def test_dissolved_tagging_group_keeps_its_loot(content: ContentRegistry, rules: Rules) -> None:
    anna, ben, stranger = make_player("Anna"), make_player("Ben"), make_player("Zed")
    old_group = uuid4()
    monster = make_monster(tagged_by_party=f"group:{old_group}")
    participations = [
        ParticipationSnapshot(
            monster_instance_id=monster.id, player_id=p.id, party_key=f"group:{old_group}"
        )
        for p in (anna, ben)
    ] + [
        ParticipationSnapshot(
            monster_instance_id=monster.id, player_id=stranger.id, party_key=stranger.party_key
        )
    ]
    snapshot = ActionSnapshot(
        actor=anna, room_players=[ben, stranger], monsters=[monster], participations=participations
    )
    members, group = party_members_in_room(snapshot, f"group:{old_group}", monster.id)
    assert group is None
    assert {m.id for m in members} == {anna.id, ben.id}  # not the stranger
