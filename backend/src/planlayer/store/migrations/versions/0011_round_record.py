"""How a round was created (ADR 0014): who created it, when, and what it carried over from the
round it was set up like. A note per comparison column, shown to planners with the column.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

from alembic import op

from planlayer.store.migrate import migration_schema

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    s = migration_schema()
    # Alembic's column operations aren't schema-translated.
    op.execute(
        f"ALTER TABLE {s}.planning_round"
        " ADD COLUMN created_by text,"
        " ADD COLUMN created_at timestamptz,"
        " ADD COLUMN carried jsonb"
    )
    op.execute(f"ALTER TABLE {s}.comparison ADD COLUMN note text")


def downgrade() -> None:
    s = migration_schema()
    op.execute(f"ALTER TABLE {s}.comparison DROP COLUMN note")
    op.execute(
        f"ALTER TABLE {s}.planning_round"
        " DROP COLUMN carried, DROP COLUMN created_at, DROP COLUMN created_by"
    )
