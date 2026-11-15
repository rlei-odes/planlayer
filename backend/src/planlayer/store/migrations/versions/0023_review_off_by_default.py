"""HR's review before approval is off unless HR switches it on for a round (ADR 0024). Rounds
that have personnel costs already keep their setting.

Revision ID: 0023
Revises: 0022
"""

from collections.abc import Sequence

from alembic import op

from planlayer.store.migrate import migration_schema

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    s = migration_schema()
    op.execute(f"ALTER TABLE {s}.pay_setting ALTER COLUMN review_required SET DEFAULT false")


def downgrade() -> None:
    s = migration_schema()
    op.execute(f"ALTER TABLE {s}.pay_setting ALTER COLUMN review_required SET DEFAULT true")
