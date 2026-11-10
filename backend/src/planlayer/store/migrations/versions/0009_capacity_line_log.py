"""A log of changes to capacity lines (plan rate, state, spread), written by a trigger with the
request's actor and change batch, so a batch on the capacity sheet can be audited and undone
(ADR 0010, ADR 0013).

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA
STATE = (
    ("rate", sa.Numeric(18, 4)),
    ("variable_rate", sa.Numeric(18, 4)),
    ("active", sa.Boolean),
    ("basis", sa.Text),
    ("profile_id", sa.SmallInteger),
)


def upgrade() -> None:
    op.create_table(
        "capacity_line_log",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor", sa.Text),
        sa.Column("batch_id", sa.Text),
        sa.Column("op", sa.Text, nullable=False),
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("cost_center_id", sa.Integer, nullable=False),
        sa.Column("work_center_id", sa.Integer, nullable=False),
        sa.Column("activity_type_id", sa.Integer, nullable=False),
        *(sa.Column(f"old_{name}", kind) for name, kind in STATE),
        *(sa.Column(f"new_{name}", kind) for name, kind in STATE),
        schema=S,
    )
    s = migration_schema()
    names = [name for name, _ in STATE]
    columns = ", ".join(
        [
            "actor, batch_id, op, version_id, cost_center_id, work_center_id, activity_type_id",
            *(f"old_{n}" for n in names),
            *(f"new_{n}" for n in names),
        ]
    )

    def state(row: str) -> str:
        return ", ".join(f"{row}.{n}" for n in names)

    op.execute(f"""
        CREATE FUNCTION {s}.log_capacity_line() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            o {s}.capacity_line;  -- stays all NULL for an insert
            n {s}.capacity_line;  -- stays all NULL for a delete
            k {s}.capacity_line;
        BEGIN
            IF TG_OP <> 'INSERT' THEN o := OLD; k := OLD; END IF;
            IF TG_OP <> 'DELETE' THEN n := NEW; k := NEW; END IF;
            IF TG_OP = 'UPDATE' AND ({state("o")}) IS NOT DISTINCT FROM ({state("n")}) THEN
                RETURN NULL;
            END IF;
            INSERT INTO {s}.capacity_line_log ({columns}) VALUES (
                nullif(current_setting('app.actor', true), ''),
                nullif(current_setting('app.batch_id', true), ''),
                TG_OP, k.version_id, k.cost_center_id, k.work_center_id, k.activity_type_id,
                {state("o")}, {state("n")});
            RETURN NULL;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER log AFTER INSERT OR UPDATE OR DELETE ON {s}.capacity_line
        FOR EACH ROW EXECUTE FUNCTION {s}.log_capacity_line()
    """)


def downgrade() -> None:
    s = migration_schema()
    op.execute(f"DROP TRIGGER log ON {s}.capacity_line")
    op.execute(f"DROP FUNCTION {s}.log_capacity_line()")
    op.drop_table("capacity_line_log", schema=S)
