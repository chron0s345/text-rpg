"""Initial schema: accounts, characters, world state, trades, logs.

Revision ID: 0001
Revises:
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account")),
    )
    op.create_index(
        "uq_account_username_lower", "account", [sa.text("lower(username)")], unique=True
    )
    op.create_table(
        "item_spawn_state",
        sa.Column("room_id", sa.String(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("next_spawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("room_id", "item_id", name=op.f("pk_item_spawn_state")),
    )
    op.create_index(
        op.f("ix_item_spawn_state_next_spawn_at"),
        "item_spawn_state",
        ["next_spawn_at"],
        unique=False,
    )
    op.create_table(
        "monster_instance",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("monster_id", sa.String(), nullable=False),
        sa.Column("room_id", sa.String(), nullable=False),
        sa.Column("hp", sa.Integer(), nullable=False),
        sa.Column("alive", sa.Boolean(), nullable=False),
        sa.Column("last_combat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("respawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tagged_by_party", sa.String(), nullable=True),
        sa.CheckConstraint("hp >= 0", name=op.f("ck_monster_instance_hp")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monster_instance")),
    )
    op.create_index(
        "ix_monster_instance_respawn_due",
        "monster_instance",
        ["respawn_at"],
        unique=False,
        postgresql_where=sa.text("NOT alive"),
    )
    op.create_index(
        op.f("ix_monster_instance_room_id"), "monster_instance", ["room_id"], unique=False
    )
    op.create_table(
        "player_group",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("leader_id", sa.Uuid(), nullable=False),
        sa.Column("loot_cursor", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("loot_cursor >= 0", name=op.f("ck_player_group_loot_cursor")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_player_group")),
    )
    op.create_table(
        "trade_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("trade_id", sa.Uuid(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trade_log")),
    )
    op.create_index(op.f("ix_trade_log_trade_id"), "trade_log", ["trade_id"], unique=False)
    op.create_table(
        "floor_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.String(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("dropped_by_account_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "expires_at IS NOT NULL OR source = 'spawn'", name=op.f("ck_floor_item_expires_at")
        ),
        sa.CheckConstraint(
            "source IN ('spawn', 'drop', 'loot')", name=op.f("ck_floor_item_source")
        ),
        sa.CheckConstraint("quantity >= 1", name=op.f("ck_floor_item_quantity")),
        sa.ForeignKeyConstraint(
            ["dropped_by_account_id"],
            ["account.id"],
            name=op.f("fk_floor_item_dropped_by_account_id_account"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_floor_item")),
    )
    op.create_index(op.f("ix_floor_item_expires_at"), "floor_item", ["expires_at"], unique=False)
    op.create_index(op.f("ix_floor_item_room_id"), "floor_item", ["room_id"], unique=False)
    op.create_table(
        "player",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.Column("hp", sa.Integer(), nullable=False),
        sa.Column("base_max_hp", sa.Integer(), nullable=False),
        sa.Column("base_attack", sa.Integer(), nullable=False),
        sa.Column("base_defense", sa.Integer(), nullable=False),
        sa.Column("xp", sa.Integer(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("gold", sa.Integer(), nullable=False),
        sa.Column("current_room_id", sa.String(), nullable=False),
        sa.Column("checkpoint_room_id", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("combat_target_id", sa.Uuid(), nullable=True),
        sa.Column("group_id", sa.Uuid(), nullable=True),
        sa.Column("group_joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("online", sa.Boolean(), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('exploring', 'in_combat', 'dead')", name=op.f("ck_player_state")
        ),
        sa.CheckConstraint(
            "(group_id IS NULL) = (group_joined_at IS NULL)", name=op.f("ck_player_group_joined")
        ),
        sa.CheckConstraint("gold >= 0", name=op.f("ck_player_gold")),
        sa.CheckConstraint("hp >= 0", name=op.f("ck_player_hp")),
        sa.CheckConstraint("level >= 1", name=op.f("ck_player_level")),
        sa.CheckConstraint("xp >= 0", name=op.f("ck_player_xp")),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
            name=op.f("fk_player_account_id_account"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["combat_target_id"],
            ["monster_instance.id"],
            name=op.f("fk_player_combat_target_id_monster_instance"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["player_group.id"],
            name=op.f("fk_player_group_id_player_group"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_player")),
    )
    op.create_index(op.f("ix_player_account_id"), "player", ["account_id"], unique=False)
    op.create_index(op.f("ix_player_group_id"), "player", ["group_id"], unique=False)
    op.create_index(
        "ix_player_room_online",
        "player",
        ["current_room_id"],
        unique=False,
        postgresql_where=sa.text("online"),
    )
    op.create_index(
        "uq_player_name_lower",
        "player",
        [sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_player_online_account",
        "player",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("online"),
    )
    op.create_table(
        "bank_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity >= 1", name=op.f("ck_bank_item_quantity")),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["player.id"],
            name=op.f("fk_bank_item_player_id_player"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bank_item")),
    )
    op.create_index(op.f("ix_bank_item_player_id"), "bank_item", ["player_id"], unique=False)
    op.create_table(
        "combat_participation",
        sa.Column("monster_instance_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("party_key", sa.String(), nullable=False),
        sa.Column("damage_dealt", sa.Integer(), nullable=False),
        sa.CheckConstraint("damage_dealt >= 0", name=op.f("ck_combat_participation_damage_dealt")),
        sa.ForeignKeyConstraint(
            ["monster_instance_id"],
            ["monster_instance.id"],
            name=op.f("fk_combat_participation_monster_instance_id_monster_instance"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["player.id"],
            name=op.f("fk_combat_participation_player_id_player"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "monster_instance_id", "player_id", name=op.f("pk_combat_participation")
        ),
    )
    op.create_table(
        "group_invite",
        sa.Column("inviter_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["player_group.id"],
            name=op.f("fk_group_invite_group_id_player_group"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inviter_id"],
            ["player.id"],
            name=op.f("fk_group_invite_inviter_id_player"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["player.id"],
            name=op.f("fk_group_invite_player_id_player"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("inviter_id", "player_id", name=op.f("pk_group_invite")),
    )
    op.create_index(
        op.f("ix_group_invite_expires_at"), "group_invite", ["expires_at"], unique=False
    )
    op.create_table(
        "inventory_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("equipped_slot", sa.String(), nullable=True),
        sa.CheckConstraint(
            "equipped_slot IS NULL OR equipped_slot IN ('weapon', 'head', 'chest', 'legs', 'feet', 'bag')",
            name=op.f("ck_inventory_item_equipped_slot"),
        ),
        sa.CheckConstraint("quantity >= 1", name=op.f("ck_inventory_item_quantity")),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["player.id"],
            name=op.f("fk_inventory_item_player_id_player"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_item")),
    )
    op.create_index(
        op.f("ix_inventory_item_player_id"), "inventory_item", ["player_id"], unique=False
    )
    op.create_table(
        "log_entry",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["player.id"],
            name=op.f("fk_log_entry_player_id_player"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_log_entry")),
    )
    op.create_index("ix_log_entry_player_id_id", "log_entry", ["player_id", "id"], unique=False)
    op.create_table(
        "trade",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("initiator_id", sa.Uuid(), nullable=False),
        sa.Column("partner_id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("initiator_gold", sa.Integer(), nullable=False),
        sa.Column("partner_gold", sa.Integer(), nullable=False),
        sa.Column("initiator_confirmed", sa.Boolean(), nullable=False),
        sa.Column("partner_confirmed", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('requested', 'open', 'completed', 'cancelled')",
            name=op.f("ck_trade_status"),
        ),
        sa.CheckConstraint("initiator_gold >= 0 AND partner_gold >= 0", name=op.f("ck_trade_gold")),
        sa.CheckConstraint("initiator_id <> partner_id", name=op.f("ck_trade_distinct_players")),
        sa.CheckConstraint("revision >= 0", name=op.f("ck_trade_revision")),
        sa.ForeignKeyConstraint(
            ["initiator_id"],
            ["player.id"],
            name=op.f("fk_trade_initiator_id_player"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["partner_id"],
            ["player.id"],
            name=op.f("fk_trade_partner_id_player"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trade")),
    )
    op.create_index(op.f("ix_trade_initiator_id"), "trade", ["initiator_id"], unique=False)
    op.create_index(op.f("ix_trade_partner_id"), "trade", ["partner_id"], unique=False)
    op.create_table(
        "trade_offer",
        sa.Column("trade_id", sa.Uuid(), nullable=False),
        sa.Column("inventory_item_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity >= 1", name=op.f("ck_trade_offer_quantity")),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_item.id"],
            name=op.f("fk_trade_offer_inventory_item_id_inventory_item"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["player.id"],
            name=op.f("fk_trade_offer_player_id_player"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trade_id"],
            ["trade.id"],
            name=op.f("fk_trade_offer_trade_id_trade"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("trade_id", "inventory_item_id", name=op.f("pk_trade_offer")),
    )
    # ### end Alembic commands ###
    # player.group_id -> player_group and player_group.leader_id -> player form a cycle
    op.create_foreign_key(
        "fk_player_group_leader_id_player", "player_group", "player", ["leader_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_player_group_leader_id_player", "player_group", type_="foreignkey")
    op.drop_table("trade_offer")
    op.drop_table("trade")
    op.drop_table("log_entry")
    op.drop_table("inventory_item")
    op.drop_table("group_invite")
    op.drop_table("combat_participation")
    op.drop_table("bank_item")
    op.drop_table("player")
    op.drop_table("floor_item")
    op.drop_table("trade_log")
    op.drop_table("player_group")
    op.drop_table("monster_instance")
    op.drop_table("item_spawn_state")
    op.drop_table("account")
