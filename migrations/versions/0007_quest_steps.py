"""Quests with several steps: the current step per character.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quest_progress",
        sa.Column("step", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("quest_progress", "step", server_default=None)
    op.create_check_constraint(op.f("ck_quest_progress_step"), "quest_progress", "step >= 0")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_quest_progress_step"), "quest_progress", type_="check")
    op.drop_column("quest_progress", "step")
