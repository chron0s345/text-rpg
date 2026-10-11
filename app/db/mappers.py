"""Conversion between database rows and engine snapshots."""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast
from uuid import UUID

from app.content.schemas import EquipSlot, PlayerState
from app.db.models import (
    BankItem,
    CombatParticipation,
    FloorItem,
    Group,
    GroupInvite,
    InventoryItem,
    MonsterInstance,
    Player,
)
from app.engine.state import (
    FloorItemSnapshot,
    GroupSnapshot,
    InventoryItemState,
    InviteSnapshot,
    MonsterSnapshot,
    ParticipationSnapshot,
    PlayerSnapshot,
)


def inventory_item_state(row: InventoryItem) -> InventoryItemState:
    return InventoryItemState(
        id=row.id,
        item_id=row.item_id,
        quantity=row.quantity,
        equipped_slot=cast(EquipSlot | None, row.equipped_slot),
    )


def player_snapshot(
    row: Player, inventory: Iterable[InventoryItem], locked_item_ids: Iterable[UUID] = ()
) -> PlayerSnapshot:
    return PlayerSnapshot(
        id=row.id,
        account_id=row.account_id,
        name=row.name,
        hp=row.hp,
        base_max_hp=row.base_max_hp,
        base_attack=row.base_attack,
        base_defense=row.base_defense,
        xp=row.xp,
        level=row.level,
        gold=row.gold,
        current_room_id=row.current_room_id,
        checkpoint_room_id=row.checkpoint_room_id,
        state=cast(PlayerState, row.state),
        combat_target_id=row.combat_target_id,
        group_id=row.group_id,
        online=row.online,
        inventory=[inventory_item_state(i) for i in inventory],
        locked_item_ids=list(locked_item_ids),
    )


def monster_snapshot(row: MonsterInstance) -> MonsterSnapshot:
    return MonsterSnapshot(
        id=row.id,
        monster_id=row.monster_id,
        room_id=row.room_id,
        hp=row.hp,
        alive=row.alive,
        last_combat_at=row.last_combat_at,
        respawn_at=row.respawn_at,
        tagged_by_party=row.tagged_by_party,
    )


def participation_snapshot(row: CombatParticipation) -> ParticipationSnapshot:
    return ParticipationSnapshot(
        monster_instance_id=row.monster_instance_id,
        player_id=row.player_id,
        party_key=row.party_key,
        damage_dealt=row.damage_dealt,
    )


def floor_item_snapshot(row: FloorItem) -> FloorItemSnapshot:
    return FloorItemSnapshot.model_validate(
        {
            "id": row.id,
            "room_id": row.room_id,
            "item_id": row.item_id,
            "quantity": row.quantity,
            "source": row.source,
            "dropped_by_account_id": row.dropped_by_account_id,
            "expires_at": row.expires_at,
        }
    )


def group_snapshot(row: Group, member_ids: list[UUID]) -> GroupSnapshot:
    return GroupSnapshot(
        id=row.id, leader_id=row.leader_id, loot_cursor=row.loot_cursor, member_ids=member_ids
    )


def invite_snapshot(row: GroupInvite) -> InviteSnapshot:
    return InviteSnapshot(
        inviter_id=row.inviter_id,
        player_id=row.player_id,
        group_id=row.group_id,
        expires_at=row.expires_at,
    )


def bank_item_state(row: BankItem) -> InventoryItemState:
    return InventoryItemState(id=row.id, item_id=row.item_id, quantity=row.quantity)
