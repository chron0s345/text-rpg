"""Quest progress per character.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quest_progress",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("quest_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("times_completed", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'completed')", name=op.f("ck_quest_progress_status")
        ),
        sa.CheckConstraint("progress >= 0", name=op.f("ck_quest_progress_progress")),
        sa.CheckConstraint("times_completed >= 0", name=op.f("ck_quest_progress_times_completed")),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["player.id"],
            name=op.f("fk_quest_progress_player_id_player"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("player_id", "quest_id", name=op.f("pk_quest_progress")),
    )


def downgrade() -> None:
    op.drop_table("quest_progress")
