from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Account(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str
    created_at: datetime = Field(default_factory=utc_now)


class Player(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    account_id: UUID = Field(foreign_key="account.id", index=True)
    name: str = Field(unique=True, index=True)
    hp: int
    base_max_hp: int
    base_attack: int
    base_defense: int
    xp: int = 0
    level: int = 1
    gold: int = 0
    current_room_id: str
    checkpoint_room_id: str
    state: str = "exploring"  # "exploring" | "in_combat" | "dead"
    combat_target_id: UUID | None = None
    group_id: UUID | None = None
    online: bool = False
    disconnected_at: datetime | None = None
    last_action_at: datetime | None = None
    deleted_at: datetime | None = None


class InventoryItem(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    player_id: UUID = Field(foreign_key="player.id", index=True)
    item_id: str
    quantity: int = 1
    equipped_slot: str | None = None


class MonsterInstance(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    monster_id: str
    room_id: str = Field(index=True)
    hp: int
    alive: bool = True
    last_combat_at: datetime | None = None
    respawn_at: datetime | None = None
    tagged_by_party: str | None = None


class CombatParticipation(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    monster_instance_id: UUID = Field(foreign_key="monsterinstance.id", index=True)
    player_id: UUID = Field(foreign_key="player.id", index=True)
    party_key: str  # "group:{id}" or "solo:{player_id}"
    damage_dealt: int = 0


class Group(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    leader_id: UUID = Field(foreign_key="player.id")
    loot_cursor: int = 0
    created_at: datetime = Field(default_factory=utc_now)


class GroupInvite(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    group_id: UUID = Field(foreign_key="group.id", index=True)
    player_id: UUID = Field(foreign_key="player.id", index=True)
    expires_at: datetime


class FloorItem(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    room_id: str = Field(index=True)
    item_id: str
    quantity: int = 1
    source: str = "drop"  # "spawn" | "drop" | "loot"
    dropped_by_account_id: UUID | None = None
    expires_at: datetime | None = None


class ItemSpawnState(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    room_id: str = Field(index=True)
    item_id: str
    next_spawn_at: datetime | None = None


class BankItem(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    player_id: UUID = Field(foreign_key="player.id", index=True)
    item_id: str
    quantity: int = 1


class Trade(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    initiator_id: UUID = Field(foreign_key="player.id", index=True)
    partner_id: UUID = Field(foreign_key="player.id", index=True)
    room_id: str
    status: str = "requested"  # "requested" | "open" | "completed" | "cancelled"
    initiator_gold: int = 0
    partner_gold: int = 0
    initiator_confirmed: bool = False
    partner_confirmed: bool = False
    revision: int = 0
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class TradeOffer(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    trade_id: UUID = Field(foreign_key="trade.id", index=True)
    player_id: UUID = Field(foreign_key="player.id")
    inventory_item_id: UUID = Field(foreign_key="inventoryitem.id")
    quantity: int = 1


class TradeLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    trade_id: UUID = Field(index=True)
    completed_at: datetime = Field(default_factory=utc_now)
    summary_json: str = "{}"


class LogEntry(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    player_id: UUID = Field(foreign_key="player.id", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    kind: str
    text: str
