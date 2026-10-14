"""Achievements: earned ids and the chosen title per character.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "player",
        sa.Column(
            "achievements",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column("player", sa.Column("title", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("player", "title")
    op.drop_column("player", "achievements")
