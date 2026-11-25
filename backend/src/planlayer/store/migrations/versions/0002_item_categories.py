"""Categories in item lists (ADR 0017): an item is an item or a category heading, which has a
name only and groups the items below it up to the next heading.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

from planlayer.store.migrate import every_row, migration_schema

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    s = migration_schema()
    op.execute(
        f"ALTER TABLE {s}.item "
        "ADD COLUMN kind text NOT NULL DEFAULT 'item', "
        "ALTER COLUMN unit_price DROP NOT NULL, "
        "ADD CONSTRAINT ck_item_kind CHECK (kind IN ('item', 'heading')), "
        "ADD CONSTRAINT ck_item_heading CHECK ("
        "(kind = 'item' AND unit_price IS NOT NULL) OR (kind = 'heading' AND unit_price IS NULL "
        "AND supplier IS NULL AND comment IS NULL AND quantity = 1))"
    )


def downgrade() -> None:
    """Headings go; their items stay, in their order."""
    s = migration_schema()
    with every_row(op.get_bind(), s, ("item",)):
        op.execute(f"DELETE FROM {s}.item WHERE kind = 'heading'")
    op.execute(
        f"ALTER TABLE {s}.item DROP CONSTRAINT ck_item_heading, DROP CONSTRAINT ck_item_kind, "
        "DROP COLUMN kind, ALTER COLUMN unit_price SET NOT NULL"
    )
