"""Character classes: every character has one; existing ones become adventurers (no bonus,
so their stats stay as they are) and may pick a class once.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "player",
        sa.Column("class_id", sa.String(length=32), nullable=False, server_default="adventurer"),
    )


def downgrade() -> None:
    op.drop_column("player", "class_id")
