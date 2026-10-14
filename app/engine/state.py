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


class PlayerRef(BaseModel):
    """Another character known only by id and name (e.g. the recipient of a whisper)."""

    id: UUID
    name: str


class QuestState(BaseModel):
    quest_id: str
    status: Literal["active", "completed"]
    step: int = 0  # index into QuestTemplate.steps; == len(steps) once all are done
    progress: int = 0  # kills so far in the current step (kill steps)
    times_completed: int = 0


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
    online: bool = True
    disconnected: bool = False  # connection lost, grace period running
    inventory: list[InventoryItemState] = Field(default_factory=list)
    # inventory entries offered in an open trade: cannot be dropped, used, equipped, sold, banked
    locked_item_ids: list[UUID] = Field(default_factory=list)
    # quests ever accepted; loaded for players of an action snapshot (else empty)
    quests: list[QuestState] = Field(default_factory=list)

    def item(self, inventory_item_id: UUID) -> InventoryItemState | None:
        return next((i for i in self.inventory if i.id == inventory_item_id), None)

    def quest(self, quest_id: str) -> QuestState | None:
        return next((q for q in self.quests if q.quest_id == quest_id), None)

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


class InviteSnapshot(BaseModel):
    inviter_id: UUID
    player_id: UUID
    group_id: UUID | None  # None while the inviter has no group yet
    expires_at: datetime

    def valid_at(self, now: datetime) -> bool:
        return self.expires_at > now


class GroupActionSnapshot(BaseModel):
    """Everything a group action may touch (wherever the players are): the actor, the other
    player involved (invitee, inviter, kicked member), all members of their groups, and the
    open invitations from or to these players. Member order = join order (oldest first)."""

    actor: PlayerSnapshot
    players: list[PlayerSnapshot] = Field(default_factory=list)  # everyone else involved
    groups: list[GroupSnapshot] = Field(default_factory=list)
    invites: list[InviteSnapshot] = Field(default_factory=list)

    def player(self, player_id: UUID) -> PlayerSnapshot | None:
        if player_id == self.actor.id:
            return self.actor
        return next((p for p in self.players if p.id == player_id), None)

    def group(self, group_id: UUID) -> GroupSnapshot | None:
        return next((g for g in self.groups if g.id == group_id), None)

    def invite(self, inviter_id: UUID, player_id: UUID) -> InviteSnapshot | None:
        return next(
            (i for i in self.invites if i.inviter_id == inviter_id and i.player_id == player_id),
            None,
        )


class TradeOfferSnapshot(BaseModel):
    player_id: UUID
    inventory_item_id: UUID
    quantity: int = Field(ge=1)


class TradeSnapshot(BaseModel):
    id: UUID
    initiator_id: UUID
    partner_id: UUID
    room_id: str
    status: Literal["requested", "open", "completed", "cancelled"]
    initiator_gold: int = 0
    partner_gold: int = 0
    initiator_confirmed: bool = False
    partner_confirmed: bool = False
    revision: int = 0
    expires_at: datetime
    offers: list[TradeOfferSnapshot] = Field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.status in ("requested", "open")

    def involves(self, player_id: UUID) -> bool:
        return player_id in (self.initiator_id, self.partner_id)

    def other(self, player_id: UUID) -> UUID:
        return self.partner_id if player_id == self.initiator_id else self.initiator_id

    def gold_of(self, player_id: UUID) -> int:
        return self.initiator_gold if player_id == self.initiator_id else self.partner_gold

    def offers_of(self, player_id: UUID) -> list[TradeOfferSnapshot]:
        return [o for o in self.offers if o.player_id == player_id]


class TradeActionSnapshot(BaseModel):
    """A trade action: the actor and the other trader (with inventories), and every active
    trade of either of them plus the trade the action names (locked: Trade -> Player ->
    InventoryItem -> TradeOffer)."""

    actor: PlayerSnapshot
    partner: PlayerSnapshot | None = None
    trades: list[TradeSnapshot] = Field(default_factory=list)

    def player(self, player_id: UUID) -> PlayerSnapshot | None:
        if player_id == self.actor.id:
            return self.actor
        if self.partner is not None and player_id == self.partner.id:
            return self.partner
        return None

    def trade(self, trade_id: UUID) -> TradeSnapshot | None:
        return next((t for t in self.trades if t.id == trade_id), None)

    def active_trades_of(self, player_id: UUID) -> list[TradeSnapshot]:
        return [t for t in self.trades if t.active and t.involves(player_id)]


class ActionSnapshot(BaseModel):
    """Relevant slice of the world for one action.

    - ``room_players``: other online players in the room the action happens in (actor excluded).
      For ``move`` this is the *destination* room.
    - ``monsters``: monster instances in that room.
    - ``participations``: combat participations for those monsters.
    - ``floor_items``: items lying in that room.
    - ``groups``: groups of the actor and of all ``room_players`` (needed for kill credit and
      round-robin loot of a tagging party that is not the actor's).
    """

    actor: PlayerSnapshot
    room_players: list[PlayerSnapshot] = Field(default_factory=list)
    monsters: list[MonsterSnapshot] = Field(default_factory=list)
    participations: list[ParticipationSnapshot] = Field(default_factory=list)
    groups: list[GroupSnapshot] = Field(default_factory=list)
    floor_items: list[FloorItemSnapshot] = Field(default_factory=list)
    # the actor's bank storage; only loaded (and locked) for bank actions
    bank: list[InventoryItemState] = Field(default_factory=list)

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
