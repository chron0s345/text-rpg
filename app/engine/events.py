"""Result types of engine functions: state changes + events (ARCHITECTURE.md, Engine-Schnittstelle).

Update models use pydantic's ``model_fields_set``: only explicitly set fields are applied by the
service (``update.model_dump(exclude_unset=True)``). This allows setting a field to ``None``
(e.g. clearing ``combat_target_id``) without ambiguity.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.content.schemas import PlayerState
from app.engine.state import InventoryItemState, QuestState

# --- Audiences --------------------------------------------------------------


class ActorAudience(BaseModel):
    """The acting player (delivered via HTTP response, not SSE)."""

    kind: Literal["actor"] = "actor"


class RoomAudience(BaseModel):
    kind: Literal["room"] = "room"
    room_id: str


class GroupAudience(BaseModel):
    kind: Literal["group"] = "group"
    group_id: UUID


class PlayersAudience(BaseModel):
    kind: Literal["players"] = "players"
    player_ids: list[UUID]


class GlobalAudience(BaseModel):
    kind: Literal["global"] = "global"


Audience = Annotated[
    ActorAudience | RoomAudience | GroupAudience | PlayersAudience | GlobalAudience,
    Field(discriminator="kind"),
]


class GameEvent(BaseModel):
    kind: str  # e.g. "combat.hit", "monster.killed", "player.died", "move.enter"
    audience: Audience
    text: str  # rendered, player-facing message (German)
    exclude_player_ids: list[UUID] = Field(default_factory=list)  # e.g. actor in room broadcasts
    data: dict[str, Any] = Field(default_factory=dict)


# --- State changes ----------------------------------------------------------


class PlayerUpdate(BaseModel):
    player_id: UUID
    hp: int | None = None
    base_max_hp: int | None = None
    base_attack: int | None = None
    base_defense: int | None = None
    xp: int | None = None
    level: int | None = None
    gold: int | None = None
    current_room_id: str | None = None
    checkpoint_room_id: str | None = None
    state: PlayerState | None = None
    combat_target_id: UUID | None = None
    online: bool | None = None  # login/logout (engine/presence.py)
    disconnected_at: datetime | None = None
    group_id: UUID | None = None  # engine/groups.py
    group_joined_at: datetime | None = None
    achievements: list[str] | None = None  # engine/achievements.py: the full new list
    title: str | None = None
    kills: dict[str, int] | None = None  # the full new tally


class MonsterUpdate(BaseModel):
    monster_instance_id: UUID
    monster_id: str | None = None  # a respawn may switch to/from a rare variant
    hp: int | None = None
    alive: bool | None = None
    last_combat_at: datetime | None = None
    respawn_at: datetime | None = None
    tagged_by_party: str | None = None


class ParticipationUpsert(BaseModel):
    monster_instance_id: UUID
    player_id: UUID
    party_key: str
    damage_dealt: int


class ParticipationDelete(BaseModel):
    monster_instance_id: UUID
    player_id: UUID


class InventoryUpsert(BaseModel):
    player_id: UUID
    item: InventoryItemState


class BankUpsert(BaseModel):
    player_id: UUID
    item: InventoryItemState  # equipped_slot is always None in the bank


class FloorItemCreate(BaseModel):
    id: UUID
    room_id: str
    item_id: str
    quantity: int
    source: Literal["spawn", "drop", "loot"]
    dropped_by_account_id: UUID | None = None
    expires_at: datetime | None = None


class FloorItemUpdate(BaseModel):
    id: UUID
    quantity: int = Field(ge=1)


class ItemSpawnUpdate(BaseModel):
    room_id: str
    item_id: str
    next_spawn_at: datetime | None


class GroupUpdate(BaseModel):
    """Only explicitly set fields are applied (like ``PlayerUpdate``)."""

    group_id: UUID
    loot_cursor: int | None = None
    leader_id: UUID | None = None


class GroupCreate(BaseModel):
    id: UUID
    leader_id: UUID
    created_at: datetime


class TradeCreate(BaseModel):
    id: UUID
    initiator_id: UUID
    partner_id: UUID
    room_id: str
    expires_at: datetime
    created_at: datetime


class TradeUpdate(BaseModel):
    """Only explicitly set fields are applied."""

    trade_id: UUID
    status: Literal["requested", "open", "completed", "cancelled"] | None = None
    initiator_gold: int | None = None
    partner_gold: int | None = None
    initiator_confirmed: bool | None = None
    partner_confirmed: bool | None = None
    revision: int | None = None


class TradeOfferUpsert(BaseModel):
    trade_id: UUID
    player_id: UUID
    inventory_item_id: UUID
    quantity: int = Field(ge=1)


class TradeOfferDelete(BaseModel):
    trade_id: UUID
    inventory_item_id: UUID


class TradeLogCreate(BaseModel):
    trade_id: UUID
    completed_at: datetime
    summary: dict[str, Any]


class InviteUpsert(BaseModel):
    inviter_id: UUID
    player_id: UUID
    group_id: UUID | None
    expires_at: datetime


class InviteDelete(BaseModel):
    inviter_id: UUID
    player_id: UUID


class QuestUpsert(BaseModel):
    player_id: UUID
    quest_id: str
    status: str
    step: int
    progress: int
    times_completed: int


class QuestDelete(BaseModel):
    player_id: UUID
    quest_id: str


class StateChanges(BaseModel):
    """Final state changes of one engine call.

    The lists never contradict each other: whatever is deleted (inventory entries, floor items,
    participations, incl. ``participations_clear_monster_ids``) never also appears as a write.
    Deletions win (see ``ResultBuilder._deletions_win``). The service may therefore apply the
    lists in any order.
    """

    players: list[PlayerUpdate] = Field(default_factory=list)
    monsters: list[MonsterUpdate] = Field(default_factory=list)
    participations_upsert: list[ParticipationUpsert] = Field(default_factory=list)
    participations_delete: list[ParticipationDelete] = Field(default_factory=list)
    participations_clear_monster_ids: list[UUID] = Field(default_factory=list)
    inventory_upsert: list[InventoryUpsert] = Field(default_factory=list)
    inventory_delete: list[UUID] = Field(default_factory=list)
    bank_upsert: list[BankUpsert] = Field(default_factory=list)
    bank_delete: list[UUID] = Field(default_factory=list)
    floor_items_create: list[FloorItemCreate] = Field(default_factory=list)
    floor_items_update: list[FloorItemUpdate] = Field(default_factory=list)
    floor_items_delete: list[UUID] = Field(default_factory=list)
    item_spawns: list[ItemSpawnUpdate] = Field(default_factory=list)
    groups: list[GroupUpdate] = Field(default_factory=list)
    groups_create: list[GroupCreate] = Field(default_factory=list)
    groups_delete: list[UUID] = Field(default_factory=list)
    invites_upsert: list[InviteUpsert] = Field(default_factory=list)
    invites_delete: list[InviteDelete] = Field(default_factory=list)
    # trades; offers are deleted before inventory changes are applied (offered items are
    # protected against deletion in the database)
    trades_create: list[TradeCreate] = Field(default_factory=list)
    trades: list[TradeUpdate] = Field(default_factory=list)
    trade_offers_upsert: list[TradeOfferUpsert] = Field(default_factory=list)
    trade_offers_delete: list[TradeOfferDelete] = Field(default_factory=list)
    trade_logs: list[TradeLogCreate] = Field(default_factory=list)
    quests_upsert: list[QuestUpsert] = Field(default_factory=list)
    quests_delete: list[QuestDelete] = Field(default_factory=list)


class ActionResult(BaseModel):
    changes: StateChanges = Field(default_factory=StateChanges)
    events: list[GameEvent] = Field(default_factory=list)


# --- Builder ----------------------------------------------------------------


class ResultBuilder:
    """Accumulates changes while an engine function runs; merges updates per entity."""

    def __init__(self, actor_id: UUID | None = None) -> None:
        self.actor_id = actor_id
        self._players: dict[UUID, dict[str, Any]] = {}
        self._monsters: dict[UUID, dict[str, Any]] = {}
        self.changes = StateChanges()
        self.events: list[GameEvent] = []

    def update_player(self, player_id: UUID, **fields: Any) -> None:
        self._players.setdefault(player_id, {}).update(fields)

    def update_monster(self, monster_instance_id: UUID, **fields: Any) -> None:
        self._monsters.setdefault(monster_instance_id, {}).update(fields)

    def upsert_quest(self, player_id: UUID, quest: QuestState) -> None:
        """Records the quest's current state; a later upsert of the same quest replaces it."""
        self.changes.quests_upsert = [
            u
            for u in self.changes.quests_upsert
            if (u.player_id, u.quest_id) != (player_id, quest.quest_id)
        ]
        self.changes.quests_upsert.append(
            QuestUpsert(
                player_id=player_id,
                quest_id=quest.quest_id,
                status=quest.status,
                step=quest.step,
                progress=quest.progress,
                times_completed=quest.times_completed,
            )
        )

    def upsert_inventory(self, player_id: UUID, entries: list[InventoryItemState]) -> None:
        """Records new/changed stacks; a later upsert of the same entry replaces the earlier one."""
        unique = {e.id: e for e in entries}
        self.changes.inventory_upsert = [
            u for u in self.changes.inventory_upsert if u.item.id not in unique
        ]
        self.changes.inventory_upsert.extend(
            InventoryUpsert(player_id=player_id, item=e.model_copy()) for e in unique.values()
        )

    def upsert_bank(self, player_id: UUID, entries: list[InventoryItemState]) -> None:
        unique = {e.id: e for e in entries}
        self.changes.bank_upsert = [u for u in self.changes.bank_upsert if u.item.id not in unique]
        self.changes.bank_upsert.extend(
            BankUpsert(player_id=player_id, item=e.model_copy()) for e in unique.values()
        )

    def event(
        self,
        kind: str,
        audience: ActorAudience | RoomAudience | GroupAudience | PlayersAudience | GlobalAudience,
        text: str,
        *,
        exclude: list[UUID] | None = None,
        **data: Any,
    ) -> None:
        self.events.append(
            GameEvent(
                kind=kind,
                audience=audience,
                text=text,
                exclude_player_ids=exclude or [],
                data=data,
            )
        )

    def to_actor(self, kind: str, text: str, **data: Any) -> None:
        self.event(kind, ActorAudience(), text, **data)

    def to_player(self, player_id: UUID, kind: str, text: str, **data: Any) -> None:
        """Personal message; the actor gets it via the HTTP response, others via SSE."""
        if player_id == self.actor_id:
            self.to_actor(kind, text, **data)
        else:
            self.event(kind, PlayersAudience(player_ids=[player_id]), text, **data)

    def build(self) -> ActionResult:
        self.changes.players = [
            PlayerUpdate.model_validate({"player_id": pid, **fields})
            for pid, fields in self._players.items()
        ]
        self.changes.monsters = [
            MonsterUpdate.model_validate({"monster_instance_id": mid, **fields})
            for mid, fields in self._monsters.items()
        ]
        self._deletions_win()
        # copies: later writes to the builder can never bypass the guard above
        return ActionResult(changes=self.changes.model_copy(deep=True), events=list(self.events))

    def _deletions_win(self) -> None:
        """Guarantees the StateChanges contract: nothing is both written and deleted.

        Example: using a potion in combat upserts the reduced stack; if the counter-attack kills
        the player and the loss roll takes that stack, the delete must win. Otherwise applying
        upserts after deletes would bring the lost stack back.
        """
        changes = self.changes
        deleted_items = set(changes.inventory_delete)
        changes.inventory_upsert = [
            u for u in changes.inventory_upsert if u.item.id not in deleted_items
        ]
        deleted_bank = set(changes.bank_delete)
        changes.bank_upsert = [u for u in changes.bank_upsert if u.item.id not in deleted_bank]
        deleted_floor = set(changes.floor_items_delete)
        changes.floor_items_update = [
            u for u in changes.floor_items_update if u.id not in deleted_floor
        ]
        changes.floor_items_create = [
            c for c in changes.floor_items_create if c.id not in deleted_floor
        ]
        cleared = set(changes.participations_clear_monster_ids)
        ended = {(d.monster_instance_id, d.player_id) for d in changes.participations_delete}
        changes.participations_upsert = [
            u
            for u in changes.participations_upsert
            if u.monster_instance_id not in cleared
            and (u.monster_instance_id, u.player_id) not in ended
        ]
        changes.participations_delete = [
            d for d in changes.participations_delete if d.monster_instance_id not in cleared
        ]
        deleted_groups = set(changes.groups_delete)
        changes.groups = [g for g in changes.groups if g.group_id not in deleted_groups]
        deleted_offers = {(d.trade_id, d.inventory_item_id) for d in changes.trade_offers_delete}
        changes.trade_offers_upsert = [
            u
            for u in changes.trade_offers_upsert
            if (u.trade_id, u.inventory_item_id) not in deleted_offers
        ]
        deleted_invites = {(d.inviter_id, d.player_id) for d in changes.invites_delete}
        changes.invites_upsert = [
            u for u in changes.invites_upsert if (u.inviter_id, u.player_id) not in deleted_invites
        ]


def rejected(text: str) -> ActionResult:
    """Action not allowed in current state: no HTTP error, just a log message (ARCHITECTURE.md)."""
    builder = ResultBuilder()
    builder.to_actor("action.rejected", text)
    return builder.build()
