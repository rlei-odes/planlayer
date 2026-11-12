"""Forecast rounds and quarter grain (ADR 0015): a round is planned by month or by quarter, and a
forecast closes the months up to its ``actuals_through``. The store refuses plan values in closed
months, for every writer.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

from alembic import op

from planlayer.store.migrate import migration_schema

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    s = migration_schema()
    # Alembic's column operations aren't schema-translated.
    op.execute(
        f"ALTER TABLE {s}.planning_round"
        " ADD COLUMN grain text NOT NULL DEFAULT 'month'"
        " CHECK (grain IN ('month', 'quarter')),"
        # The last closed month of a forecast (first day of the month); null: none closed.
        " ADD COLUMN actuals_through date"
        " CHECK (actuals_through = date_trunc('month', actuals_through)::date)"
    )
    # Statement level, like the lock trigger: one check per statement over the rows written.
    # Deleting a closed month's value is allowed: it only removes what shouldn't be there.
    op.execute(f"""
        CREATE FUNCTION {s}.lock_closed_months() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM new_rows t
                JOIN {s}.planning_round r ON r.version_id = t.version_id
                WHERE t.period <= r.actuals_through
            ) THEN
                RAISE EXCEPTION 'plan values are locked: the month is closed in this forecast'
                    USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$
    """)
    for event in ("INSERT", "UPDATE"):
        op.execute(f"""
            CREATE TRIGGER closed_{event.lower()} AFTER {event} ON {s}.fact_cost_center
            REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT
            EXECUTE FUNCTION {s}.lock_closed_months()
        """)


def downgrade() -> None:
    s = migration_schema()
    for event in ("insert", "update"):
        op.execute(f"DROP TRIGGER closed_{event} ON {s}.fact_cost_center")
    op.execute(f"DROP FUNCTION {s}.lock_closed_months()")
    op.execute(f"ALTER TABLE {s}.planning_round DROP COLUMN actuals_through, DROP COLUMN grain")
