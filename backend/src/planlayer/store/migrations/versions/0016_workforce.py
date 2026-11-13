"""Workforce planning (ADR 0020): staff types and categories as dimensions, the round's workforce
mode, FTE and headcount per line and month with row-level security, locks and audit, lines added
to sheets, positions with their log, and HR's staff list.

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA
STAFF_TYPE, STAFF_CATEGORY = 6, 7
EDITABLE = "('not_started', 'in_progress')"
OLD_DATASETS = (
    "'entities', 'cost_centers', 'accounts', 'actuals', "
    "'activity_types', 'work_centers', 'work_center_rates', 'capacity_actuals'"
)
DATASETS = f"{OLD_DATASETS}, 'staff_types', 'staff_categories', 'staff', 'workforce_actuals'"
MODES = "('fte', 'headcount', 'both', 'positions')"
# Seeded staff types (ADR 0020): code, name, external.
TYPES = (
    ("EMP", "Employee", False),
    ("APP", "Apprentice", False),
    ("TMP", "Temporary (agency)", True),
    ("FRL", "Freelancer", True),
)


def _member(name: str, nullable: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Integer, sa.ForeignKey(f"{S}.member.member_id"), nullable=nullable)


def _changed() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("changed_by", sa.Text),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def _module_fk() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["version_id", "module"], [f"{S}.round_module.version_id", f"{S}.round_module.module"]
    )


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
            {"dim_id": STAFF_TYPE, "code": "staff_type", "name": "Staff type"},
            {"dim_id": STAFF_CATEGORY, "code": "staff_category", "name": "Staff category"},
        ],
    )
    # Alembic's column operations aren't schema-translated.
    op.execute(f"ALTER TABLE {s}.member ADD COLUMN external boolean")  # staff types
    for code, name, external in TYPES:
        op.execute(
            f"INSERT INTO {s}.member (dim_id, code, name, is_group, origin, external) "
            f"VALUES ({STAFF_TYPE}, '{code}', '{name}', false, 'manual', {external})"
        )
    op.execute(
        f"ALTER TABLE {s}.round_module ADD COLUMN mode text, "
        "DROP CONSTRAINT ck_round_module_module, "
        "ADD CONSTRAINT ck_round_module_module "
        "CHECK (module IN ('cost_center', 'capacity', 'workforce')), "
        f"ADD CONSTRAINT ck_round_module_mode CHECK (mode IN {MODES}), "
        "ADD CONSTRAINT ck_round_module_mode_workforce "
        "CHECK ((module = 'workforce') = (mode IS NOT NULL))"
    )
    op.execute(
        f"ALTER TABLE {s}.dataset_mapping DROP CONSTRAINT ck_dataset_mapping_dataset, "
        f"ADD CONSTRAINT ck_dataset_mapping_dataset CHECK (dataset IN ({DATASETS}))"
    )

    op.create_table(
        "fact_workforce",
        sa.Column("version_id", sa.SmallInteger, sa.ForeignKey(f"{S}.version.version_id"),
                  nullable=False),
        sa.Column("layer_id", sa.SmallInteger, nullable=False),
        sa.Column("measure_id", sa.SmallInteger, nullable=False),
        sa.Column("period", sa.Date, nullable=False),
        _member("dim_entity"),
        _member("dim_cost_center"),
        _member("dim_staff_type"),
        _member("dim_staff_category"),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("row_version", sa.BigInteger, nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint(
            "version_id", "layer_id", "measure_id", "period", "dim_entity", "dim_cost_center",
            "dim_staff_type", "dim_staff_category",
        ),
        sa.CheckConstraint("amount >= 0", name="amount"),
        schema=S,
    )  # fmt: skip
    op.create_table(
        "workforce_change_log",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor", sa.Text),
        sa.Column("batch_id", sa.Text),
        sa.Column("op", sa.Text, nullable=False),
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("measure_id", sa.SmallInteger, nullable=False),
        sa.Column("period", sa.Date, nullable=False),
        sa.Column("dim_cost_center", sa.Integer, nullable=False),
        sa.Column("dim_staff_type", sa.Integer, nullable=False),
        sa.Column("dim_staff_category", sa.Integer, nullable=False),
        sa.Column("old_amount", sa.Numeric(18, 2)),
        sa.Column("new_amount", sa.Numeric(18, 2)),
        schema=S,
    )
    op.create_table(
        "workforce_line",
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("module", sa.Text, nullable=False, server_default="workforce"),
        _member("cost_center_id"),
        _member("staff_type_id"),
        _member("staff_category_id"),
        *_changed(),
        sa.PrimaryKeyConstraint(
            "version_id", "cost_center_id", "staff_type_id", "staff_category_id"
        ),
        _module_fk(),
        sa.CheckConstraint("module = 'workforce'", name="module"),
        schema=S,
    )
    op.create_table(
        "workforce_position",
        sa.Column("position_id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("module", sa.Text, nullable=False, server_default="workforce"),
        _member("cost_center_id"),
        sa.Column("code", sa.Text),  # HR's position or personnel number
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("person_name", sa.Text),
        _member("staff_type_id"),
        _member("staff_category_id"),
        sa.Column("entry_date", sa.Date),
        sa.Column("leave_date", sa.Date),
        sa.Column("contract_percent", sa.Numeric(5, 2)),
        sa.Column("is_plan", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_replacement", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("description", sa.Text),
        *_changed(),
        sa.UniqueConstraint("version_id", "code"),
        _module_fk(),
        sa.CheckConstraint("module = 'workforce'", name="module"),
        sa.CheckConstraint("title <> ''", name="title"),
        sa.CheckConstraint("leave_date >= entry_date", name="dates"),
        sa.CheckConstraint("contract_percent > 0 AND contract_percent <= 100", name="contract"),
        sa.CheckConstraint("is_plan OR NOT is_replacement", name="replacement"),
        schema=S,
    )
    op.create_table(
        "workforce_position_log",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor", sa.Text),
        sa.Column("batch_id", sa.Text),
        sa.Column("op", sa.Text, nullable=False),
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("cost_center_id", sa.Integer, nullable=False),
        sa.Column("position_id", sa.Integer, nullable=False),
        sa.Column("old", sa.dialects.postgresql.JSONB),
        sa.Column("new", sa.dialects.postgresql.JSONB),
        schema=S,
    )
    op.create_table(
        "hr_staff",
        sa.Column("code", sa.Text, primary_key=True),
        _member("cost_center_id"),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("person_name", sa.Text),
        _member("staff_type_id"),
        _member("staff_category_id"),
        sa.Column("entry_date", sa.Date),
        sa.Column("leave_date", sa.Date),
        sa.Column("contract_percent", sa.Numeric(5, 2)),
        schema=S,
    )

    # Row-level security: whoever reads the cost center reads its workforce; planners write it.
    def scope(column: str, write: bool) -> str:
        return (
            f"{column} = ANY (ARRAY(SELECT cost_center_id FROM {s}.cost_center_access WHERE "
            "user_id = nullif(current_setting('app.user_id', true), '')::int"
            f"{' AND can_write' if write else ''}))"
        )

    for table, column in (
        ("fact_workforce", "dim_cost_center"),
        ("workforce_line", "cost_center_id"),
        ("workforce_position", "cost_center_id"),
        ("hr_staff", "cost_center_id"),
    ):
        read, write = scope(column, False), scope(column, True)
        op.execute(f"ALTER TABLE {s}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {s}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY read ON {s}.{table} FOR SELECT USING ({read})")
        op.execute(f"CREATE POLICY add ON {s}.{table} FOR INSERT WITH CHECK ({write})")
        op.execute(
            f"CREATE POLICY change ON {s}.{table} FOR UPDATE USING ({write}) WITH CHECK ({write})"
        )
        op.execute(f"CREATE POLICY remove ON {s}.{table} FOR DELETE USING ({write})")

    # Locks: workforce values, lines and positions follow the cost center's plan values (ADR 0008,
    # ADR 0014): the round open, or in setup for controllers; the cost center planned; the round
    # with workforce planning; the status editable. Written in an open round, the first value
    # moves the cost center from not_started to in_progress.
    closed = f"NOT (r.status = 'open' OR {s}.prepares(r.status))"
    problem = f"""
        SELECT CASE
                   WHEN r.version_id IS NULL THEN 'the version has no planning round'
                   WHEN {closed} THEN 'the planning round is ' || r.status
                   WHEN rm.version_id IS NULL THEN 'the round has no workforce planning'
                   WHEN NOT coalesce(m.enabled, false)
                       THEN 'the cost center is not enabled in the round'
                   WHEN coalesce(w.status, 'not_started') NOT IN {EDITABLE}
                       THEN 'the cost center is ' || w.status
               END
        INTO problem
        FROM {{touched}}
        LEFT JOIN {s}.planning_round r ON r.version_id = t.version_id
        LEFT JOIN {s}.round_module rm
            ON rm.version_id = t.version_id AND rm.module = 'workforce'
        LEFT JOIN {s}.round_member m
            ON m.version_id = t.version_id AND m.module = 'cost_center'
            AND m.member_id = t.cost_center_id
        LEFT JOIN {s}.workflow_status w
            ON w.version_id = t.version_id AND w.cost_center_id = t.cost_center_id
        WHERE r.version_id IS NULL OR {closed} OR rm.version_id IS NULL
           OR NOT coalesce(m.enabled, false)
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
    both = "(SELECT * FROM new_rows UNION ALL SELECT * FROM old_rows) u"
    op.execute(f"""
        CREATE FUNCTION {s}.lock_fact_workforce() RETURNS trigger LANGUAGE plpgsql AS $$
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
    row = "(SELECT l.version_id, l.cost_center_id) AS t"
    op.execute(f"""
        CREATE FUNCTION {s}.lock_workforce_row() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            l record;
            problem text;
        BEGIN
            IF TG_OP = 'DELETE' THEN l := OLD; ELSE l := NEW; END IF;
            PERFORM 1 FROM {s}.planning_round WHERE version_id = l.version_id FOR SHARE;
            {problem.format(touched=row)}
            RETURN NULL;
        END $$
    """)
    for table in ("workforce_line", "workforce_position"):
        op.execute(f"""
            CREATE TRIGGER lock AFTER INSERT OR UPDATE OR DELETE ON {s}.{table}
            FOR EACH ROW EXECUTE FUNCTION {s}.lock_workforce_row()
        """)
    # A forecast's closed months take no plan values (ADR 0015).
    for event in ("INSERT", "UPDATE"):
        op.execute(f"""
            CREATE TRIGGER closed_{event.lower()} AFTER {event} ON {s}.fact_workforce
            REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT
            EXECUTE FUNCTION {s}.lock_closed_months()
        """)

    # Audit (ADR 0005): one change-log row per changed plan value; actuals are not logged.
    columns = (
        "actor, batch_id, op, version_id, measure_id, period, dim_cost_center, dim_staff_type, "
        "dim_staff_category, old_amount, new_amount"
    )
    context = (
        "nullif(current_setting('app.actor', true), ''), "
        "nullif(current_setting('app.batch_id', true), '')"
    )
    keys = "version_id, measure_id, period, dim_cost_center, dim_staff_type, dim_staff_category"
    plan_only = f"version_id IN (SELECT version_id FROM {s}.version WHERE kind <> 'actuals')"
    op.execute(f"""
        CREATE FUNCTION {s}.audit_fact_workforce() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO {s}.workforce_change_log ({columns})
                SELECT {context}, 'INSERT', {keys}, NULL, amount
                FROM new_rows WHERE {plan_only};
            ELSIF TG_OP = 'UPDATE' THEN
                INSERT INTO {s}.workforce_change_log ({columns})
                SELECT {context}, 'UPDATE', n.version_id, n.measure_id, n.period,
                       n.dim_cost_center, n.dim_staff_type, n.dim_staff_category,
                       o.amount, n.amount
                FROM new_rows n JOIN old_rows o USING (version_id, layer_id, measure_id, period,
                    dim_entity, dim_cost_center, dim_staff_type, dim_staff_category)
                WHERE n.{plan_only} AND n.amount IS DISTINCT FROM o.amount;
            ELSE
                INSERT INTO {s}.workforce_change_log ({columns})
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
                CREATE TRIGGER {kind}_{event.lower()} AFTER {event} ON {s}.fact_workforce
                REFERENCING {refs} FOR EACH STATEMENT
                EXECUTE FUNCTION {s}.{kind}_fact_workforce()
            """)
    op.execute(f"""
        CREATE FUNCTION {s}.log_workforce_position() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            k {s}.workforce_position;
        BEGIN
            IF TG_OP = 'DELETE' THEN k := OLD; ELSE k := NEW; END IF;
            IF TG_OP = 'UPDATE' AND to_jsonb(OLD) - 'changed_at' - 'changed_by'
                                  = to_jsonb(NEW) - 'changed_at' - 'changed_by' THEN
                RETURN NULL;
            END IF;
            INSERT INTO {s}.workforce_position_log
                (actor, batch_id, op, version_id, cost_center_id, position_id, old, new)
            VALUES (nullif(current_setting('app.actor', true), ''),
                    nullif(current_setting('app.batch_id', true), ''),
                    TG_OP, k.version_id, k.cost_center_id, k.position_id,
                    CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
                    CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END);
            RETURN NULL;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER log AFTER INSERT OR UPDATE OR DELETE ON {s}.workforce_position
        FOR EACH ROW EXECUTE FUNCTION {s}.log_workforce_position()
    """)


def downgrade() -> None:
    s = migration_schema()
    for table in (
        "hr_staff",
        "workforce_position_log",
        "workforce_position",
        "workforce_line",
        "workforce_change_log",
        "fact_workforce",
    ):
        op.drop_table(table, schema=S)
    for fn in (
        "lock_fact_workforce",
        "lock_workforce_row",
        "audit_fact_workforce",
        "log_workforce_position",
    ):
        op.execute(f"DROP FUNCTION {s}.{fn}()")
    op.execute(f"DELETE FROM {s}.round_module WHERE module = 'workforce'")
    op.execute(
        f"ALTER TABLE {s}.round_module DROP CONSTRAINT ck_round_module_mode_workforce, "
        "DROP CONSTRAINT ck_round_module_mode, DROP CONSTRAINT ck_round_module_module, "
        "ADD CONSTRAINT ck_round_module_module CHECK (module IN ('cost_center', 'capacity')), "
        "DROP COLUMN mode"
    )
    op.execute(f"DELETE FROM {s}.dataset_mapping WHERE dataset NOT IN ({OLD_DATASETS})")
    op.execute(
        f"ALTER TABLE {s}.dataset_mapping DROP CONSTRAINT ck_dataset_mapping_dataset, "
        f"ADD CONSTRAINT ck_dataset_mapping_dataset CHECK (dataset IN ({OLD_DATASETS}))"
    )
    op.execute(f"DELETE FROM {s}.member WHERE dim_id IN ({STAFF_TYPE}, {STAFF_CATEGORY})")
    op.execute(f"ALTER TABLE {s}.member DROP COLUMN external")
    op.execute(f"DELETE FROM {s}.dimension WHERE dim_id IN ({STAFF_TYPE}, {STAFF_CATEGORY})")
