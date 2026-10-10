"""Group panel updates (ARCHITECTURE.md, "Gruppen": members see each other's HP and location)."""

from __future__ import annotations

from app.engine.events import GroupAudience, ResultBuilder
from app.engine.state import PlayerSnapshot


def report_hp(builder: ResultBuilder, player: PlayerSnapshot) -> None:
    if player.group_id is None:
        return
    builder.event(
        "group.member_hp",
        GroupAudience(group_id=player.group_id),
        f"{player.name}: {player.hp} HP",
        exclude=[player.id],
        player_id=str(player.id),
        hp=player.hp,
    )


def report_location(builder: ResultBuilder, player: PlayerSnapshot, room_name: str) -> None:
    if player.group_id is None:
        return
    builder.event(
        "group.member_moved",
        GroupAudience(group_id=player.group_id),
        f"{player.name} ist jetzt in {room_name}.",
        exclude=[player.id],
        player_id=str(player.id),
        room_id=player.current_room_id,
    )
