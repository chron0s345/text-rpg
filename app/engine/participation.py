"""Combat participation bookkeeping shared by combat, flee, death and ambush."""

from __future__ import annotations

from uuid import UUID

from app.engine.events import ParticipationDelete, ParticipationUpsert, ResultBuilder
from app.engine.state import ActionSnapshot, ParticipationSnapshot, PlayerSnapshot


def upsert_participation(
    builder: ResultBuilder,
    snapshot: ActionSnapshot,
    player: PlayerSnapshot,
    monster_instance_id: UUID,
    added_damage: int,
) -> ParticipationSnapshot:
    """Adds damage to the player's participation (creating it if needed) in snapshot + changes."""
    existing = next(
        (
            p
            for p in snapshot.participations
            if p.monster_instance_id == monster_instance_id and p.player_id == player.id
        ),
        None,
    )
    if existing is None:
        existing = ParticipationSnapshot(
            monster_instance_id=monster_instance_id,
            player_id=player.id,
            party_key=player.party_key,
            damage_dealt=0,
        )
        snapshot.participations.append(existing)
    existing.party_key = player.party_key
    existing.damage_dealt += added_damage

    builder.changes.participations_upsert = [
        u
        for u in builder.changes.participations_upsert
        if not (u.monster_instance_id == monster_instance_id and u.player_id == player.id)
    ]
    builder.changes.participations_upsert.append(
        ParticipationUpsert(
            monster_instance_id=monster_instance_id,
            player_id=player.id,
            party_key=existing.party_key,
            damage_dealt=existing.damage_dealt,
        )
    )
    return existing


def remove_from_combat(
    builder: ResultBuilder,
    snapshot: ActionSnapshot,
    player: PlayerSnapshot,
    monster_instance_id: UUID,
) -> None:
    """Ends the player's participation; clears the kill-credit tag if the tagging party is gone.

    ARCHITECTURE.md: "Fliehen oder sterben alle Mitglieder der markierenden Partei, wird die
    Markierung aufgehoben."
    """
    builder.changes.participations_upsert = [
        u
        for u in builder.changes.participations_upsert
        if not (u.monster_instance_id == monster_instance_id and u.player_id == player.id)
    ]
    builder.changes.participations_delete.append(
        ParticipationDelete(monster_instance_id=monster_instance_id, player_id=player.id)
    )
    snapshot.participations = [
        p
        for p in snapshot.participations
        if not (p.monster_instance_id == monster_instance_id and p.player_id == player.id)
    ]
    player.combat_target_id = None

    monster = snapshot.monster(monster_instance_id)
    if monster is None or monster.tagged_by_party is None:
        return
    remaining = snapshot.participations_for(monster_instance_id)
    if not any(p.party_key == monster.tagged_by_party for p in remaining):
        monster.tagged_by_party = None
        builder.update_monster(monster_instance_id, tagged_by_party=None)
