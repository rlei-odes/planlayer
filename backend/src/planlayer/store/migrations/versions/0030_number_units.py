"""The number format's units (the k or M after amounts in thousands or millions), and cents
hidden by default. The installation's default had cents shown; it takes the new default.

Revision ID: 0030
Revises: 0029
"""

from collections.abc import Sequence

from alembic import op

from planlayer.store.migrate import migration_schema

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    s = migration_schema()
    op.execute(f"""
        ALTER TABLE {s}.installation
            ADD COLUMN number_units boolean NOT NULL DEFAULT true,
            ALTER COLUMN number_decimals SET DEFAULT false
    """)
    op.execute(f"UPDATE {s}.installation SET number_decimals = false")
    op.execute(f"ALTER TABLE {s}.app_user ADD COLUMN number_units boolean")


def downgrade() -> None:
    s = migration_schema()
    op.execute(f"""
        ALTER TABLE {s}.installation
            DROP COLUMN number_units,
            ALTER COLUMN number_decimals SET DEFAULT true
    """)
    op.execute(f"ALTER TABLE {s}.app_user DROP COLUMN number_units")
