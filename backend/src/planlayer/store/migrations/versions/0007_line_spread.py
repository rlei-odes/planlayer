"""How a single line spreads its year total, chosen on the sheet (ADR 0007): row-level security
as on the fact table, and a lock trigger refusing changes where plan values are locked.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA


def _fk(target: str) -> sa.ForeignKey:
    return sa.ForeignKey(f"{S}.{target}")


def upgrade() -> None:
    op.create_table(
        "line_spread",
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("module", sa.Text, nullable=False),
        sa.Column("cost_center_id", sa.Integer, _fk("member.member_id"), nullable=False),
        sa.Column("account_id", sa.Integer, _fk("member.member_id"), nullable=False),
        sa.Column("basis", sa.Text, nullable=False),
        sa.Column("profile_id", sa.SmallInteger, _fk("seasonality_profile.profile_id")),
        sa.Column("changed_by", sa.Text),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("version_id", "module", "cost_center_id", "account_id"),
        sa.ForeignKeyConstraint(
            ["version_id", "module"], [f"{S}.round_module.version_id", f"{S}.round_module.module"]
        ),
        sa.CheckConstraint("basis IN ('even', 'reference', 'profile')", name="basis"),
        sa.CheckConstraint("(basis = 'profile') = (profile_id IS NOT NULL)", name="profile"),
        schema=S,
    )

    s = migration_schema()
    scope = (
        "cost_center_id = ANY (ARRAY(SELECT cost_center_id FROM {s}.cost_center_access "
        "WHERE user_id = nullif(current_setting('app.user_id', true), '')::int{write}))"
    )
    read = scope.format(s=s, write="")
    write = scope.format(s=s, write=" AND can_write")
    op.execute(f"ALTER TABLE {s}.line_spread ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {s}.line_spread FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY read ON {s}.line_spread FOR SELECT USING ({read})")
    op.execute(f"CREATE POLICY add ON {s}.line_spread FOR INSERT WITH CHECK ({write})")
    op.execute(
        f"CREATE POLICY change ON {s}.line_spread FOR UPDATE USING ({write}) WITH CHECK ({write})"
    )
    op.execute(f"CREATE POLICY remove ON {s}.line_spread FOR DELETE USING ({write})")

    # One row per request, so a row trigger; the same rules and error as plan values.
    op.execute(f"""
        CREATE FUNCTION {s}.lock_line_spread() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            t {s}.line_spread;
            problem text;
        BEGIN
            t := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            SELECT CASE
                       WHEN r.status <> 'open' THEN 'the planning round is ' || r.status
                       WHEN NOT coalesce(m.enabled, false)
                           THEN 'the cost center is not enabled in the round'
                       WHEN coalesce(w.status, 'not_started') NOT IN ('not_started', 'in_progress')
                           THEN 'the cost center is ' || w.status
                   END
            INTO problem
            FROM {s}.planning_round r
            LEFT JOIN {s}.round_member m
                ON m.version_id = r.version_id AND m.module = t.module
                AND m.member_id = t.cost_center_id
            LEFT JOIN {s}.workflow_status w
                ON w.version_id = r.version_id AND w.cost_center_id = t.cost_center_id
            WHERE r.version_id = t.version_id
            FOR SHARE OF r;
            IF problem IS NOT NULL THEN
                RAISE EXCEPTION 'plan values are locked: %', problem USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER lock AFTER INSERT OR UPDATE OR DELETE ON {s}.line_spread
        FOR EACH ROW EXECUTE FUNCTION {s}.lock_line_spread()
    """)


def downgrade() -> None:
    s = migration_schema()
    op.drop_table("line_spread", schema=S)
    op.execute(f"DROP FUNCTION {s}.lock_line_spread()")
