"""Login throttle: attempts per user name and per client address (protection against password
guessing and hashing load).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login_throttle",
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_login_throttle_attempts")),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_login_throttle")),
    )
    op.create_index(
        op.f("ix_login_throttle_expires_at"), "login_throttle", ["expires_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_login_throttle_expires_at"), table_name="login_throttle")
    op.drop_table("login_throttle")
