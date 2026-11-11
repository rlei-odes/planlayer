"""Preparing a round (ADR 0014): members no longer delivered by the source, accounts not
planned (formerly hidden), account sets assigned to cost center nodes, and the locks' setup rule:
controllers write into a round in setup without moving workflow status. Plan values outside a
cost center's account set are refused.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA
H_COST_CENTER, H_ACCOUNT = 1, 2
EDITABLE = "('not_started', 'in_progress')"


def _module_fk() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["version_id", "module"],
        [f"{S}.round_module.version_id", f"{S}.round_module.module"],
    )


def upgrade() -> None:
    s = migration_schema()
    op.execute(f"ALTER TABLE {s}.hidden_account RENAME TO not_planned_account")
    # Since when the source no longer delivers a synced member (master data only grows).
    # Alembic's column operations aren't schema-translated.
    op.execute(f"ALTER TABLE {s}.member ADD COLUMN missing_since timestamptz")
    op.create_table(
        "account_set",
        sa.Column("set_id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("module", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.UniqueConstraint("version_id", "module", "name"),
        _module_fk(),
        schema=S,
    )
    op.create_table(
        "account_set_account",
        sa.Column(
            "set_id",
            sa.Integer,
            sa.ForeignKey(f"{S}.account_set.set_id", ondelete="CASCADE"),
            nullable=False,
        ),
        # An account or an account group: a group stands for every account below it.
        sa.Column("account_id", sa.Integer, sa.ForeignKey(f"{S}.member.member_id"), nullable=False),
        sa.PrimaryKeyConstraint("set_id", "account_id"),
        schema=S,
    )
    op.create_table(
        "account_set_assignment",
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("module", sa.Text, nullable=False),
        # A cost center node or a single cost center; the nearest assignment above wins.
        sa.Column("member_id", sa.Integer, sa.ForeignKey(f"{S}.member.member_id"), nullable=False),
        sa.Column(
            "set_id",
            sa.Integer,
            sa.ForeignKey(f"{S}.account_set.set_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("version_id", "module", "member_id"),
        _module_fk(),
        schema=S,
    )
    op.execute(f"""
        CREATE FUNCTION {s}.prepares(status text) RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT status = 'setup' AND EXISTS (
                SELECT 1 FROM {s}.app_user u
                WHERE u.user_id = nullif(current_setting('app.user_id', true), '')::int
                  AND u.is_controller AND NOT u.disabled)
        $$
    """)
    _locks(s, prepared=True)


def downgrade() -> None:
    s = migration_schema()
    _locks(s, prepared=False)
    op.execute(f"DROP FUNCTION {s}.prepares(text)")
    op.drop_table("account_set_assignment", schema=S)
    op.drop_table("account_set_account", schema=S)
    op.drop_table("account_set", schema=S)
    op.execute(f"ALTER TABLE {s}.member DROP COLUMN missing_since")
    op.execute(f"ALTER TABLE {s}.not_planned_account RENAME TO hidden_account")


def _locks(s: str, prepared: bool) -> None:
    """The lock functions, with the setup rule and account sets (``prepared``) or as before."""
    excluded = "not_planned_account" if prepared else "hidden_account"
    closed = (
        f"NOT (r.status = 'open' OR {s}.prepares(r.status))" if prepared else "r.status <> 'open'"
    )
    # Workflow status moves only in open rounds; before, every round written was open.
    opened = (
        f"AND w.version_id IN (SELECT version_id FROM {s}.planning_round WHERE status = 'open')"
    )
    opened = opened if prepared else ""

    # --- Plan values --------------------------------------------------------------------------
    collect = (
        "SELECT array_agg(version_id), array_agg(dim_cost_center), array_agg(dim_account) "
        "INTO v, c, a FROM (SELECT DISTINCT version_id, dim_cost_center, dim_account FROM {rows}"
        f" WHERE version_id NOT IN (SELECT version_id FROM {s}.version WHERE kind = 'actuals')) x"
    )
    touched = "unnest(v, c, a) AS t(version_id, dim_cost_center, dim_account)"
    both = "(SELECT * FROM new_rows UNION ALL SELECT * FROM old_rows) u"
    # The cost center's account set: the nearest assignment on it or a node above it.
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
    excluded_text = "not planned" if prepared else "hidden"
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {s}.lock_fact_cost_center() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            v smallint[];
            c int[];
            a int[];
            problem text;
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
                       WHEN w.status NOT IN {EDITABLE}
                           THEN 'the cost center is ' || w.status
                       WHEN h.account_id IS NOT NULL
                           THEN 'the account is {excluded_text} in the round'
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
            LEFT JOIN {s}.{excluded} h
                ON h.version_id = t.version_id AND h.module = 'cost_center'
                AND h.account_id = t.dim_account
            {nearest if prepared else ""}
            WHERE r.version_id IS NULL OR {closed} OR NOT coalesce(m.enabled, false)
               OR w.status NOT IN {EDITABLE} OR h.account_id IS NOT NULL
               {"OR " + outside if prepared else ""}
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
                          (SELECT t.version_id, t.dim_cost_center FROM {touched})
                      {opened}
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

    # --- Line spreads -------------------------------------------------------------------------
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {s}.lock_line_spread() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            t {s}.line_spread;
            problem text;
        BEGIN
            t := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            SELECT CASE
                       WHEN {closed} THEN 'the planning round is ' || r.status
                       WHEN NOT coalesce(m.enabled, false)
                           THEN 'the cost center is not enabled in the round'
                       WHEN coalesce(w.status, 'not_started') NOT IN {EDITABLE}
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

    # --- Capacity: planned hours and capacity lines -------------------------------------------
    problem = f"""
        SELECT CASE
                   WHEN r.version_id IS NULL THEN 'the version has no planning round'
                   WHEN {closed} THEN 'the planning round is ' || r.status
                   WHEN NOT coalesce(m.enabled, false)
                       THEN 'the cost center is not productive in the round'
                   WHEN coalesce(w.status, 'not_started') NOT IN {EDITABLE}
                       THEN 'the cost center is ' || w.status
               END
        INTO problem
        FROM {{touched}}
        LEFT JOIN {s}.planning_round r ON r.version_id = t.version_id
        LEFT JOIN {s}.round_member m
            ON m.version_id = t.version_id AND m.module = 'capacity'
            AND m.member_id = t.cost_center_id
        LEFT JOIN {s}.workflow_status w
            ON w.version_id = t.version_id AND w.cost_center_id = t.cost_center_id
        WHERE r.version_id IS NULL OR {closed} OR NOT coalesce(m.enabled, false)
           OR coalesce(w.status, 'not_started') NOT IN {EDITABLE}
        LIMIT 1;
        IF problem IS NOT NULL THEN
            RAISE EXCEPTION 'plan values are locked: %', problem USING ERRCODE = 'PL001';
        END IF;
    """
    collect = (
        "SELECT array_agg(version_id), array_agg(dim_cost_center) INTO v, c FROM "
        "(SELECT DISTINCT version_id, dim_cost_center FROM {rows} WHERE version_id NOT IN "
        f"(SELECT version_id FROM {s}.version WHERE kind = 'actuals')) x"
    )
    touched = "unnest(v, c) AS t(version_id, cost_center_id)"
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {s}.lock_fact_capacity() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            v smallint[];
            c int[];
            problem text;
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
                SELECT DISTINCT t.version_id, t.cost_center_id, 'not_started' FROM {touched}
                WHERE t.version_id IN (SELECT version_id FROM {s}.planning_round)
                ON CONFLICT DO NOTHING;
            PERFORM 1 FROM {s}.workflow_status w
                WHERE (w.version_id, w.cost_center_id) IN
                      (SELECT t.version_id, t.cost_center_id FROM {touched})
                FOR UPDATE;
            {problem.format(touched=touched)}
            IF TG_OP <> 'DELETE' THEN
                WITH started AS (
                    UPDATE {s}.workflow_status w
                    SET status = 'in_progress', changed_at = now(),
                        changed_by = nullif(current_setting('app.actor', true), '')
                    WHERE w.status = 'not_started'
                      AND (w.version_id, w.cost_center_id) IN
                          (SELECT t.version_id, t.cost_center_id FROM {touched})
                      {opened}
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
    line = "(SELECT l.version_id, l.cost_center_id) AS t"
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {s}.lock_capacity_line() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            l {s}.capacity_line;
            problem text;
        BEGIN
            l := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            PERFORM 1 FROM {s}.planning_round WHERE version_id = l.version_id FOR SHARE;
            {problem.format(touched=line)}
            RETURN NULL;
        END $$
    """)
