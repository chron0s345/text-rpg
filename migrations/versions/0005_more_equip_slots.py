"""More equipment slots: shield (offhand), amulet (neck), ring.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "('weapon', 'head', 'chest', 'legs', 'feet', 'bag')"
NEW = "('weapon', 'offhand', 'head', 'chest', 'legs', 'feet', 'neck', 'ring', 'bag')"


def _replace(slots: str) -> None:
    op.drop_constraint(op.f("ck_inventory_item_equipped_slot"), "inventory_item", type_="check")
    op.create_check_constraint(
        op.f("ck_inventory_item_equipped_slot"),
        "inventory_item",
        f"equipped_slot IS NULL OR equipped_slot IN {slots}",
    )


def upgrade() -> None:
    _replace(NEW)


def downgrade() -> None:
    # items in the new slots go back into the backpack first
    op.execute(
        "UPDATE inventory_item SET equipped_slot = NULL "
        "WHERE equipped_slot IN ('offhand', 'neck', 'ring')"
    )
    _replace(OLD)
