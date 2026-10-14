"""SQLModel tables: mutable world state only (ARCHITECTURE.md, "Zustandsmodell").

Content (rooms, monsters, items, ...) is never stored here; rows reference it by stable string IDs.
Integrity rules that must hold no matter which code path writes are enforced by the database:
check constraints (no negative gold/quantities, valid states), one online character per account,
unique character names among non-deleted characters, composite keys for link tables.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

SQLModel.metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

PLAYER_STATES = ("exploring", "in_combat", "dead")
EQUIP_SLOTS = ("weapon", "head", "chest", "legs", "feet", "bag")
FLOOR_SOURCES = ("spawn", "drop", "loot")
TRADE_STATUSES = ("requested", "open", "completed", "cancelled")


def utc_now() -> datetime:
    return datetime.now(UTC)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def timestamp(*, nullable: bool = True, index: bool = False) -> Any:
    """timestamptz column. All times are stored as UTC."""
    return Field(default=None, sa_type=DateTime(timezone=True), nullable=nullable, index=index)


class Account(SQLModel, table=True):
    __tablename__ = "account"
    __table_args__ = (
        Index("uq_account_username_lower", func.lower(text("username")), unique=True),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    username: str = Field(max_length=32)
    password_hash: str
    created_at: datetime = Field(default_factory=utc_now, sa_type=DateTime(timezone=True))
    # raised on a password change; session cookies of an older version are no longer valid
    session_version: int = Field(default=0, sa_column_kwargs={"server_default": "0"})


class Player(SQLModel, table=True):
    """One character. ``group_joined_at`` orders group members (oldest member becomes leader)."""

    __tablename__ = "player"
    __table_args__ = (
        CheckConstraint(_in("state", PLAYER_STATES), name="state"),
        CheckConstraint("hp >= 0", name="hp"),
        CheckConstraint("gold >= 0", name="gold"),
        CheckConstraint("xp >= 0", name="xp"),
        CheckConstraint("level >= 1", name="level"),
        # members have a join time; ON DELETE SET NULL of group_id may leave a stale one behind
        CheckConstraint("group_id IS NULL OR group_joined_at IS NOT NULL", name="group_joined"),
        # character names are unique (case-insensitive) among non-deleted characters
        Index(
            "uq_player_name_lower",
            func.lower(text("name")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # only one character per account can be logged in at a time
        Index(
            "uq_player_online_account",
            "account_id",
            unique=True,
            postgresql_where=text("online"),
        ),
        Index("ix_player_room_online", "current_room_id", postgresql_where=text("online")),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    account_id: UUID = Field(foreign_key="account.id", ondelete="CASCADE", index=True)
    name: str = Field(max_length=20)
    hp: int
    base_max_hp: int
    base_attack: int
    base_defense: int
    xp: int = 0
    level: int = 1
    gold: int = 0
    current_room_id: str
    checkpoint_room_id: str
    state: str = "exploring"
    combat_target_id: UUID | None = Field(
        default=None, foreign_key="monster_instance.id", ondelete="SET NULL"
    )
    group_id: UUID | None = Field(
        default=None, foreign_key="player_group.id", ondelete="SET NULL", index=True
    )
    group_joined_at: datetime | None = timestamp()
    online: bool = False
    disconnected_at: datetime | None = timestamp()
    last_action_at: datetime | None = timestamp()
    deleted_at: datetime | None = timestamp()


class InventoryItem(SQLModel, table=True):
    __tablename__ = "inventory_item"
    __table_args__ = (
        CheckConstraint("quantity >= 1", name="quantity"),
        CheckConstraint(
            f"equipped_slot IS NULL OR {_in('equipped_slot', EQUIP_SLOTS)}", name="equipped_slot"
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    player_id: UUID = Field(foreign_key="player.id", ondelete="CASCADE", index=True)
    item_id: str
    quantity: int = 1
    equipped_slot: str | None = None


class MonsterInstance(SQLModel, table=True):
    __tablename__ = "monster_instance"
    __table_args__ = (
        CheckConstraint("hp >= 0", name="hp"),
        Index("ix_monster_instance_respawn_due", "respawn_at", postgresql_where=text("NOT alive")),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    monster_id: str
    room_id: str = Field(index=True)
    hp: int  # as of last_combat_at; regeneration is applied lazily
    alive: bool = True
    last_combat_at: datetime | None = timestamp()
    respawn_at: datetime | None = timestamp()
    tagged_by_party: str | None = None


class CombatParticipation(SQLModel, table=True):
    __tablename__ = "combat_participation"
    __table_args__ = (CheckConstraint("damage_dealt >= 0", name="damage_dealt"),)

    monster_instance_id: UUID = Field(
        foreign_key="monster_instance.id", ondelete="CASCADE", primary_key=True
    )
    player_id: UUID = Field(
        foreign_key="player.id", ondelete="CASCADE", primary_key=True, index=True
    )
    party_key: str  # "group:{id}" or "solo:{player_id}"
    damage_dealt: int = 0


class Group(SQLModel, table=True):
    """Membership lives in ``Player.group_id``. (Table name avoids the SQL keyword GROUP.)"""

    __tablename__ = "player_group"
    __table_args__ = (CheckConstraint("loot_cursor >= 0", name="loot_cursor"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    leader_id: UUID = Field(
        sa_column=Column(
            Uuid,
            # player.group_id -> player_group and player_group.leader_id -> player form a cycle
            ForeignKey(
                "player.id",
                use_alter=True,
                name="fk_player_group_leader_id_player",
                ondelete="CASCADE",  # hard-deleting the leader's account dissolves the group
            ),
            nullable=False,
        )
    )
    loot_cursor: int = 0
    created_at: datetime = Field(default_factory=utc_now, sa_type=DateTime(timezone=True))


class GroupInvite(SQLModel, table=True):
    """``group_id`` is NULL while the inviter has no group yet; accepting creates the group
    with the inviter as leader (ARCHITECTURE.md, "Gruppen")."""

    __tablename__ = "group_invite"
    __table_args__ = (CheckConstraint("inviter_id <> player_id", name="distinct_players"),)

    inviter_id: UUID = Field(foreign_key="player.id", ondelete="CASCADE", primary_key=True)
    player_id: UUID = Field(
        foreign_key="player.id", ondelete="CASCADE", primary_key=True, index=True
    )
    group_id: UUID | None = Field(default=None, foreign_key="player_group.id", ondelete="CASCADE")
    expires_at: datetime = Field(sa_type=DateTime(timezone=True), index=True)


class FloorItem(SQLModel, table=True):
    __tablename__ = "floor_item"
    __table_args__ = (
        CheckConstraint("quantity >= 1", name="quantity"),
        CheckConstraint(_in("source", FLOOR_SOURCES), name="source"),
        CheckConstraint("expires_at IS NOT NULL OR source = 'spawn'", name="expires_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    room_id: str = Field(index=True)
    item_id: str
    quantity: int = 1
    source: str
    dropped_by_account_id: UUID | None = Field(
        default=None, foreign_key="account.id", ondelete="SET NULL"
    )
    expires_at: datetime | None = timestamp(index=True)


class ItemSpawnState(SQLModel, table=True):
    """One row per room item spawn rule; exists once the spawn has been seeded."""

    __tablename__ = "item_spawn_state"

    room_id: str = Field(primary_key=True)
    item_id: str = Field(primary_key=True)
    next_spawn_at: datetime | None = timestamp(index=True)


class BankItem(SQLModel, table=True):
    __tablename__ = "bank_item"
    __table_args__ = (CheckConstraint("quantity >= 1", name="quantity"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    player_id: UUID = Field(foreign_key="player.id", ondelete="CASCADE", index=True)
    item_id: str
    quantity: int = 1


class Trade(SQLModel, table=True):
    __tablename__ = "trade"
    __table_args__ = (
        CheckConstraint(_in("status", TRADE_STATUSES), name="status"),
        CheckConstraint("initiator_id <> partner_id", name="distinct_players"),
        CheckConstraint("initiator_gold >= 0 AND partner_gold >= 0", name="gold"),
        CheckConstraint("revision >= 0", name="revision"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    initiator_id: UUID = Field(foreign_key="player.id", ondelete="CASCADE", index=True)
    partner_id: UUID = Field(foreign_key="player.id", ondelete="CASCADE", index=True)
    room_id: str
    status: str = "requested"
    initiator_gold: int = 0
    partner_gold: int = 0
    initiator_confirmed: bool = False
    partner_confirmed: bool = False
    revision: int = 0
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))  # only relevant for "requested"
    created_at: datetime = Field(default_factory=utc_now, sa_type=DateTime(timezone=True))


class TradeOffer(SQLModel, table=True):
    __tablename__ = "trade_offer"
    __table_args__ = (CheckConstraint("quantity >= 1", name="quantity"),)

    trade_id: UUID = Field(foreign_key="trade.id", ondelete="CASCADE", primary_key=True)
    # NO ACTION (no ondelete): an offered item cannot be deleted while the offer exists, so a
    # partner never confirms an offer that silently changed. Unlike RESTRICT it is checked at
    # the end of the statement, so deleting a whole player (offers cascade too) still works.
    inventory_item_id: UUID = Field(foreign_key="inventory_item.id", primary_key=True)
    player_id: UUID = Field(foreign_key="player.id", ondelete="CASCADE")
    quantity: int


class TradeLog(SQLModel, table=True):
    """Immutable audit trail; deliberately without foreign keys so it outlives trades/players."""

    __tablename__ = "trade_log"

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    trade_id: UUID = Field(index=True)
    completed_at: datetime = Field(sa_type=DateTime(timezone=True))
    summary: dict[str, Any] = Field(sa_type=JSONB)


class LogEntry(SQLModel, table=True):
    __tablename__ = "log_entry"
    __table_args__ = (Index("ix_log_entry_player_id_id", "player_id", "id"),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    player_id: UUID = Field(foreign_key="player.id", ondelete="CASCADE")
    created_at: datetime = Field(sa_type=DateTime(timezone=True))
    kind: str
    text: str


class LoginThrottle(SQLModel, table=True):
    """Counts login/registration attempts per key ("user:<name>", "ip:<address>", ...) across all
    server processes (services/login_guard.py). ``expires_at``: after it the row is irrelevant
    and the world ticker deletes it."""

    __tablename__ = "login_throttle"
    __table_args__ = (CheckConstraint("attempts >= 0", name="attempts"),)

    key: str = Field(primary_key=True, max_length=200)
    attempts: int = 0
    window_started_at: datetime = Field(sa_type=DateTime(timezone=True))
    locked_until: datetime | None = timestamp()
    expires_at: datetime = Field(sa_type=DateTime(timezone=True), index=True)
