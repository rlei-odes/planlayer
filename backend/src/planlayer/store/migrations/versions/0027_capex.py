"""Capex planning (ADR 0028): investments of a round, planned per cost center, grouped into large
projects, confirmed by approvers and controllers, with an optional cash-out schedule.

``capex_type`` holds the installation's investment types; ``capex_setting`` a round's threshold
for large projects and whether it plans cash out; ``capex_item`` its investments and large
projects; ``capex_cash_out`` an investment's cash out by period; ``capex_comment`` the comments
on an investment; ``capex_log`` every change. The view ``capex_confirmer`` lists who confirms
the investments of which cost center.

Revision ID: 0027
Revises: 0026
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA, COST_CENTER, H_COST_CENTER

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA
ME = "nullif(current_setting('app.user_id', true), '')::int"


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


MODULES = "'cost_center', 'capacity', 'workforce', 'personnel_costs'"


def upgrade() -> None:
    s = migration_schema()
    member = f"{S}.member.member_id"
    op.execute(
        f"ALTER TABLE {s}.round_module DROP CONSTRAINT ck_round_module_module, "
        f"ADD CONSTRAINT ck_round_module_module CHECK (module IN ({MODULES}, 'capex'))"
    )
    op.create_table(
        "capex_type",
        sa.Column("type_id", sa.SmallInteger, sa.Identity(), primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("asset_account", sa.Text),  # the balance sheet account it is capitalised on
        sa.Column("useful_life", sa.SmallInteger),  # years, the default for depreciation
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.CheckConstraint("useful_life BETWEEN 1 AND 100", name="useful_life"),
        schema=S,
    )
    op.create_table(
        "capex_setting",
        sa.Column("version_id", sa.SmallInteger, primary_key=True),
        sa.Column("module", sa.Text, nullable=False, server_default="capex"),
        sa.Column("project_threshold", sa.Numeric(18, 2)),
        sa.Column("cash_out", sa.Boolean, nullable=False, server_default="false"),
        *_changed(),
        _module_fk(),
        sa.CheckConstraint("module = 'capex'", name="module"),
        sa.CheckConstraint("project_threshold > 0", name="project_threshold"),
        schema=S,
    )
    op.create_table(
        "capex_item",
        sa.Column("item_id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("module", sa.Text, nullable=False, server_default="capex"),
        sa.Column("kind", sa.Text, nullable=False),  # line / project
        sa.Column("project_id", sa.Integer),  # a line of a large project
        sa.Column("cost_center_id", sa.Integer, sa.ForeignKey(member), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("type_id", sa.SmallInteger, sa.ForeignKey(f"{S}.capex_type.type_id")),
        sa.Column("priority", sa.SmallInteger),
        sa.Column("start_month", sa.Date),
        sa.Column("amount", sa.Numeric(18, 2)),
        sa.Column("status", sa.Text),  # open / confirmed / declined
        sa.Column("status_by", sa.Text),
        sa.Column("status_at", sa.DateTime(timezone=True)),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_by", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        *_changed(),
        _module_fk(),
        sa.UniqueConstraint("item_id", "cost_center_id", name="uq_capex_item_cost_center"),
        # A project's lines are on its cost center, and follow it when it moves.
        sa.ForeignKeyConstraint(
            ["project_id", "cost_center_id"],
            [f"{S}.capex_item.item_id", f"{S}.capex_item.cost_center_id"],
            name="fk_capex_item_project",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.CheckConstraint("module = 'capex'", name="module"),
        sa.CheckConstraint("kind IN ('line', 'project')", name="kind"),
        sa.CheckConstraint("btrim(name) <> ''", name="name"),
        sa.CheckConstraint("priority BETWEEN 1 AND 3", name="priority"),
        sa.CheckConstraint("extract(day FROM start_month) = 1", name="start_month"),
        sa.CheckConstraint("amount >= 0", name="amount"),
        sa.CheckConstraint("status IN ('open', 'confirmed', 'declined')", name="status"),
        # A project groups lines: it has no type, start or amount of its own, and is in none.
        sa.CheckConstraint(
            "kind = 'line' OR (project_id IS NULL AND type_id IS NULL AND start_month IS NULL "
            "AND amount IS NULL)",
            name="project",
        ),
        # A project's lines take its priority and confirmation.
        sa.CheckConstraint(
            "(project_id IS NULL) = (priority IS NOT NULL) "
            "AND (project_id IS NULL) = (status IS NOT NULL)",
            name="in_project",
        ),
        schema=S,
    )
    op.create_index("ix_capex_item_version", "capex_item", ["version_id"], schema=S)
    op.create_table(
        "capex_cash_out",
        sa.Column(
            "item_id",
            sa.Integer,
            sa.ForeignKey(f"{S}.capex_item.item_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period", sa.Date, nullable=False),  # a month, or a quarter's first month
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.PrimaryKeyConstraint("item_id", "period"),
        sa.CheckConstraint("amount > 0", name="amount"),
        sa.CheckConstraint("extract(day FROM period) = 1", name="period"),
        schema=S,
    )
    op.create_table(
        "capex_comment",
        sa.Column("comment_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "item_id",
            sa.Integer,
            sa.ForeignKey(f"{S}.capex_item.item_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author_id", sa.Integer, sa.ForeignKey(f"{S}.app_user.user_id"), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("btrim(body) <> ''", name="body"),
        schema=S,
    )
    op.create_index("ix_capex_comment_item", "capex_comment", ["item_id"], schema=S)
    op.create_table(
        "capex_log",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor", sa.Text),
        sa.Column("batch_id", sa.Text),
        sa.Column("op", sa.Text, nullable=False),
        sa.Column("target", sa.Text, nullable=False),
        sa.Column("version_id", sa.SmallInteger),
        sa.Column("item_id", sa.Integer, nullable=False),
        sa.Column("old", sa.dialects.postgresql.JSONB),
        sa.Column("new", sa.dialects.postgresql.JSONB),
        schema=S,
    )

    # Who confirms a cost center's investments: controllers, and the approvers above it who
    # don't plan it themselves (ADR 0008, ADR 0009).
    op.execute(f"""
        CREATE VIEW {s}.capex_confirmer AS
        SELECT u.user_id, m.member_id AS cost_center_id
        FROM {s}.app_user u CROSS JOIN {s}.member m
        WHERE u.is_controller AND NOT u.disabled AND m.dim_id = {COST_CENTER} AND NOT m.is_group
        UNION
        SELECT a.user_id, c.descendant_id
        FROM {s}.approver_assignment a JOIN {s}.app_user u USING (user_id)
        JOIN {s}.closure c ON c.hier_id = {H_COST_CENTER} AND c.ancestor_id = a.member_id
        WHERE NOT u.disabled
          AND NOT EXISTS (
              SELECT 1 FROM {s}.cost_center_owner o
              WHERE o.user_id = a.user_id AND o.cost_center_id = c.descendant_id)
          AND NOT EXISTS (
              SELECT 1 FROM {s}.planner_grant g
              JOIN {s}.closure p ON p.hier_id = {H_COST_CENTER} AND p.ancestor_id = g.member_id
              WHERE g.user_id = a.user_id AND p.descendant_id = c.descendant_id)
    """)

    # Row-level security: investments as the cost center's plan values; confirmers change them
    # too (the rules trigger lets them change the confirmation only).
    def access(write: bool) -> str:
        return (
            f"ARRAY(SELECT cost_center_id FROM {s}.cost_center_access WHERE user_id = {ME}"
            f"{' AND can_write' if write else ''})"
        )

    readable = f"cost_center_id = ANY ({access(False)})"
    writable = f"cost_center_id = ANY ({access(True)})"
    confirms = (
        f"cost_center_id = ANY (ARRAY(SELECT cost_center_id FROM {s}.capex_confirmer "
        f"WHERE user_id = {ME}))"
    )
    item = f"{s}.capex_item"
    in_item = f"item_id IN (SELECT item_id FROM {item})"
    in_writable = f"item_id IN (SELECT item_id FROM {item} WHERE {writable})"
    policies = {
        "capex_item": (readable, writable, f"{writable} OR {confirms}", writable),
        "capex_cash_out": (in_item, in_writable, in_writable, in_writable),
        "capex_comment": (
            in_item,
            f"{in_item} AND author_id = {ME}",
            "false",
            f"{in_item} AND author_id = {ME}",
        ),
    }
    for table, (read, add, change, remove) in policies.items():
        t = f"{s}.{table}"
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY read ON {t} FOR SELECT USING ({read})")
        op.execute(f"CREATE POLICY add ON {t} FOR INSERT WITH CHECK ({add})")
        op.execute(f"CREATE POLICY change ON {t} FOR UPDATE USING ({change}) WITH CHECK ({change})")
        op.execute(f"CREATE POLICY remove ON {t} FOR DELETE USING ({remove})")

    # The rules of an investment that the database keeps:
    # - only a confirmer changes the confirmation; a confirmer who doesn't plan the cost center
    #   changes nothing else;
    # - a change of what was confirmed (amount, start, type, cost center, project) opens it
    #   again; a line's change opens its project (after the row, below);
    # - a project's lines are lines of a project.
    op.execute(f"""
        CREATE FUNCTION {s}.capex_item_rules() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            me int := {ME};
            writes boolean;
            confirms boolean;
            content boolean := false;
        BEGIN
            SELECT EXISTS (SELECT 1 FROM {s}.cost_center_access
                           WHERE user_id = me AND cost_center_id = NEW.cost_center_id
                             AND can_write),
                   EXISTS (SELECT 1 FROM {s}.capex_confirmer
                           WHERE user_id = me AND cost_center_id = NEW.cost_center_id)
            INTO writes, confirms;
            IF NEW.project_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM {s}.capex_item WHERE item_id = NEW.project_id AND kind = 'project'
            ) THEN
                RAISE EXCEPTION 'an investment can only be part of a large project'
                    USING ERRCODE = 'PL003';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF coalesce(NEW.status, 'open') <> 'open' AND NOT confirms THEN
                    RAISE EXCEPTION 'only an approver above the cost center or a controller '
                        'confirms investments' USING ERRCODE = 'PL003';
                END IF;
                RETURN NEW;
            END IF;
            content := (NEW.amount, NEW.start_month, NEW.type_id, NEW.cost_center_id,
                        NEW.project_id, NEW.kind)
                       IS DISTINCT FROM
                       (OLD.amount, OLD.start_month, OLD.type_id, OLD.cost_center_id,
                        OLD.project_id, OLD.kind);
            -- Back to open, or into a project, is anyone's who writes; a confirmation isn't.
            IF NEW.status IS DISTINCT FROM OLD.status
               AND coalesce(NEW.status, 'open') <> 'open' AND NOT confirms THEN
                RAISE EXCEPTION 'only an approver above the cost center or a controller '
                    'confirms investments' USING ERRCODE = 'PL003';
            END IF;
            IF NOT writes AND (content OR (NEW.name, NEW.priority)
                                          IS DISTINCT FROM (OLD.name, OLD.priority)) THEN
                RAISE EXCEPTION 'investments are changed by those who plan the cost center'
                    USING ERRCODE = 'PL003';
            END IF;
            IF content AND NEW.status IS NOT DISTINCT FROM OLD.status
               AND NEW.status IS DISTINCT FROM 'open' AND NEW.status IS NOT NULL THEN
                NEW.status := 'open';
                NEW.status_by := NULL;
                NEW.status_at := NULL;
            END IF;
            IF to_jsonb(NEW) - 'changed_at' - 'changed_by' - 'row_version'
               IS DISTINCT FROM to_jsonb(OLD) - 'changed_at' - 'changed_by' - 'row_version' THEN
                NEW.row_version := OLD.row_version + 1;
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER rules BEFORE INSERT OR UPDATE ON {item}
        FOR EACH ROW EXECUTE FUNCTION {s}.capex_item_rules()
    """)
    # A change to a project's lines opens the project's confirmation again.
    op.execute(f"""
        CREATE FUNCTION {s}.capex_reopen_project() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            projects int[];
        BEGIN
            IF TG_OP = 'INSERT' THEN
                projects := ARRAY[NEW.project_id];
            ELSIF TG_OP = 'DELETE' THEN
                projects := ARRAY[OLD.project_id];
            ELSIF (NEW.amount, NEW.start_month, NEW.type_id, NEW.project_id)
                  IS DISTINCT FROM (OLD.amount, OLD.start_month, OLD.type_id, OLD.project_id) THEN
                projects := ARRAY[OLD.project_id, NEW.project_id];
            ELSE
                RETURN NULL;
            END IF;
            UPDATE {item} SET status = 'open', status_by = NULL, status_at = NULL
            WHERE item_id = ANY (projects) AND status <> 'open';
            RETURN NULL;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER reopen AFTER INSERT OR UPDATE OR DELETE ON {item}
        FOR EACH ROW EXECUTE FUNCTION {s}.capex_reopen_project()
    """)

    # Locks: the round open, or in setup for controllers (ADR 0014); the cost center planned in
    # it. The cost center's workflow status doesn't lock investments: they are confirmed one by
    # one (ADR 0028).
    closed = f"NOT (r.status = 'open' OR {s}.prepares(r.status))"
    op.execute(f"""
        CREATE FUNCTION {s}.lock_capex() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            row_item int;
            v smallint;
            cc int;
            problem text;
        BEGIN
            IF TG_TABLE_NAME = 'capex_item' THEN
                IF TG_OP = 'DELETE' THEN v := OLD.version_id; cc := OLD.cost_center_id;
                ELSE v := NEW.version_id; cc := NEW.cost_center_id; END IF;
            ELSE
                IF TG_OP = 'DELETE' THEN row_item := OLD.item_id;
                ELSE row_item := NEW.item_id; END IF;
                SELECT i.version_id, i.cost_center_id INTO v, cc
                FROM {item} i WHERE i.item_id = row_item;
                IF NOT FOUND THEN
                    RETURN NULL;  -- the investment itself was deleted, and checked
                END IF;
            END IF;
            SELECT CASE
                       WHEN {closed} THEN 'the planning round is ' || r.status
                       WHEN NOT coalesce(m.enabled, false)
                           THEN 'the cost center is not planned in the round'
                   END
            INTO problem
            FROM {s}.planning_round r
            LEFT JOIN {s}.round_member m
                ON m.version_id = r.version_id AND m.module = 'cost_center'
                AND m.member_id = cc
            WHERE r.version_id = v
            FOR SHARE OF r;
            IF problem IS NOT NULL THEN
                RAISE EXCEPTION 'plan values are locked: %', problem USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$
    """)
    for table in ("capex_item", "capex_cash_out"):
        op.execute(f"""
            CREATE TRIGGER lock AFTER INSERT OR UPDATE OR DELETE ON {s}.{table}
            FOR EACH ROW EXECUTE FUNCTION {s}.lock_capex()
        """)

    # Cash out never exceeds the investment's amount, and only lines have it: checked at commit,
    # so an amount and its schedule change in either order.
    op.execute(f"""
        CREATE FUNCTION {s}.check_capex_cash_out() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            i record;
            scheduled numeric;
        BEGIN
            SELECT kind, amount INTO i FROM {item} WHERE item_id = NEW.item_id;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;
            SELECT coalesce(sum(amount), 0) INTO scheduled
            FROM {s}.capex_cash_out WHERE item_id = NEW.item_id;
            IF scheduled > 0 AND i.kind <> 'line' THEN
                RAISE EXCEPTION 'a large project''s cash out is its lines'''
                    USING ERRCODE = 'PL003';
            END IF;
            IF scheduled > coalesce(i.amount, 0) THEN
                RAISE EXCEPTION 'cash out of % is more than the investment''s amount', scheduled
                    USING ERRCODE = 'PL003';
            END IF;
            RETURN NULL;
        END $$
    """)
    op.execute(f"""
        CREATE CONSTRAINT TRIGGER cash_out AFTER INSERT OR UPDATE ON {s}.capex_cash_out
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION {s}.check_capex_cash_out()
    """)
    op.execute(f"""
        CREATE CONSTRAINT TRIGGER cash_out AFTER UPDATE OF amount, kind ON {item}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION {s}.check_capex_cash_out()
    """)

    # Audit: every change as old and new row, with the request's actor and change batch.
    op.execute(f"""
        CREATE FUNCTION {s}.log_capex() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            l record;
            v smallint;
        BEGIN
            IF TG_OP = 'DELETE' THEN l := OLD; ELSE l := NEW; END IF;
            IF TG_OP = 'UPDATE' AND to_jsonb(OLD) - 'changed_at' - 'changed_by' - 'row_version'
                                  = to_jsonb(NEW) - 'changed_at' - 'changed_by' - 'row_version'
            THEN
                RETURN NULL;
            END IF;
            IF TG_TABLE_NAME = 'capex_item' THEN
                v := l.version_id;
            ELSE
                SELECT version_id INTO v FROM {item} WHERE item_id = l.item_id;
            END IF;
            INSERT INTO {s}.capex_log (actor, batch_id, op, target, version_id, item_id, old, new)
            VALUES (nullif(current_setting('app.actor', true), ''),
                    nullif(current_setting('app.batch_id', true), ''),
                    TG_OP, TG_TABLE_NAME, v, l.item_id,
                    CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
                    CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END);
            RETURN NULL;
        END $$
    """)
    for table in ("capex_item", "capex_cash_out", "capex_comment"):
        op.execute(f"""
            CREATE TRIGGER log AFTER INSERT OR UPDATE OR DELETE ON {s}.{table}
            FOR EACH ROW EXECUTE FUNCTION {s}.log_capex()
        """)


def downgrade() -> None:
    s = migration_schema()
    for table in ("capex_log", "capex_comment", "capex_cash_out", "capex_item"):
        op.drop_table(table, schema=S)
    op.execute(f"DROP VIEW {s}.capex_confirmer")
    for table in ("capex_setting", "capex_type"):
        op.drop_table(table, schema=S)
    op.execute(f"DELETE FROM {s}.round_module WHERE module = 'capex'")
    op.execute(
        f"ALTER TABLE {s}.round_module DROP CONSTRAINT ck_round_module_module, "
        f"ADD CONSTRAINT ck_round_module_module CHECK (module IN ({MODULES}))"
    )
    for fn in (
        "capex_item_rules",
        "capex_reopen_project",
        "lock_capex",
        "check_capex_cash_out",
        "log_capex",
    ):
        op.execute(f"DROP FUNCTION {s}.{fn}()")
