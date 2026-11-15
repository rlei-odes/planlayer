"""Whether the sync reads HR's salaries from the source (ADR 0025). Off: HR uploads them.

Revision ID: 0024
Revises: 0023
"""

from collections.abc import Sequence

from alembic import op

from planlayer.store.migrate import migration_schema

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    s = migration_schema()
    op.execute(
        f"ALTER TABLE {s}.installation ADD COLUMN pay_from_source boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    s = migration_schema()
    op.execute(f"ALTER TABLE {s}.installation DROP COLUMN pay_from_source")
