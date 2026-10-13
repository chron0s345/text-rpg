"""Groups (ARCHITECTURE.md, "Gruppen"): invite, accept, decline, leave, kick.

- An invitation (``GroupInvite``) expires after ``group_invite_ttl_seconds``. Accepting creates
  the group if the inviter has none yet; the inviter becomes leader.
- At most ``max_group_size`` members; a character is in at most one group. Only the leader
  invites and kicks; kicking is not possible in combat.
- Leaving is always possible (also dead, also on logout). If the leader leaves, the longest
  serving member leads. A group with a single member left is dissolved.

Kill credit stays with the party that tagged a monster (``rewards.party_members_in_room``), so
joining or leaving mid-fight changes no running fight's loot rules.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from app.content.registry import ContentRegistry
from app.content.schemas import Rules
from app.engine.events import (
    ActionResult,
    GroupAudience,
    GroupCreate,
    GroupUpdate,
    InviteDelete,
    InviteUpsert,
    ResultBuilder,
    rejected,
)
from app.engine.permissions import check_state
from app.engine.state import GroupActionSnapshot, GroupSnapshot, PlayerSnapshot, new_id

INVITED: Final = "group.invited"


def resolve_group_invite(
    snapshot: GroupActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    player_name: str,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("group_invite", actor):
        return denied
    wanted = player_name.strip().lower()
    target = next((p for p in snapshot.players if p.name.lower() == wanted), None)
    if wanted == actor.name.lower():
        return rejected("Du kannst dich nicht selbst einladen.")
    if target is None or not target.online:
        return rejected("Niemand mit diesem Namen ist online.")
    if target.group_id is not None:
        if target.group_id == actor.group_id:
            return rejected(f"{target.name} ist schon in deiner Gruppe.")
        return rejected(f"{target.name} ist schon in einer Gruppe.")
    group = snapshot.group(actor.group_id) if actor.group_id is not None else None
    if group is not None and group.leader_id != actor.id:
        return rejected("Nur die Gruppenleitung kann einladen.")
    if (len(group.member_ids) if group else 1) >= rules.max_group_size:
        return rejected("Die Gruppe ist voll.")

    builder = ResultBuilder(actor_id=actor.id)
    builder.changes.invites_upsert.append(
        InviteUpsert(
            inviter_id=actor.id,
            player_id=target.id,
            group_id=actor.group_id,
            expires_at=now + timedelta(seconds=rules.timings.group_invite_ttl_seconds),
        )
    )
    builder.to_actor("group.invite_sent", f"Du lädst {target.name} in deine Gruppe ein.")
    builder.to_player(
        target.id,
        INVITED,
        f"{actor.name} lädt dich in eine Gruppe ein.",
        inviter_id=str(actor.id),
    )
    return builder.build()


def resolve_group_accept(
    snapshot: GroupActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    inviter_id: UUID,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("group_accept", actor):
        return denied
    if actor.group_id is not None:
        return rejected("Du bist schon in einer Gruppe.")
    invite = snapshot.invite(inviter_id, actor.id)
    if invite is None or not invite.valid_at(now):
        return rejected("Diese Einladung gibt es nicht (mehr).")
    inviter = snapshot.player(inviter_id)
    if inviter is None or not inviter.online:
        return rejected("Wer dich eingeladen hat, ist nicht mehr online.")
    if invite.group_id != inviter.group_id:
        return rejected("Diese Einladung ist nicht mehr gültig.")

    builder = ResultBuilder(actor_id=actor.id)
    if inviter.group_id is None:
        group = GroupSnapshot(id=new_id(rng), leader_id=inviter.id, member_ids=[])
        snapshot.groups.append(group)
        builder.changes.groups_create.append(
            GroupCreate(id=group.id, leader_id=inviter.id, created_at=now)
        )
        _join(builder, group, inviter, now)
        builder.to_player(
            inviter.id, "group.created", f"Du leitest jetzt eine Gruppe mit {actor.name}."
        )
    else:
        found = snapshot.group(inviter.group_id)
        assert found is not None, "groups of involved players are part of the snapshot"
        group = found
        if len(group.member_ids) >= rules.max_group_size:
            return rejected("Die Gruppe ist voll.")
    _join(builder, group, actor, now)

    # joined: no other invitation to or from the new member stays open
    builder.changes.invites_delete.extend(
        InviteDelete(inviter_id=i.inviter_id, player_id=i.player_id)
        for i in snapshot.invites
        if actor.id in (i.inviter_id, i.player_id)
    )
    leader = snapshot.player(group.leader_id)
    leader_name = leader.name if leader is not None else inviter.name
    builder.to_actor("group.joined", f"Du trittst der Gruppe von {leader_name} bei.")
    builder.event(
        "group.member_joined",
        GroupAudience(group_id=group.id),
        f"{actor.name} tritt der Gruppe bei.",
        exclude=[actor.id],
        player_id=str(actor.id),
    )
    return builder.build()


def resolve_group_decline(
    snapshot: GroupActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    inviter_id: UUID,
) -> ActionResult:
    actor = snapshot.actor
    invite = snapshot.invite(inviter_id, actor.id)
    if invite is None:
        return rejected("Diese Einladung gibt es nicht (mehr).")
    builder = ResultBuilder(actor_id=actor.id)
    builder.changes.invites_delete.append(InviteDelete(inviter_id=inviter_id, player_id=actor.id))
    inviter = snapshot.player(inviter_id)
    builder.to_actor(
        "group.declined",
        f"Du lehnst die Einladung von {inviter.name if inviter else 'jemandem'} ab.",
    )
    if inviter is not None and invite.valid_at(now):
        builder.to_player(inviter.id, "group.declined", f"{actor.name} lehnt deine Einladung ab.")
    return builder.build()


def resolve_group_leave(
    snapshot: GroupActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
) -> ActionResult:
    """Also used after a logout (the actor is offline by then)."""
    actor = snapshot.actor
    if denied := check_state("group_leave", actor):
        return denied
    group = snapshot.group(actor.group_id) if actor.group_id is not None else None
    if group is None:
        return rejected("Du bist in keiner Gruppe.")
    builder = ResultBuilder(actor_id=actor.id)
    builder.to_actor("group.left", "Du verlässt die Gruppe.")
    _remove(builder, snapshot, group, actor, f"{actor.name} verlässt die Gruppe.")
    return builder.build()


def resolve_group_kick(
    snapshot: GroupActionSnapshot,
    content: ContentRegistry,
    rules: Rules,
    now: datetime,
    rng: random.Random,
    player_id: UUID,
) -> ActionResult:
    actor = snapshot.actor
    if denied := check_state("group_kick", actor):
        return denied
    group = snapshot.group(actor.group_id) if actor.group_id is not None else None
    if group is None:
        return rejected("Du bist in keiner Gruppe.")
    if group.leader_id != actor.id:
        return rejected("Nur die Gruppenleitung kann jemanden entfernen.")
    if player_id == actor.id:
        return rejected("Um zu gehen, verlasse die Gruppe.")
    target = snapshot.player(player_id)
    if target is None or player_id not in group.member_ids:
        return rejected("Diese Person ist nicht in deiner Gruppe.")
    builder = ResultBuilder(actor_id=actor.id)
    builder.to_actor("group.kicked", f"Du entfernst {target.name} aus der Gruppe.")
    builder.to_player(target.id, "group.kicked", "Du wurdest aus der Gruppe entfernt.")
    _remove(
        builder, snapshot, group, target, f"{target.name} wurde aus der Gruppe entfernt.", actor
    )
    return builder.build()


# --- helpers ---------------------------------------------------------------------------------


def _join(
    builder: ResultBuilder, group: GroupSnapshot, player: PlayerSnapshot, now: datetime
) -> None:
    player.group_id = group.id
    group.member_ids.append(player.id)
    builder.update_player(player.id, group_id=group.id, group_joined_at=now)


def _remove(
    builder: ResultBuilder,
    snapshot: GroupActionSnapshot,
    group: GroupSnapshot,
    member: PlayerSnapshot,
    text: str,
    actor: PlayerSnapshot | None = None,
) -> None:
    """Takes ``member`` out of ``group``; passes on the lead or dissolves the group."""
    member.group_id = None
    group.member_ids = [m for m in group.member_ids if m != member.id]
    builder.update_player(member.id, group_id=None, group_joined_at=None)
    exclude = [member.id] + ([actor.id] if actor is not None else [])

    if len(group.member_ids) <= 1:
        for remaining_id in group.member_ids:
            remaining = snapshot.player(remaining_id)
            if remaining is not None:
                remaining.group_id = None
            builder.update_player(remaining_id, group_id=None, group_joined_at=None)
            if remaining_id not in exclude:
                builder.to_player(
                    remaining_id, "group.dissolved", f"{text} Die Gruppe ist aufgelöst."
                )
        builder.changes.groups_delete.append(group.id)  # open invitations of it go with it
        snapshot.groups = [g for g in snapshot.groups if g.id != group.id]
        return

    builder.event(
        "group.member_left",
        GroupAudience(group_id=group.id),
        text,
        exclude=exclude,
        player_id=str(member.id),
    )
    if group.leader_id == member.id:
        group.leader_id = group.member_ids[0]  # longest serving member
        builder.changes.groups.append(GroupUpdate(group_id=group.id, leader_id=group.leader_id))
        leader = snapshot.player(group.leader_id)
        builder.event(
            "group.leader_changed",
            GroupAudience(group_id=group.id),
            f"{leader.name if leader else 'Jemand'} leitet jetzt die Gruppe.",
            player_id=str(group.leader_id),
        )
