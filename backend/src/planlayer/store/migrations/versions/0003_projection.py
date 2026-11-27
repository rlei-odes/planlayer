"""Projection comparison columns: actuals of a year to a month, extrapolated to the full year.
``through`` is the last month a budget's projection counts as booked; a forecast's projection
uses its actuals through.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

from alembic import op

from planlayer.store.migrate import every_row, migration_schema

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    s = migration_schema()
    op.execute(
        f"ALTER TABLE {s}.comparison "
        "ADD COLUMN through date, "
        "DROP CONSTRAINT ck_comparison_kind, "
        "ADD CONSTRAINT ck_comparison_kind CHECK "
        "(kind IN ('prior_year_actuals', 'actuals', 'version', 'projection')), "
        "ADD CONSTRAINT ck_comparison_through CHECK "
        "(through IS NULL OR (kind = 'projection' AND extract(day FROM through) = 1))"
    )


def downgrade() -> None:
    """Projection columns go; the other columns keep their order (positions are only sorted)."""
    s = migration_schema()
    with every_row(op.get_bind(), s, ("comparison",)):
        op.execute(f"DELETE FROM {s}.comparison WHERE kind = 'projection'")
    op.execute(
        f"ALTER TABLE {s}.comparison "
        "DROP CONSTRAINT ck_comparison_through, DROP COLUMN through, "
        "DROP CONSTRAINT ck_comparison_kind, ADD CONSTRAINT ck_comparison_kind CHECK "
        "(kind IN ('prior_year_actuals', 'actuals', 'version'))"
    )
