"""Personnel costs (ADR 0022): the HR role, who reads pay, HR's salaries, pay per position with
its log, the round's rules, small groups and HR's review; personnel accounts written like
forwarding accounts, and small groups hidden from readers of the cost center who don't read pay.

Revision ID: 0019
Revises: 0018
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA
COST_CENTER, H_COST_CENTER, H_ACCOUNT = 2, 1, 2
EDITABLE = "('not_started', 'in_progress')"
MODULE = "personnel_costs"
KINDS = "('extra', 'raise', 'award', 'contribution')"
USER = "nullif(current_setting('app.user_id', true), '')::int"


def _member(name: str, nullable: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Integer, sa.ForeignKey(f"{S}.member.member_id"), nullable=nullable)


def _changed() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("changed_by", sa.Text),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def _module() -> list[sa.Column | sa.Constraint]:  # type: ignore[type-arg]
    return [
        sa.Column("module", sa.Text, nullable=False, server_default=MODULE),
        sa.ForeignKeyConstraint(
            ["version_id", "module"],
            [f"{S}.round_module.version_id", f"{S}.round_module.module"],
        ),
        sa.CheckConstraint(f"module = '{MODULE}'", name="module"),
    ]


def upgrade() -> None:
    s = migration_schema()
    op.execute(f"ALTER TABLE {s}.app_user ADD COLUMN is_hr boolean NOT NULL DEFAULT false")
    op.execute(
        f"ALTER TABLE {s}.round_module DROP CONSTRAINT ck_round_module_module, "
        "ADD CONSTRAINT ck_round_module_module "
        f"CHECK (module IN ('cost_center', 'capacity', 'workforce', '{MODULE}'))"
    )
    op.execute(f"""
        CREATE FUNCTION {s}.writes_as_hr() RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (
                SELECT 1 FROM {s}.app_user u
                WHERE u.user_id = {USER} AND u.is_hr AND NOT u.disabled)
        $$
    """)
    # Who reads and writes pay (ADR 0022): HR everywhere, the system user to compute totals, and
    # the owner the source names on their cost center; not the controller's override.
    op.execute(f"""
        CREATE VIEW {s}.pay_access AS
        SELECT u.user_id, m.member_id AS cost_center_id
        FROM {s}.app_user u CROSS JOIN {s}.member m
        WHERE (u.is_hr OR u.is_system) AND NOT u.disabled
          AND m.dim_id = {COST_CENTER} AND NOT m.is_group
        UNION
        SELECT o.matched_user_id, o.cost_center_id
        FROM {s}.cost_center_owner o JOIN {s}.app_user u ON u.user_id = o.matched_user_id
        WHERE NOT u.disabled
    """)
    pays = f"ARRAY(SELECT cost_center_id FROM {s}.pay_access WHERE user_id = {USER})"

    # HR reads positions and HR's staff list everywhere, for the names beside the pay.
    for table, column in (("workforce_position", "cost_center_id"), ("hr_staff", "cost_center_id")):
        op.execute(f"DROP POLICY read ON {s}.{table}")
        op.execute(
            f"CREATE POLICY read ON {s}.{table} FOR SELECT USING ({s}.writes_as_hr() OR "
            f"{column} = ANY (ARRAY(SELECT cost_center_id FROM {s}.cost_center_access "
            f"WHERE user_id = {USER})))"
        )

    op.create_table(
        "hr_pay",
        sa.Column("code", sa.Text, primary_key=True),
        _member("cost_center_id"),
        sa.Column("monthly_salary", sa.Numeric(18, 2), nullable=False),
        sa.CheckConstraint("monthly_salary >= 0", name="salary"),
        schema=S,
    )
    op.create_table(
        "pay_setting",
        sa.Column("version_id", sa.SmallInteger, primary_key=True),
        *_module(),
        sa.Column("min_group", sa.SmallInteger, nullable=False, server_default="3"),
        _member("bonus_account_id", nullable=True),
        *_changed(),
        sa.CheckConstraint("min_group >= 1", name="min_group"),
        schema=S,
    )
    op.create_table(
        "pay_staff_type",
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        *_module(),
        _member("staff_type_id"),
        _member("account_id"),
        sa.Column("typed", sa.Boolean, nullable=False),
        sa.PrimaryKeyConstraint("version_id", "staff_type_id"),
        schema=S,
    )
    op.create_table(
        "pay_rule",
        sa.Column("rule_id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        *_module(),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        _member("account_id"),
        _member("entity_id", nullable=True),
        sa.Column("salaries", sa.Numeric(8, 4)),  # extra: salaries per year
        sa.Column("percent", sa.Numeric(7, 4)),  # raise, contribution
        sa.Column("ceiling", sa.Numeric(18, 2)),  # contribution: monthly
        sa.Column("starts", sa.Date),  # raise: its first month
        *_changed(),
        sa.CheckConstraint(f"kind IN {KINDS}", name="kind"),
        sa.CheckConstraint("name <> ''", name="name"),
        sa.CheckConstraint(
            "(kind = 'extra') = (salaries IS NOT NULL) AND coalesce(salaries, 1) > 0",
            name="salaries",
        ),
        sa.CheckConstraint(
            "(kind IN ('raise', 'contribution')) = (percent IS NOT NULL) "
            "AND coalesce(percent, 1) > 0",
            name="percent",
        ),
        sa.CheckConstraint("ceiling IS NULL OR kind = 'contribution' AND ceiling > 0",
                           name="ceiling"),
        sa.CheckConstraint("(kind = 'raise') = (starts IS NOT NULL)", name="starts"),
        schema=S,
    )  # fmt: skip
    op.create_table(
        "pay_rule_step",
        sa.Column(
            "rule_id",
            sa.Integer,
            sa.ForeignKey(f"{S}.pay_rule.rule_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("years", sa.SmallInteger, nullable=False),
        sa.Column("salaries", sa.Numeric(8, 4)),
        sa.Column("amount", sa.Numeric(18, 2)),
        sa.PrimaryKeyConstraint("rule_id", "years"),
        sa.CheckConstraint("years > 0", name="years"),
        sa.CheckConstraint(
            "(salaries IS NULL) <> (amount IS NULL) AND coalesce(salaries, amount) > 0",
            name="one",
        ),
        schema=S,
    )
    op.create_table(
        "position_pay",
        sa.Column(
            "position_id",
            sa.Integer,
            sa.ForeignKey(f"{S}.workforce_position.position_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        *_module(),
        sa.Column("hr_salary", sa.Numeric(18, 2)),  # taken from HR; read only
        sa.Column("salary", sa.Numeric(18, 2)),  # planned, monthly; None: HR's
        sa.Column("bonus", sa.Numeric(18, 2)),  # yearly
        _member("charged_to_id", nullable=True),
        *_changed(),
        sa.CheckConstraint(
            "coalesce(hr_salary, 0) >= 0 AND coalesce(salary, 0) >= 0 AND coalesce(bonus, 0) >= 0",
            name="amounts",
        ),
        schema=S,
    )
    op.create_table(
        "position_pay_log",
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
        "pay_small_group",
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        _member("cost_center_id"),
        _member("account_id"),
        sa.Column("positions", sa.SmallInteger, nullable=False),
        sa.PrimaryKeyConstraint("version_id", "cost_center_id", "account_id"),
        schema=S,
    )
    op.create_table(
        "pay_review",
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        _member("cost_center_id"),
        sa.Column("confirmed_by", sa.Text, nullable=False),
        sa.Column(
            "confirmed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("version_id", "cost_center_id"),
        schema=S,
    )

    # Row-level security on pay: through the position's cost center, for every reader of pay.
    positions = (
        f"position_id IN (SELECT p.position_id FROM {s}.workforce_position p "
        f"WHERE p.cost_center_id = ANY ({pays}))"
    )
    for table, scope in (
        ("hr_pay", f"cost_center_id = ANY ({pays})"),
        ("position_pay", positions),
    ):
        op.execute(f"ALTER TABLE {s}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {s}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY read ON {s}.{table} FOR SELECT USING ({scope})")
        op.execute(f"CREATE POLICY add ON {s}.{table} FOR INSERT WITH CHECK ({scope})")
        op.execute(
            f"CREATE POLICY change ON {s}.{table} FOR UPDATE USING ({scope}) WITH CHECK ({scope})"
        )
        op.execute(f"CREATE POLICY remove ON {s}.{table} FOR DELETE USING ({scope})")
    op.execute(f"ALTER TABLE {s}.position_pay_log ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {s}.position_pay_log FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY read ON {s}.position_pay_log FOR SELECT "
        f"USING (cost_center_id = ANY ({pays}))"
    )
    op.execute(f"CREATE POLICY add ON {s}.position_pay_log FOR INSERT WITH CHECK (true)")

    _lock_position_pay(s, hr_edits_hr_salary=False)
    op.execute(f"""
        CREATE TRIGGER lock AFTER INSERT OR UPDATE OR DELETE ON {s}.position_pay
        FOR EACH ROW EXECUTE FUNCTION {s}.lock_position_pay()
    """)
    op.execute(f"""
        CREATE FUNCTION {s}.log_position_pay() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            k {s}.position_pay;
            cc int;
        BEGIN
            IF TG_OP = 'DELETE' THEN k := OLD; ELSE k := NEW; END IF;
            IF TG_OP = 'UPDATE' AND to_jsonb(OLD) - 'changed_at' - 'changed_by'
                                  = to_jsonb(NEW) - 'changed_at' - 'changed_by' THEN
                RETURN NULL;
            END IF;
            SELECT p.cost_center_id INTO cc FROM {s}.workforce_position p
                WHERE p.position_id = k.position_id;
            IF cc IS NULL THEN  -- deleted with its position, whose log has the row
                RETURN NULL;
            END IF;
            INSERT INTO {s}.position_pay_log
                (actor, batch_id, op, version_id, cost_center_id, position_id, old, new)
            VALUES (nullif(current_setting('app.actor', true), ''),
                    nullif(current_setting('app.batch_id', true), ''),
                    TG_OP, k.version_id, cc, k.position_id,
                    CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
                    CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END);
            RETURN NULL;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER log AFTER INSERT OR UPDATE OR DELETE ON {s}.position_pay
        FOR EACH ROW EXECUTE FUNCTION {s}.log_position_pay()
    """)

    # The rules are HR's and the controllers', and change only in setup or while open.
    op.execute(f"""
        CREATE FUNCTION {s}.guard_pay_rules() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            vid smallint;
            st text;
        BEGIN
            IF TG_TABLE_NAME = 'pay_rule_step' THEN
                SELECT r.version_id INTO vid FROM {s}.pay_rule r WHERE r.rule_id =
                    CASE WHEN TG_OP = 'DELETE' THEN OLD.rule_id ELSE NEW.rule_id END;
                IF vid IS NULL THEN  -- deleted with its rule
                    RETURN NULL;
                END IF;
            ELSE
                vid := CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END;
            END IF;
            IF NOT ({s}.writes_as_hr() OR {s}.writes_as_controller()) THEN
                RAISE EXCEPTION 'plan values are locked: only HR and controllers change the '
                    'personnel cost rules' USING ERRCODE = 'PL001';
            END IF;
            SELECT status INTO st FROM {s}.planning_round WHERE version_id = vid FOR SHARE;
            IF st NOT IN ('setup', 'open') THEN
                RAISE EXCEPTION 'plan values are locked: the planning round is %', st
                    USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$
    """)
    for table in ("pay_setting", "pay_staff_type", "pay_rule", "pay_rule_step"):
        op.execute(f"""
            CREATE TRIGGER guard AFTER INSERT OR UPDATE OR DELETE ON {s}.{table}
            FOR EACH ROW EXECUTE FUNCTION {s}.guard_pay_rules()
        """)
    # Only HR confirms; anyone's change may withdraw a confirmation.
    op.execute(f"""
        CREATE FUNCTION {s}.guard_pay_review() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT {s}.writes_as_hr() THEN
                RAISE EXCEPTION 'plan values are locked: only HR confirms pay'
                    USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER guard AFTER INSERT OR UPDATE ON {s}.pay_review
        FOR EACH ROW EXECUTE FUNCTION {s}.guard_pay_review()
    """)

    # The round's personnel accounts, and every account a calculation writes.
    op.execute(f"""
        CREATE VIEW {s}.pay_account AS
        SELECT version_id, account_id FROM {s}.pay_staff_type
        UNION SELECT version_id, account_id FROM {s}.pay_rule
        UNION SELECT version_id, bonus_account_id FROM {s}.pay_setting
            WHERE bonus_account_id IS NOT NULL
    """)
    op.execute(f"""
        CREATE VIEW {s}.written_account AS
        SELECT DISTINCT version_id, account_id, 'forwarding'::text AS source
        FROM {s}.forwarding_account
        UNION ALL
        SELECT version_id, account_id, 'personnel' FROM {s}.pay_account
    """)
    _lock_fact_cost_center(s)

    # Small groups are hidden from those who read the cost center but not its pay.
    read = (
        f"dim_cost_center = ANY (ARRAY(SELECT cost_center_id FROM {s}.cost_center_access "
        f"WHERE user_id = {USER}))"
    )
    op.execute(f"DROP POLICY read ON {s}.fact_cost_center")
    op.execute(f"""
        CREATE POLICY read ON {s}.fact_cost_center FOR SELECT USING ({read} AND (
            (version_id, dim_cost_center, dim_account) NOT IN
                (SELECT version_id, cost_center_id, account_id FROM {s}.pay_small_group)
            OR dim_cost_center = ANY ({pays})))
    """)


def downgrade() -> None:
    s = migration_schema()
    read = (
        f"dim_cost_center = ANY (ARRAY(SELECT cost_center_id FROM {s}.cost_center_access "
        f"WHERE user_id = {USER}))"
    )
    op.execute(f"DROP POLICY read ON {s}.fact_cost_center")
    op.execute(f"CREATE POLICY read ON {s}.fact_cost_center FOR SELECT USING ({read})")
    path = Path(__file__).with_name("0014_forwarding.py")
    spec = importlib.util.spec_from_file_location("forwarding_0014", path)
    assert spec is not None and spec.loader is not None
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    previous._lock_fact_cost_center(s)
    op.execute(f"DROP VIEW {s}.written_account")
    op.execute(f"DROP VIEW {s}.pay_account")
    for table in (
        "pay_review",
        "pay_small_group",
        "position_pay_log",
        "position_pay",
        "pay_rule_step",
        "pay_rule",
        "pay_staff_type",
        "pay_setting",
        "hr_pay",
    ):
        op.drop_table(table, schema=S)
    for fn in ("lock_position_pay", "log_position_pay", "guard_pay_rules", "guard_pay_review"):
        op.execute(f"DROP FUNCTION {s}.{fn}()")
    for table in ("workforce_position", "hr_staff"):
        op.execute(f"DROP POLICY read ON {s}.{table}")
        op.execute(
            f"CREATE POLICY read ON {s}.{table} FOR SELECT USING (cost_center_id = ANY "
            f"(ARRAY(SELECT cost_center_id FROM {s}.cost_center_access WHERE user_id = {USER})))"
        )
    op.execute(f"DROP VIEW {s}.pay_access")
    op.execute(f"DROP FUNCTION {s}.writes_as_hr()")
    op.execute(f"DELETE FROM {s}.round_module WHERE module = '{MODULE}'")
    op.execute(
        f"ALTER TABLE {s}.round_module DROP CONSTRAINT ck_round_module_module, "
        "ADD CONSTRAINT ck_round_module_module "
        "CHECK (module IN ('cost_center', 'capacity', 'workforce'))"
    )
    op.execute(f"ALTER TABLE {s}.app_user DROP COLUMN is_hr")


def _lock_fact_cost_center(s: str) -> None:
    """The lock of migration 0014, with every written account (``fa``) apart, forwarding's and
    personnel costs' alike: written with controller rights only, whatever the cost center's
    status, outside account sets and not-planned accounts, and moving no status."""
    closed = f"NOT (r.status = 'open' OR {s}.prepares(r.status))"
    collect = (
        "SELECT array_agg(version_id), array_agg(dim_cost_center), array_agg(dim_account) "
        "INTO v, c, a FROM (SELECT DISTINCT version_id, dim_cost_center, dim_account FROM {rows}"
        f" WHERE version_id NOT IN (SELECT version_id FROM {s}.version WHERE kind = 'actuals')) x"
    )
    touched = "unnest(v, c, a) AS t(version_id, dim_cost_center, dim_account)"
    both = "(SELECT * FROM new_rows UNION ALL SELECT * FROM old_rows) u"
    written = f"""
        LEFT JOIN LATERAL (
            SELECT w.account_id, w.source FROM {s}.written_account w
            WHERE w.version_id = t.version_id AND w.account_id = t.dim_account LIMIT 1
        ) fa ON true"""
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
                       WHEN fa.source = 'forwarding'
                           THEN 'the account is written by cost forwarding'
                       WHEN fa.source = 'personnel'
                           THEN 'the account is written by personnel costs'
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
            {written}
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
                          (SELECT t.version_id, t.dim_cost_center FROM {touched} {written}
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


def _lock_position_pay(s: str, *, hr_edits_hr_salary: bool, handover: bool = False) -> None:
    """The lock on pay; HR's salary changes only by the system user, or also by HR (ADR 0023);
    with ``handover``, others change pay only once HR has handed the cost center over."""
    handed = (
        f"""
            IF NOT {s}.writes_as_hr() AND NOT EXISTS (
                    SELECT 1 FROM {s}.pay_handover h
                    WHERE h.version_id = k.version_id AND h.cost_center_id = cc) THEN
                RAISE EXCEPTION 'plan values are locked: HR hasn''t handed this cost center''s '
                    'pay over yet' USING ERRCODE = 'PL001';
            END IF;"""
        if handover
        else ""
    )
    hr_note = "; HR corrects it" if hr_edits_hr_salary else ""
    hr_may = f" AND NOT {s}.writes_as_hr()" if hr_edits_hr_salary else ""
    hr_message = (
        "only HR changes HR''s salary"
        if hr_edits_hr_salary
        else "HR''s salary stays as it was taken"
    )
    # Pay follows its position's locks (ADR 0020); HR prepares it in setup like controllers.
    closed = (
        f"NOT (r.status = 'open' OR r.status = 'setup' "
        f"AND ({s}.writes_as_controller() OR {s}.writes_as_hr()))"
    )
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {s}.lock_position_pay() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            k {s}.position_pay;
            cc int;
            problem text;
            was_hr numeric;
            was_charged int;
            system boolean := EXISTS (SELECT 1 FROM {s}.app_user u
                                      WHERE u.user_id = {USER} AND u.is_system);
        BEGIN
            IF TG_OP = 'DELETE' THEN k := OLD; ELSE k := NEW; END IF;
            SELECT p.cost_center_id INTO cc FROM {s}.workforce_position p
                WHERE p.position_id = k.position_id;
            IF cc IS NULL THEN  -- deleted with its position
                RETURN NULL;
            END IF;
            PERFORM 1 FROM {s}.planning_round WHERE version_id = k.version_id FOR SHARE;
            SELECT CASE
                       WHEN {closed} THEN 'the planning round is ' || r.status
                       WHEN NOT coalesce(m.enabled, false)
                           THEN 'the cost center is not enabled in the round'
                       ELSE 'the cost center is ' || w.status
                   END
            INTO problem
            FROM {s}.planning_round r
            LEFT JOIN {s}.round_member m
                ON m.version_id = r.version_id AND m.module = 'cost_center' AND m.member_id = cc
            LEFT JOIN {s}.workflow_status w
                ON w.version_id = r.version_id AND w.cost_center_id = cc
            WHERE r.version_id = k.version_id
              AND ({closed} OR NOT coalesce(m.enabled, false)
                   OR coalesce(w.status, 'not_started') NOT IN {EDITABLE});
            IF problem IS NOT NULL THEN
                RAISE EXCEPTION 'plan values are locked: %', problem USING ERRCODE = 'PL001';
            END IF;
            IF TG_OP = 'DELETE' OR system THEN
                RETURN NULL;
            END IF;{handed}
            IF TG_OP = 'UPDATE' THEN
                was_hr := OLD.hr_salary;
                was_charged := OLD.charged_to_id;
            END IF;
            -- HR's salary comes with the position, taken by the system user's rights{hr_note}.
            IF NEW.hr_salary IS DISTINCT FROM was_hr{hr_may} THEN
                RAISE EXCEPTION 'plan values are locked: {hr_message}'
                    USING ERRCODE = 'PL001';
            END IF;
            IF NEW.charged_to_id IS DISTINCT FROM was_charged AND NOT {s}.writes_as_hr() THEN
                RAISE EXCEPTION 'plan values are locked: only HR charges a position to another '
                    'cost center' USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$
    """)
