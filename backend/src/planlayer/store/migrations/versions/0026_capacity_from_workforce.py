"""Capacity from the workforce (ADR 0027): a productive cost center's hours typed per line, from
its positions, or both. ``round_module.fte_hours`` holds the round's hours per full-time
position; ``capacity_unit`` where a cost center's hours come from and the line and productive
share of its positions; ``capacity_position`` a position's own, or its typed hours;
``capacity_line.staffed`` marks the lines whose hours the positions give.

Revision ID: 0026
Revises: 0025
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA
EDITABLE = "('not_started', 'in_progress')"
TABLES = ("capacity_unit", "capacity_position")


def _line_fk(prefix: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["version_id", f"{prefix}work_center_id", f"{prefix}activity_type_id"],
        [
            f"{S}.capacity_line.version_id",
            f"{S}.capacity_line.work_center_id",
            f"{S}.capacity_line.activity_type_id",
        ],
        ondelete=f"SET NULL ({prefix}work_center_id, {prefix}activity_type_id)",
    )


def _module_fk() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["version_id", "module"], [f"{S}.round_module.version_id", f"{S}.round_module.module"]
    )


def _changed() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("changed_by", sa.Text),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    s = migration_schema()
    # Alembic's column operations aren't schema-translated.
    op.execute(f"ALTER TABLE {s}.round_module ADD COLUMN fte_hours numeric(7, 2)")
    op.execute(
        f"ALTER TABLE {s}.round_module ADD CONSTRAINT ck_round_module_fte_hours "
        "CHECK (fte_hours > 0 AND (fte_hours IS NULL OR module = 'capacity'))"
    )
    op.execute(f"ALTER TABLE {s}.capacity_line ADD COLUMN staffed boolean NOT NULL DEFAULT false")
    member = f"{S}.member.member_id"
    op.create_table(
        "capacity_unit",
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("module", sa.Text, nullable=False, server_default="capacity"),
        sa.Column("cost_center_id", sa.Integer, sa.ForeignKey(member), nullable=False),
        sa.Column("typed_hours", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("staff_hours", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("default_work_center_id", sa.Integer),
        sa.Column("default_activity_type_id", sa.Integer),
        sa.Column("default_percent", sa.Numeric(5, 2)),
        *_changed(),
        sa.PrimaryKeyConstraint("version_id", "cost_center_id"),
        _module_fk(),
        _line_fk("default_"),
        sa.CheckConstraint("module = 'capacity'", name="module"),
        sa.CheckConstraint("typed_hours OR staff_hours", name="hours"),
        sa.CheckConstraint("default_percent BETWEEN 0 AND 100", name="default_percent"),
        schema=S,
    )
    op.create_table(
        "capacity_position",
        sa.Column(
            "position_id",
            sa.Integer,
            sa.ForeignKey(f"{S}.workforce_position.position_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("module", sa.Text, nullable=False, server_default="capacity"),
        sa.Column("cost_center_id", sa.Integer, sa.ForeignKey(member), nullable=False),
        sa.Column("productive_percent", sa.Numeric(5, 2)),
        sa.Column("hours", sa.Numeric(10, 2)),  # a year's hours, without a contract %
        sa.Column("work_center_id", sa.Integer),
        sa.Column("activity_type_id", sa.Integer),
        *_changed(),
        _module_fk(),
        _line_fk(""),
        sa.CheckConstraint("module = 'capacity'", name="module"),
        sa.CheckConstraint("productive_percent BETWEEN 0 AND 100", name="productive_percent"),
        sa.CheckConstraint("hours >= 0", name="hours"),
        schema=S,
    )
    op.create_table(
        "capacity_staff_log",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor", sa.Text),
        sa.Column("batch_id", sa.Text),
        sa.Column("op", sa.Text, nullable=False),
        sa.Column("target", sa.Text, nullable=False),
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("cost_center_id", sa.Integer, nullable=False),
        sa.Column("old", sa.dialects.postgresql.JSONB),
        sa.Column("new", sa.dialects.postgresql.JSONB),
        schema=S,
    )

    # Row-level security: as the capacity lines, through capacity_access.
    def scope(write: bool) -> str:
        return (
            f"cost_center_id = ANY (ARRAY(SELECT cost_center_id FROM {s}.capacity_access WHERE "
            "user_id = nullif(current_setting('app.user_id', true), '')::int"
            f"{' AND can_write' if write else ''}))"
        )

    for table in TABLES:
        t = f"{s}.{table}"
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY read ON {t} FOR SELECT USING ({scope(False)})")
        op.execute(f"CREATE POLICY add ON {t} FOR INSERT WITH CHECK ({scope(True)})")
        op.execute(
            f"CREATE POLICY change ON {t} FOR UPDATE USING ({scope(True)}) "
            f"WITH CHECK ({scope(True)})"
        )
        op.execute(f"CREATE POLICY remove ON {t} FOR DELETE USING ({scope(True)})")

    # Locks: as the capacity lines (ADR 0013, 0014): the round open, or in setup for
    # controllers; the cost center productive; its status editable.
    closed = f"NOT (r.status = 'open' OR {s}.prepares(r.status))"
    op.execute(f"""
        CREATE FUNCTION {s}.lock_capacity_staff() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            l record;
            problem text;
        BEGIN
            IF TG_OP = 'DELETE' THEN l := OLD; ELSE l := NEW; END IF;
            SELECT CASE
                       WHEN {closed} THEN 'the planning round is ' || r.status
                       WHEN NOT coalesce(m.enabled, false)
                           THEN 'the cost center is not productive in the round'
                       WHEN coalesce(w.status, 'not_started') NOT IN {EDITABLE}
                           THEN 'the cost center is ' || w.status
                   END
            INTO problem
            FROM {s}.planning_round r
            LEFT JOIN {s}.round_member m
                ON m.version_id = r.version_id AND m.module = 'capacity'
                AND m.member_id = l.cost_center_id
            LEFT JOIN {s}.workflow_status w
                ON w.version_id = r.version_id AND w.cost_center_id = l.cost_center_id
            WHERE r.version_id = l.version_id
            FOR SHARE OF r;
            IF problem IS NOT NULL THEN
                RAISE EXCEPTION 'plan values are locked: %', problem USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$
    """)
    # Audit: every change as old and new row, with the request's actor and change batch.
    op.execute(f"""
        CREATE FUNCTION {s}.log_capacity_staff() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            l record;
        BEGIN
            IF TG_OP = 'DELETE' THEN l := OLD; ELSE l := NEW; END IF;
            IF TG_OP = 'UPDATE' AND to_jsonb(OLD) - 'changed_at' - 'changed_by'
                                  = to_jsonb(NEW) - 'changed_at' - 'changed_by' THEN
                RETURN NULL;
            END IF;
            INSERT INTO {s}.capacity_staff_log
                (actor, batch_id, op, target, version_id, cost_center_id, old, new)
            VALUES (nullif(current_setting('app.actor', true), ''),
                    nullif(current_setting('app.batch_id', true), ''),
                    TG_OP, TG_TABLE_NAME, l.version_id, l.cost_center_id,
                    CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
                    CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END);
            RETURN NULL;
        END $$
    """)
    for table in TABLES:
        for kind in ("lock", "log"):
            op.execute(f"""
                CREATE TRIGGER {kind} AFTER INSERT OR UPDATE OR DELETE ON {s}.{table}
                FOR EACH ROW EXECUTE FUNCTION {s}.{kind}_capacity_staff()
            """)


def downgrade() -> None:
    s = migration_schema()
    for table in ("capacity_staff_log", "capacity_position", "capacity_unit"):
        op.drop_table(table, schema=S)
    for fn in ("lock_capacity_staff", "log_capacity_staff"):
        op.execute(f"DROP FUNCTION {s}.{fn}()")
    op.execute(f"ALTER TABLE {s}.capacity_line DROP COLUMN staffed")
    op.execute(f"ALTER TABLE {s}.round_module DROP CONSTRAINT ck_round_module_fte_hours")
    op.execute(f"ALTER TABLE {s}.round_module DROP COLUMN fte_hours")
