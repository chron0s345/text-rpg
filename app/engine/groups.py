"""Groups (ARCHITECTURE.md, "Gruppen").

So far only leaving, which logout and expired disconnects need; invite/accept/kick follow with the
group actions. Changing a player's membership requires the group's row lock and the locked rows
of every member that changes (the service loads them with ``lock_group_members``).
"""

from __future__ import annotations

from app.engine.events import GroupAudience, GroupUpdate, PlayersAudience, ResultBuilder
from app.engine.state import ActionSnapshot, PlayerSnapshot


def leave_group(builder: ResultBuilder, snapshot: ActionSnapshot, player: PlayerSnapshot) -> None:
    """``player`` leaves its group (no-op without one), mutating ``player``.

    - The longest-standing remaining member becomes leader if the leader leaves.
    - With one member left the group is dissolved: that member leaves too, the group is deleted.
    """
    if player.group_id is None:
        return
    group_id = player.group_id
    group = snapshot.group(group_id)
    assert group is not None, "the player's group must be in the snapshot"
    remaining = [m for m in group.member_ids if m != player.id]

    player.group_id = None
    builder.update_player(player.id, group_id=None, group_joined_at=None)
    group.member_ids = remaining
    builder.to_player(player.id, "group.left", "Du hast die Gruppe verlassen.")

    if len(remaining) <= 1:
        for member_id in remaining:
            builder.update_player(member_id, group_id=None, group_joined_at=None)
            member = snapshot.player(member_id)
            if member is not None:
                member.group_id = None
        builder.changes.groups_delete.append(group_id)
        snapshot.groups = [g for g in snapshot.groups if g.id != group_id]
        if remaining:
            builder.event(
                "group.dissolved",
                PlayersAudience(player_ids=remaining),
                f"{player.name} hat die Gruppe verlassen. Die Gruppe ist aufgelöst.",
                group_id=str(group_id),
            )
        return

    text = f"{player.name} hat die Gruppe verlassen."
    data: dict[str, str] = {"player_id": str(player.id)}
    if group.leader_id == player.id:
        group.leader_id = remaining[0]  # members are ordered by join time
        builder.changes.groups.append(GroupUpdate(group_id=group_id, leader_id=group.leader_id))
        leader = snapshot.player(group.leader_id)  # None if the new leader is elsewhere
        text += (
            f" {leader.name} führt jetzt die Gruppe."
            if leader is not None
            else " Die Gruppe hat eine neue Führung."
        )
        data["leader_id"] = str(group.leader_id)
    builder.event(
        "group.member_left", GroupAudience(group_id=group_id), text, exclude=[player.id], **data
    )
