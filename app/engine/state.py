"""Snapshots of mutable world state handed to the engine.

The service layer loads (and locks) the relevant rows, converts them into these models and
passes them to pure engine functions. The engine never touches the database.
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.content.schemas import EquipSlot, PlayerState


def new_id(rng: random.Random) -> UUID:
    """Deterministic UUIDv4 from the injected RNG (engine must not use uuid4()/os.urandom)."""
    return UUID(int=rng.getrandbits(128), version=4)


def party_key_for(player_id: UUID, group_id: UUID | None) -> str:
    return f"group:{group_id}" if group_id is not None else f"solo:{player_id}"


class InventoryItemState(BaseModel):
    id: UUID
    item_id: str
    quantity: int = Field(ge=1)
    equipped_slot: EquipSlot | None = None


class PlayerSnapshot(BaseModel):
    id: UUID
    account_id: UUID
    name: str
    hp: int
    base_max_hp: int
    base_attack: int
    base_defense: int
    xp: int
    level: int
    gold: int
    current_room_id: str
    checkpoint_room_id: str
    state: PlayerState
    combat_target_id: UUID | None = None
    group_id: UUID | None = None
    inventory: list[InventoryItemState] = Field(default_factory=list)
    # inventory entries offered in an open trade: cannot be dropped, used, equipped, sold, banked
    locked_item_ids: list[UUID] = Field(default_factory=list)

    def item(self, inventory_item_id: UUID) -> InventoryItemState | None:
        return next((i for i in self.inventory if i.id == inventory_item_id), None)

    @property
    def party_key(self) -> str:
        return party_key_for(self.id, self.group_id)


class MonsterSnapshot(BaseModel):
    id: UUID
    monster_id: str
    room_id: str
    hp: int  # stored HP as of last_combat_at (regeneration is applied lazily)
    alive: bool
    last_combat_at: datetime | None = None
    respawn_at: datetime | None = None
    tagged_by_party: str | None = None


class FloorItemSnapshot(BaseModel):
    id: UUID
    room_id: str
    item_id: str
    quantity: int = Field(ge=1)
    source: Literal["spawn", "drop", "loot"]
    dropped_by_account_id: UUID | None = None
    expires_at: datetime | None = None  # None only for source = "spawn"

    def visible_at(self, now: datetime) -> bool:
        """Expired items are filtered out immediately, even before the ticker deletes them."""
        return self.expires_at is None or self.expires_at > now


class ItemSpawnSnapshot(BaseModel):
    """Respawn state of one room item rule (``next_spawn_at`` is set once it was picked up)."""

    room_id: str
    item_id: str
    next_spawn_at: datetime | None = None


class ParticipationSnapshot(BaseModel):
    monster_instance_id: UUID
    player_id: UUID
    party_key: str
    damage_dealt: int = 0


class GroupSnapshot(BaseModel):
    id: UUID
    leader_id: UUID
    loot_cursor: int = 0
    member_ids: list[UUID]  # ordered by join time (oldest first)


class TradeSnapshot(BaseModel):
    """An active trade ("requested" or "open") of a player in the snapshot."""

    id: UUID
    initiator_id: UUID
    partner_id: UUID
    status: Literal["requested", "open"]

    def other(self, player_id: UUID) -> UUID:
        return self.partner_id if player_id == self.initiator_id else self.initiator_id


class ActionSnapshot(BaseModel):
    """Relevant slice of the world for one action.

    - ``room_players``: other online players in the room the action happens in (actor excluded).
      For ``move`` this is the *destination* room.
    - ``monsters``: monster instances in that room.
    - ``participations``: combat participations for those monsters.
    - ``floor_items``: items lying in that room.
    - ``groups``: groups of the actor and of all ``room_players`` (needed for kill credit and
      round-robin loot of a tagging party that is not the actor's).
    - ``trades``: the actor's active trades, only loaded when the action may cancel them
      (logout, disconnect); empty otherwise.
    """

    actor: PlayerSnapshot
    room_players: list[PlayerSnapshot] = Field(default_factory=list)
    monsters: list[MonsterSnapshot] = Field(default_factory=list)
    participations: list[ParticipationSnapshot] = Field(default_factory=list)
    groups: list[GroupSnapshot] = Field(default_factory=list)
    floor_items: list[FloorItemSnapshot] = Field(default_factory=list)
    trades: list[TradeSnapshot] = Field(default_factory=list)

    @property
    def players_in_room(self) -> list[PlayerSnapshot]:
        return [self.actor, *self.room_players]

    def player(self, player_id: UUID) -> PlayerSnapshot | None:
        if player_id == self.actor.id:
            return self.actor
        return next((p for p in self.room_players if p.id == player_id), None)

    def group(self, group_id: UUID) -> GroupSnapshot | None:
        return next((g for g in self.groups if g.id == group_id), None)

    def monster(self, monster_instance_id: UUID) -> MonsterSnapshot | None:
        return next((m for m in self.monsters if m.id == monster_instance_id), None)

    def participations_for(self, monster_instance_id: UUID) -> list[ParticipationSnapshot]:
        return [p for p in self.participations if p.monster_instance_id == monster_instance_id]
