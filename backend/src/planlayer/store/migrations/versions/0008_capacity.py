"""Productive cost centers and capacity planning (ADR 0013): work centers and activity types as
dimensions, the source's rates, capacity lines per round, planned and actual hours with
row-level security, locks and audit, capacity planners, and credit accounts writable by them.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA
COST_CENTER, ACCOUNT, WORK_CENTER, ACTIVITY_TYPE = 2, 3, 4, 5
RATE = sa.Numeric(18, 4)
OLD_DATASETS = "'entities', 'cost_centers', 'accounts', 'actuals'"
DATASETS = (
    f"{OLD_DATASETS}, 'activity_types', 'work_centers', 'work_center_rates', 'capacity_actuals'"
)


def _fk(target: str) -> sa.ForeignKey:
    return sa.ForeignKey(f"{S}.{target}")


def _member(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Integer, _fk("member.member_id"), nullable=False)


def _write_policies(table: str, write: str) -> None:
    op.execute(f"CREATE POLICY add ON {table} FOR INSERT WITH CHECK ({write})")
    op.execute(f"CREATE POLICY change ON {table} FOR UPDATE USING ({write}) WITH CHECK ({write})")
    op.execute(f"CREATE POLICY remove ON {table} FOR DELETE USING ({write})")


def upgrade() -> None:
    s = migration_schema()
    dimension = sa.Table(
        "dimension",
        sa.MetaData(),
        sa.Column("dim_id", sa.SmallInteger),
        sa.Column("code", sa.Text),
        sa.Column("name", sa.Text),
        schema=S,
    )
    op.bulk_insert(
        dimension,
        [
            {"dim_id": WORK_CENTER, "code": "work_center", "name": "Work center"},
            {"dim_id": ACTIVITY_TYPE, "code": "activity_type", "name": "Activity type"},
        ],
    )
    # A work center's cost center; an activity type's credited accounts (codes, like entity_code).
    op.execute(
        f"ALTER TABLE {s}.member ADD COLUMN cost_center_code text, "
        "ADD COLUMN account_code text, ADD COLUMN variable_account_code text"
    )
    op.execute(
        f"ALTER TABLE {s}.round_module DROP CONSTRAINT ck_round_module_module, "
        "ADD CONSTRAINT ck_round_module_module CHECK (module IN ('cost_center', 'capacity'))"
    )
    op.execute(
        f"ALTER TABLE {s}.dataset_mapping DROP CONSTRAINT ck_dataset_mapping_dataset, "
        f"ADD CONSTRAINT ck_dataset_mapping_dataset CHECK (dataset IN ({DATASETS}))"
    )

    op.create_table(
        "work_center_rate",
        _member("work_center_id"),
        _member("activity_type_id"),
        sa.Column("rate", RATE, nullable=False),
        sa.PrimaryKeyConstraint("work_center_id", "activity_type_id"),
        schema=S,
    )
    op.create_table(
        "capacity_line",
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("module", sa.Text, nullable=False, server_default="capacity"),
        _member("cost_center_id"),
        _member("work_center_id"),
        _member("activity_type_id"),
        sa.Column("rate", RATE, nullable=False),
        sa.Column("variable_rate", RATE),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("basis", sa.Text),  # NULL: like prior-year actual hours
        sa.Column("profile_id", sa.SmallInteger, _fk("seasonality_profile.profile_id")),
        sa.Column("changed_by", sa.Text),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("version_id", "work_center_id", "activity_type_id"),
        sa.ForeignKeyConstraint(
            ["version_id", "module"], [f"{S}.round_module.version_id", f"{S}.round_module.module"]
        ),
        sa.CheckConstraint("module = 'capacity'", name="module"),
        sa.CheckConstraint("basis IN ('even', 'reference', 'profile')", name="basis"),
        sa.CheckConstraint("(basis = 'profile') = (profile_id IS NOT NULL)", name="profile"),
        sa.CheckConstraint("rate >= 0 AND coalesce(variable_rate, 0) >= 0", name="rate"),
        schema=S,
    )
    op.create_table(
        "fact_capacity",
        sa.Column("version_id", sa.SmallInteger, _fk("version.version_id"), nullable=False),
        sa.Column("layer_id", sa.SmallInteger, nullable=False),
        sa.Column("measure_id", sa.SmallInteger, nullable=False),
        sa.Column("period", sa.Date, nullable=False),
        _member("dim_entity"),
        _member("dim_cost_center"),
        _member("dim_work_center"),
        _member("dim_activity_type"),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("row_version", sa.BigInteger, nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint(
            "version_id", "layer_id", "measure_id", "period", "dim_entity", "dim_cost_center",
            "dim_work_center", "dim_activity_type",
        ),
        schema=S,
    )  # fmt: skip
    op.create_table(
        "capacity_change_log",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor", sa.Text),
        sa.Column("batch_id", sa.Text),
        sa.Column("op", sa.Text, nullable=False),
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("period", sa.Date, nullable=False),
        sa.Column("dim_cost_center", sa.Integer, nullable=False),
        sa.Column("dim_work_center", sa.Integer, nullable=False),
        sa.Column("dim_activity_type", sa.Integer, nullable=False),
        sa.Column("old_amount", sa.Numeric(18, 2)),
        sa.Column("new_amount", sa.Numeric(18, 2)),
        schema=S,
    )
    op.create_table(
        "capacity_grant",
        _member("member_id"),
        sa.Column("user_id", sa.Integer, _fk("app_user.user_id"), nullable=False),
        sa.Column("granted_by", sa.Text),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("member_id", "user_id"),
        schema=S,
    )

    # Capacity planners read the cost center (both sheets); they write only its capacity.
    op.execute(f"""
        CREATE OR REPLACE VIEW {s}.cost_center_access AS
        SELECT u.user_id, m.member_id AS cost_center_id, u.is_controller AS can_write
        FROM {s}.app_user u CROSS JOIN {s}.member m
        WHERE (u.is_controller OR u.is_viewer) AND NOT u.disabled
          AND m.dim_id = {COST_CENTER} AND NOT m.is_group
        UNION ALL
        SELECT o.user_id, o.cost_center_id, true
        FROM {s}.cost_center_owner o JOIN {s}.app_user u USING (user_id)
        WHERE NOT u.disabled
        UNION ALL
        SELECT g.user_id, c.descendant_id, true
        FROM {s}.planner_grant g JOIN {s}.app_user u USING (user_id)
        JOIN {s}.closure c ON c.hier_id = 1 AND c.ancestor_id = g.member_id
        WHERE NOT u.disabled
        UNION ALL
        SELECT a.user_id, c.descendant_id, false
        FROM {s}.approver_assignment a JOIN {s}.app_user u USING (user_id)
        JOIN {s}.closure c ON c.hier_id = 1 AND c.ancestor_id = a.member_id
        WHERE NOT u.disabled
        UNION ALL
        SELECT g.user_id, c.descendant_id, false
        FROM {s}.capacity_grant g JOIN {s}.app_user u USING (user_id)
        JOIN {s}.closure c ON c.hier_id = 1 AND c.ancestor_id = g.member_id
        WHERE NOT u.disabled
    """)
    op.execute(f"""
        CREATE VIEW {s}.capacity_access AS
        SELECT user_id, cost_center_id, can_write FROM {s}.cost_center_access
        UNION ALL
        SELECT g.user_id, c.descendant_id, true
        FROM {s}.capacity_grant g JOIN {s}.app_user u USING (user_id)
        JOIN {s}.closure c ON c.hier_id = 1 AND c.ancestor_id = g.member_id
        WHERE NOT u.disabled
    """)
    # Accounts credited by an activity type.
    op.execute(f"""
        CREATE VIEW {s}.credit_account AS
        SELECT DISTINCT a.member_id AS account_id
        FROM {s}.member t JOIN {s}.member a
            ON a.dim_id = {ACCOUNT} AND a.code IN (t.account_code, t.variable_account_code)
        WHERE t.dim_id = {ACTIVITY_TYPE}
    """)

    def scope(column: str, view: str, write: bool) -> str:
        return (
            f"{column} = ANY (ARRAY(SELECT cost_center_id FROM {s}.{view} WHERE user_id = "
            f"nullif(current_setting('app.user_id', true), '')::int"
            f"{' AND can_write' if write else ''}))"
        )

    for table, column in (
        ("fact_capacity", "dim_cost_center"),
        ("capacity_line", "cost_center_id"),
    ):
        read, write = (
            scope(column, "capacity_access", False),
            scope(column, "capacity_access", True),
        )
        op.execute(f"ALTER TABLE {s}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {s}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY read ON {s}.{table} FOR SELECT USING ({read})")
        _write_policies(f"{s}.{table}", write)

    # Plan values: as before, plus credit accounts for whoever writes the cost center's capacity.
    credits = (
        f"(dim_account = ANY (ARRAY(SELECT account_id FROM {s}.credit_account)) AND "
        f"{scope('dim_cost_center', 'capacity_access', True)})"
    )
    write = f"({scope('dim_cost_center', 'cost_center_access', True)} OR {credits})"
    for policy in ("add", "change", "remove"):
        op.execute(f"DROP POLICY {policy} ON {s}.fact_cost_center")
    _write_policies(f"{s}.fact_cost_center", write)

    # Locks: planned hours and capacity lines follow the cost center's plan values (ADR 0008),
    # and need the cost center to be productive in the round. The first saved hours move the
    # cost center from not_started to in_progress, as plan values do.
    problem = f"""
        SELECT CASE
                   WHEN r.version_id IS NULL THEN 'the version has no planning round'
                   WHEN r.status <> 'open' THEN 'the planning round is ' || r.status
                   WHEN NOT coalesce(m.enabled, false)
                       THEN 'the cost center is not productive in the round'
                   WHEN coalesce(w.status, 'not_started') NOT IN ('not_started', 'in_progress')
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
        WHERE r.version_id IS NULL OR r.status <> 'open' OR NOT coalesce(m.enabled, false)
           OR coalesce(w.status, 'not_started') NOT IN ('not_started', 'in_progress')
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
    both = "(SELECT * FROM new_rows UNION ALL SELECT * FROM old_rows) u"
    op.execute(f"""
        CREATE FUNCTION {s}.lock_fact_capacity() RETURNS trigger LANGUAGE plpgsql AS $$
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
        CREATE FUNCTION {s}.lock_capacity_line() RETURNS trigger LANGUAGE plpgsql AS $$
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
    op.execute(f"""
        CREATE TRIGGER lock AFTER INSERT OR UPDATE OR DELETE ON {s}.capacity_line
        FOR EACH ROW EXECUTE FUNCTION {s}.lock_capacity_line()
    """)

    # Audit (ADR 0005): one change-log row per changed planned hour; actuals are not logged.
    columns = (
        "actor, batch_id, op, version_id, period, dim_cost_center, dim_work_center, "
        "dim_activity_type, old_amount, new_amount"
    )
    context = (
        "nullif(current_setting('app.actor', true), ''), "
        "nullif(current_setting('app.batch_id', true), '')"
    )
    keys = "version_id, period, dim_cost_center, dim_work_center, dim_activity_type"
    plan_only = f"version_id IN (SELECT version_id FROM {s}.version WHERE kind <> 'actuals')"
    op.execute(f"""
        CREATE FUNCTION {s}.audit_fact_capacity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO {s}.capacity_change_log ({columns})
                SELECT {context}, 'INSERT', {keys}, NULL, amount
                FROM new_rows WHERE {plan_only};
            ELSIF TG_OP = 'UPDATE' THEN
                INSERT INTO {s}.capacity_change_log ({columns})
                SELECT {context}, 'UPDATE', n.version_id, n.period, n.dim_cost_center,
                       n.dim_work_center, n.dim_activity_type, o.amount, n.amount
                FROM new_rows n JOIN old_rows o USING (version_id, layer_id, measure_id, period,
                    dim_entity, dim_cost_center, dim_work_center, dim_activity_type)
                WHERE n.{plan_only} AND n.amount IS DISTINCT FROM o.amount;
            ELSE
                INSERT INTO {s}.capacity_change_log ({columns})
                SELECT {context}, 'DELETE', {keys}, amount, NULL
                FROM old_rows WHERE {plan_only};
            END IF;
            RETURN NULL;
        END $$
    """)
    for event, refs in (
        ("INSERT", "NEW TABLE AS new_rows"),
        ("UPDATE", "NEW TABLE AS new_rows OLD TABLE AS old_rows"),
        ("DELETE", "OLD TABLE AS old_rows"),
    ):
        for kind in ("lock", "audit"):
            op.execute(f"""
                CREATE TRIGGER {kind}_{event.lower()} AFTER {event} ON {s}.fact_capacity
                REFERENCING {refs} FOR EACH STATEMENT
                EXECUTE FUNCTION {s}.{kind}_fact_capacity()
            """)


def downgrade() -> None:
    s = migration_schema()
    for policy in ("add", "change", "remove"):
        op.execute(f"DROP POLICY {policy} ON {s}.fact_cost_center")
    write = (
        f"dim_cost_center = ANY (ARRAY(SELECT cost_center_id FROM {s}.cost_center_access WHERE "
        "user_id = nullif(current_setting('app.user_id', true), '')::int AND can_write))"
    )
    _write_policies(f"{s}.fact_cost_center", write)
    for table in (
        "fact_capacity",
        "capacity_change_log",
        "capacity_line",
        "work_center_rate",
    ):
        op.drop_table(table, schema=S)
    op.execute(f"DROP VIEW {s}.credit_account")
    op.execute(f"DROP VIEW {s}.capacity_access")
    for fn in ("lock_fact_capacity", "audit_fact_capacity", "lock_capacity_line"):
        op.execute(f"DROP FUNCTION {s}.{fn}()")
    # cost_center_access back to its form without capacity planners (0004), then the grants.
    op.execute(f"""
        CREATE OR REPLACE VIEW {s}.cost_center_access AS
        SELECT u.user_id, m.member_id AS cost_center_id, u.is_controller AS can_write
        FROM {s}.app_user u CROSS JOIN {s}.member m
        WHERE (u.is_controller OR u.is_viewer) AND NOT u.disabled
          AND m.dim_id = {COST_CENTER} AND NOT m.is_group
        UNION ALL
        SELECT o.user_id, o.cost_center_id, true
        FROM {s}.cost_center_owner o JOIN {s}.app_user u USING (user_id)
        WHERE NOT u.disabled
        UNION ALL
        SELECT g.user_id, c.descendant_id, true
        FROM {s}.planner_grant g JOIN {s}.app_user u USING (user_id)
        JOIN {s}.closure c ON c.hier_id = 1 AND c.ancestor_id = g.member_id
        WHERE NOT u.disabled
        UNION ALL
        SELECT a.user_id, c.descendant_id, false
        FROM {s}.approver_assignment a JOIN {s}.app_user u USING (user_id)
        JOIN {s}.closure c ON c.hier_id = 1 AND c.ancestor_id = a.member_id
        WHERE NOT u.disabled
    """)
    op.drop_table("capacity_grant", schema=S)
    op.execute(
        f"ALTER TABLE {s}.round_module DROP CONSTRAINT ck_round_module_module, "
        "ADD CONSTRAINT ck_round_module_module CHECK (module IN ('cost_center'))"
    )
    op.execute(f"DELETE FROM {s}.dataset_mapping WHERE dataset NOT IN ({OLD_DATASETS})")
    op.execute(
        f"ALTER TABLE {s}.dataset_mapping DROP CONSTRAINT ck_dataset_mapping_dataset, "
        f"ADD CONSTRAINT ck_dataset_mapping_dataset CHECK (dataset IN ({OLD_DATASETS}))"
    )
    op.execute(
        f"ALTER TABLE {s}.member DROP COLUMN cost_center_code, DROP COLUMN account_code, "
        "DROP COLUMN variable_account_code"
    )
    op.execute(f"DELETE FROM {s}.dimension WHERE dim_id IN ({WORK_CENTER}, {ACTIVITY_TYPE})")
