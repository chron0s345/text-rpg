"""Relax the group membership check: a member needs a join time, a former member may keep one.

``(group_id IS NULL) = (group_joined_at IS NULL)`` made the ``ON DELETE SET NULL`` of
``player.group_id`` fail: deleting a group from the database side (e.g. hard-deleting the
leader's account, which cascades to the group) nulls ``group_id`` but cannot touch
``group_joined_at``, so the whole delete was rejected.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_player_group_joined"), "player", type_="check")
    op.create_check_constraint(
        op.f("ck_player_group_joined"),
        "player",
        "group_id IS NULL OR group_joined_at IS NOT NULL",
    )


def downgrade() -> None:
    op.execute("UPDATE player SET group_joined_at = NULL WHERE group_id IS NULL")
    op.drop_constraint(op.f("ck_player_group_joined"), "player", type_="check")
    op.create_check_constraint(
        op.f("ck_player_group_joined"),
        "player",
        "(group_id IS NULL) = (group_joined_at IS NULL)",
    )
