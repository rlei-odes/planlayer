"""Published signs (ADR 0019): the installation's sign convention for published amounts.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

from alembic import op

from planlayer.store.migrate import migration_schema

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    s = migration_schema()
    # Alembic's column operations aren't schema-translated.
    op.execute(
        f"ALTER TABLE {s}.installation ADD COLUMN published_signs text NOT NULL DEFAULT 'ledger'"
        " CONSTRAINT published_signs CHECK (published_signs IN ('ledger', 'result'))"
    )


def downgrade() -> None:
    s = migration_schema()
    op.execute(f"ALTER TABLE {s}.installation DROP COLUMN published_signs")
