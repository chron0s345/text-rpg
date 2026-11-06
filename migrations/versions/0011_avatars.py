"""Player avatars: the chosen portrait (sprites.yaml); NULL shows one picked by the id.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("player", sa.Column("avatar", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("player", "avatar")
