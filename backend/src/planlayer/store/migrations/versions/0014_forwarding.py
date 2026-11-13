"""Cost forwarding (ADR 0018): forwardings per round with their receivers, the accounts they
charge and credit, and the locks' forwarding rule: forwarding accounts are written by controllers
only (the system user is one), whatever the cost center's status, and writing them moves no
status.

Revision ID: 0014
Revises: 0013
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA
H_COST_CENTER, H_ACCOUNT = 1, 2
EDITABLE = "('not_started', 'in_progress')"


def _member(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Integer, sa.ForeignKey(f"{S}.member.member_id"), nullable=False)


def upgrade() -> None:
    s = migration_schema()
    op.create_table(
        "forwarding",
        sa.Column("forwarding_id", sa.Integer, primary_key=True),
        sa.Column(
            "version_id",
            sa.SmallInteger,
            sa.ForeignKey(f"{S}.planning_round.version_id"),
            nullable=False,
        ),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("method", sa.Text, nullable=False),
        _member("source_id"),
        # full and fixed: how the receivers' shares read.
        sa.Column("shares", sa.Text),
        sa.Column("amount", sa.Numeric(18, 2)),  # fixed: the year's amount
        sa.Column("rate", sa.Numeric(18, 4)),  # rate: per unit and year
        sa.Column("unit", sa.Text),  # rate: what a quantity counts, e.g. m³
        _member("charge_account_id"),
        _member("credit_account_id"),
        sa.Column("changed_by", sa.Text),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("version_id", "code"),
        sa.UniqueConstraint("version_id", "charge_account_id"),
        sa.UniqueConstraint("version_id", "credit_account_id"),
        sa.CheckConstraint("method IN ('full', 'fixed', 'rate')", name="method"),
        sa.CheckConstraint("shares IN ('percent', 'portion')", name="shares"),
        sa.CheckConstraint("charge_account_id <> credit_account_id", name="two_accounts"),
        sa.CheckConstraint(
            "(method = 'full' AND shares IS NOT NULL AND amount IS NULL AND rate IS NULL)"
            " OR (method = 'fixed' AND shares IS NOT NULL AND amount >= 0 AND rate IS NULL)"
            " OR (method = 'rate' AND shares IS NULL AND amount IS NULL AND rate >= 0)",
            name="method_values",
        ),
        schema=S,
    )
    op.create_table(
        "forwarding_receiver",
        sa.Column(
            "forwarding_id",
            sa.Integer,
            sa.ForeignKey(f"{S}.forwarding.forwarding_id", ondelete="CASCADE"),
            nullable=False,
        ),
        _member("cost_center_id"),
        # A share (percent or portion) or a quantity of the forwarding's unit.
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.PrimaryKeyConstraint("forwarding_id", "cost_center_id"),
        sa.CheckConstraint("quantity >= 0", name="quantity"),
        schema=S,
    )
    op.execute(f"""
        CREATE VIEW {s}.forwarding_account AS
        SELECT version_id, charge_account_id AS account_id, 'charge'::text AS side, forwarding_id
        FROM {s}.forwarding
        UNION ALL
        SELECT version_id, credit_account_id, 'credit', forwarding_id FROM {s}.forwarding
    """)
    # Forwardings are the controllers', and change only while the round is in setup or open.
    op.execute(f"""
        CREATE FUNCTION {s}.guard_forwarding() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            vid smallint;
            st text;
        BEGIN
            IF TG_TABLE_NAME = 'forwarding' THEN
                vid := CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END;
            ELSE
                SELECT f.version_id INTO vid FROM {s}.forwarding f WHERE f.forwarding_id =
                    CASE WHEN TG_OP = 'DELETE' THEN OLD.forwarding_id ELSE NEW.forwarding_id END;
                IF vid IS NULL THEN  -- deleted with its forwarding
                    RETURN NULL;
                END IF;
            END IF;
            IF NOT {s}.writes_as_controller() THEN
                RAISE EXCEPTION 'plan values are locked: only controllers change cost forwarding'
                    USING ERRCODE = 'PL001';
            END IF;
            SELECT status INTO st FROM {s}.planning_round WHERE version_id = vid FOR SHARE;
            IF st NOT IN ('setup', 'open') THEN
                RAISE EXCEPTION 'plan values are locked: the planning round is %', st
                    USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$
    """)
    op.execute(f"""
        CREATE FUNCTION {s}.writes_as_controller() RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (
                SELECT 1 FROM {s}.app_user u
                WHERE u.user_id = nullif(current_setting('app.user_id', true), '')::int
                  AND u.is_controller AND NOT u.disabled)
        $$
    """)
    for table in ("forwarding", "forwarding_receiver"):
        op.execute(f"""
            CREATE TRIGGER guard AFTER INSERT OR UPDATE OR DELETE ON {s}.{table}
            FOR EACH ROW EXECUTE FUNCTION {s}.guard_forwarding()
        """)
    _lock_fact_cost_center(s)


def downgrade() -> None:
    s = migration_schema()
    # The lock function as migration 0010 left it.
    path = Path(__file__).with_name("0010_round_scope.py")
    spec = importlib.util.spec_from_file_location("round_scope_0010", path)
    assert spec is not None and spec.loader is not None
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    previous._locks(s, prepared=True)
    for table in ("forwarding_receiver", "forwarding"):
        op.execute(f"DROP TRIGGER guard ON {s}.{table}")
    op.execute(f"DROP FUNCTION {s}.guard_forwarding()")
    op.execute(f"DROP FUNCTION {s}.writes_as_controller()")
    op.execute(f"DROP VIEW {s}.forwarding_account")
    op.drop_table("forwarding_receiver", schema=S)
    op.drop_table("forwarding", schema=S)


def _lock_fact_cost_center(s: str) -> None:
    """The lock of migration 0010, with forwarding accounts (``fa``) apart: written by
    controllers only, whatever the cost center's status, outside account sets and not-planned
    accounts, and moving no status."""
    closed = f"NOT (r.status = 'open' OR {s}.prepares(r.status))"
    collect = (
        "SELECT array_agg(version_id), array_agg(dim_cost_center), array_agg(dim_account) "
        "INTO v, c, a FROM (SELECT DISTINCT version_id, dim_cost_center, dim_account FROM {rows}"
        f" WHERE version_id NOT IN (SELECT version_id FROM {s}.version WHERE kind = 'actuals')) x"
    )
    touched = "unnest(v, c, a) AS t(version_id, dim_cost_center, dim_account)"
    both = "(SELECT * FROM new_rows UNION ALL SELECT * FROM old_rows) u"
    forwarded = f"""
        LEFT JOIN {s}.forwarding_account fa
            ON fa.version_id = t.version_id AND fa.account_id = t.dim_account"""
    nearest = f"""
        LEFT JOIN LATERAL (
            SELECT x.set_id FROM {s}.closure cl
            JOIN {s}.account_set_assignment x
                ON x.member_id = cl.ancestor_id AND x.version_id = t.version_id
                AND x.module = 'cost_center'
            WHERE cl.hier_id = {H_COST_CENTER} AND cl.descendant_id = t.dim_cost_center
            ORDER BY cl.depth LIMIT 1
        ) cs ON true"""
    outside = f"""(TG_OP <> 'DELETE' AND cs.set_id IS NOT NULL
               AND t.dim_account NOT IN (SELECT account_id FROM {s}.credit_account)
               AND NOT EXISTS (
                   SELECT 1 FROM {s}.account_set_account i
                   JOIN {s}.closure ca ON ca.hier_id = {H_ACCOUNT} AND ca.ancestor_id = i.account_id
                   WHERE i.set_id = cs.set_id AND ca.descendant_id = t.dim_account))"""
    planned = f"(w.status NOT IN {EDITABLE} OR h.account_id IS NOT NULL OR {outside})"
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {s}.lock_fact_cost_center() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            v smallint[];
            c int[];
            a int[];
            problem text;
            controller boolean := {s}.writes_as_controller();
        BEGIN
            IF TG_OP = 'INSERT' THEN
                {collect.format(rows="new_rows")};
            ELSIF TG_OP = 'UPDATE' THEN
                {collect.format(rows=both)};
            ELSE
                {collect.format(rows="old_rows")};
            END IF;
            IF v IS NULL THEN
                RETURN NULL;
            END IF;

            PERFORM 1 FROM {s}.planning_round WHERE version_id = ANY (v) FOR SHARE;
            INSERT INTO {s}.workflow_status (version_id, cost_center_id, status)
                SELECT DISTINCT t.version_id, t.dim_cost_center, 'not_started' FROM {touched}
                WHERE t.version_id IN (SELECT version_id FROM {s}.planning_round)
                ON CONFLICT DO NOTHING;
            PERFORM 1 FROM {s}.workflow_status w
                WHERE (w.version_id, w.cost_center_id) IN
                      (SELECT t.version_id, t.dim_cost_center FROM {touched})
                FOR UPDATE;

            SELECT CASE
                       WHEN r.version_id IS NULL THEN 'the version has no planning round'
                       WHEN {closed} THEN 'the planning round is ' || r.status
                       WHEN NOT coalesce(m.enabled, false)
                           THEN 'the cost center is not enabled in the round'
                       WHEN fa.account_id IS NOT NULL
                           THEN 'the account is written by cost forwarding'
                       WHEN w.status NOT IN {EDITABLE}
                           THEN 'the cost center is ' || w.status
                       WHEN h.account_id IS NOT NULL
                           THEN 'the account is not planned in the round'
                       ELSE 'the account is not in the cost center''s account set'
                   END
            INTO problem
            FROM {touched}
            LEFT JOIN {s}.planning_round r ON r.version_id = t.version_id
            LEFT JOIN {s}.round_member m
                ON m.version_id = t.version_id AND m.module = 'cost_center'
                AND m.member_id = t.dim_cost_center
            LEFT JOIN {s}.workflow_status w
                ON w.version_id = t.version_id AND w.cost_center_id = t.dim_cost_center
            LEFT JOIN {s}.not_planned_account h
                ON h.version_id = t.version_id AND h.module = 'cost_center'
                AND h.account_id = t.dim_account
            {forwarded}
            {nearest}
            WHERE r.version_id IS NULL OR {closed} OR NOT coalesce(m.enabled, false)
               OR (fa.account_id IS NOT NULL AND NOT controller)
               OR (fa.account_id IS NULL AND {planned})
            LIMIT 1;
            IF problem IS NOT NULL THEN
                RAISE EXCEPTION 'plan values are locked: %', problem USING ERRCODE = 'PL001';
            END IF;

            IF TG_OP <> 'DELETE' THEN
                WITH started AS (
                    UPDATE {s}.workflow_status w
                    SET status = 'in_progress', changed_at = now(),
                        changed_by = nullif(current_setting('app.actor', true), '')
                    WHERE w.status = 'not_started'
                      AND (w.version_id, w.cost_center_id) IN
                          (SELECT t.version_id, t.dim_cost_center FROM {touched} {forwarded}
                           WHERE fa.account_id IS NULL)
                      AND w.version_id IN
                          (SELECT version_id FROM {s}.planning_round WHERE status = 'open')
                    RETURNING w.version_id, w.cost_center_id
                )
                INSERT INTO {s}.workflow_event
                    (version_id, cost_center_id, actor, from_status, to_status)
                SELECT version_id, cost_center_id,
                       nullif(current_setting('app.actor', true), ''),
                       'not_started', 'in_progress'
                FROM started;
            END IF;
            RETURN NULL;
        END $$
    """)
