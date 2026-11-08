"""Planning rounds, modules and entry forms (ADR 0007); workflow and the lock trigger (ADR 0008).

A planning round extends a plan version one-to-one; the version's fiscal year moves to it.
Existing plan versions become open rounds with the module defaults, every cost center valid in
their fiscal year enabled.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA
FORMS = "'monthly', 'annual', 'annual_even', 'annual_profile', 'annual_reference'"
STATUSES = "'not_started', 'in_progress', 'submitted', 'approved'"


def _fk(target: str) -> sa.ForeignKey:
    return sa.ForeignKey(f"{S}.{target}")


def _module_fk() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["version_id", "module"], [f"{S}.round_module.version_id", f"{S}.round_module.module"]
    )


def _table(name: str, *columns: str) -> sa.Table:
    """An existing table, for queries: a ``Table`` (unlike ``sa.table``) gets the schema
    translation."""
    return sa.Table(name, sa.MetaData(), *(sa.Column(c) for c in columns), schema=S)


def upgrade() -> None:
    planning_round = op.create_table(
        "planning_round",
        sa.Column("version_id", sa.SmallInteger, _fk("version.version_id"), primary_key=True),
        sa.Column("fiscal_year", sa.Integer, nullable=False),
        sa.Column("deadline", sa.Date),
        sa.Column("status", sa.Text, nullable=False, server_default="setup"),
        sa.Column("copied_from", sa.SmallInteger, _fk("planning_round.version_id")),
        sa.CheckConstraint("status IN ('setup', 'open', 'closed')", name="status"),
        schema=S,
    )
    round_module = op.create_table(
        "round_module",
        sa.Column("version_id", sa.SmallInteger, _fk("planning_round.version_id"), nullable=False),
        sa.Column("module", sa.Text, nullable=False),
        sa.Column("default_form", sa.Text, nullable=False, server_default="monthly"),
        sa.PrimaryKeyConstraint("version_id", "module"),
        sa.CheckConstraint("module IN ('cost_center')", name="module"),
        sa.CheckConstraint(f"default_form IN ({FORMS})", name="default_form"),
        schema=S,
    )
    round_member = op.create_table(
        "round_member",
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("module", sa.Text, nullable=False),
        sa.Column("member_id", sa.Integer, _fk("member.member_id"), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.PrimaryKeyConstraint("version_id", "module", "member_id"),
        _module_fk(),
        schema=S,
    )
    op.create_table(
        "seasonality_profile",
        sa.Column("profile_id", sa.SmallInteger, sa.Identity(), primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("entered_as", sa.Text, nullable=False),
        sa.Column("values", ARRAY(sa.Numeric), nullable=False),
        sa.CheckConstraint("entered_as IN ('percent', 'relative')", name="entered_as"),
        sa.CheckConstraint("cardinality(values) = 12", name="twelve_values"),
        schema=S,
    )
    op.create_table(
        "entry_form",
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("module", sa.Text, nullable=False),
        sa.Column("account_id", sa.Integer, _fk("member.member_id"), nullable=False),
        sa.Column("form", sa.Text, nullable=False),
        sa.Column("profile_id", sa.SmallInteger, _fk("seasonality_profile.profile_id")),
        sa.Column("reference_version_id", sa.SmallInteger, _fk("version.version_id")),
        sa.PrimaryKeyConstraint("version_id", "module", "account_id"),
        _module_fk(),
        sa.CheckConstraint(f"form IN ({FORMS})", name="form"),
        # annual_profile needs a profile; monthly may name one as its spread basis.
        sa.CheckConstraint(
            "CASE form WHEN 'annual_profile' THEN profile_id IS NOT NULL "
            "WHEN 'monthly' THEN true ELSE profile_id IS NULL END",
            name="profile",
        ),
        sa.CheckConstraint(
            "form IN ('monthly', 'annual_reference') OR reference_version_id IS NULL",
            name="reference",
        ),
        sa.CheckConstraint("profile_id IS NULL OR reference_version_id IS NULL", name="one_basis"),
        schema=S,
    )
    comparison = op.create_table(
        "comparison",
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("module", sa.Text, nullable=False),
        sa.Column("position", sa.SmallInteger, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("compare_version_id", sa.SmallInteger, _fk("version.version_id")),
        sa.PrimaryKeyConstraint("version_id", "module", "position"),
        _module_fk(),
        sa.CheckConstraint("kind IN ('prior_year_actuals', 'actuals', 'version')", name="kind"),
        sa.CheckConstraint(
            "(kind = 'version') = (compare_version_id IS NOT NULL)", name="compare_version"
        ),
        schema=S,
    )
    op.create_table(
        "sheet_account",
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("module", sa.Text, nullable=False),
        sa.Column("cost_center_id", sa.Integer, _fk("member.member_id"), nullable=False),
        sa.Column("account_id", sa.Integer, _fk("member.member_id"), nullable=False),
        sa.Column("added_by", sa.Text),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("version_id", "module", "cost_center_id", "account_id"),
        _module_fk(),
        schema=S,
    )
    op.create_table(
        "hidden_account",
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("module", sa.Text, nullable=False),
        sa.Column("account_id", sa.Integer, _fk("member.member_id"), nullable=False),
        sa.PrimaryKeyConstraint("version_id", "module", "account_id"),
        _module_fk(),
        schema=S,
    )
    op.create_table(
        "workflow_status",
        sa.Column("version_id", sa.SmallInteger, _fk("planning_round.version_id"), nullable=False),
        sa.Column("cost_center_id", sa.Integer, _fk("member.member_id"), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True)),
        sa.Column("changed_by", sa.Text),
        sa.PrimaryKeyConstraint("version_id", "cost_center_id"),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="status"),
        schema=S,
    )
    op.create_table(
        "workflow_event",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("version_id", sa.SmallInteger, _fk("planning_round.version_id"), nullable=False),
        sa.Column("cost_center_id", sa.Integer, _fk("member.member_id"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor", sa.Text),
        sa.Column("from_status", sa.Text, nullable=False),
        sa.Column("to_status", sa.Text, nullable=False),
        sa.Column("comment", sa.Text),
        schema=S,
    )
    op.create_index(
        "ix_workflow_event_cc", "workflow_event", ["version_id", "cost_center_id"], schema=S
    )
    op.create_table(
        "approver_assignment",
        sa.Column("member_id", sa.Integer, _fk("member.member_id"), nullable=False),
        sa.Column("user_name", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("member_id", "user_name"),
        schema=S,
    )

    # Existing plan versions become open rounds with the module defaults.
    version = _table("version", "version_id", "kind", "fiscal_year")
    member = _table("member", "member_id", "dim_id", "is_group", "valid_from", "valid_to")
    installation = _table("installation", "fiscal_year_start_month")
    plan = (
        sa.select(version.c.version_id, version.c.fiscal_year)
        .where(version.c.kind != "actuals")
        .subquery()
    )
    op.execute(
        planning_round.insert().from_select(
            ["version_id", "fiscal_year", "status"],
            sa.select(plan.c.version_id, plan.c.fiscal_year, sa.literal("open")),
        )
    )
    op.execute(
        round_module.insert().from_select(
            ["version_id", "module"],
            sa.select(plan.c.version_id, sa.literal("cost_center")),
        )
    )
    op.execute(
        comparison.insert().from_select(
            ["version_id", "module", "position", "kind"],
            sa.select(
                plan.c.version_id,
                sa.literal("cost_center"),
                sa.literal(1),
                sa.literal("prior_year_actuals"),
            ),
        )
    )
    # Valid in the fiscal year, as in planlayer.rounds.valid_cost_centers: valid from at most
    # the first day of the last period, and valid to at least the first period.
    start = sa.func.make_date(
        version.c.fiscal_year,
        sa.select(installation.c.fiscal_year_start_month).scalar_subquery(),
        1,
    )
    end = start + sa.text("interval '11 months'")
    op.execute(
        round_member.insert().from_select(
            ["version_id", "module", "member_id", "enabled"],
            sa.select(
                version.c.version_id,
                sa.literal("cost_center"),
                member.c.member_id,
                sa.true(),
            )
            .select_from(version)
            .join(member, sa.true())
            .where(
                version.c.kind != "actuals",
                member.c.dim_id == 2,
                member.c.is_group.is_(False),
                sa.or_(member.c.valid_from.is_(None), member.c.valid_from <= end),
                sa.or_(member.c.valid_to.is_(None), member.c.valid_to >= start),
            ),
        )
    )
    # Alembic's column operations aren't schema-translated; dropping the column drops its check.
    op.execute(f"ALTER TABLE {migration_schema()}.version DROP COLUMN fiscal_year")

    _create_lock_trigger()


def _create_lock_trigger() -> None:
    """Writes to a plan version's facts only while (ADR 0008):

    - the round is open,
    - the cost center is enabled in the round,
    - its status is not_started or in_progress,
    - and the account is not hidden in the round (ADR 0007).

    The first saved value moves a cost center from not_started to in_progress. The round row is
    share-locked and the status rows are locked for update, so a concurrent submit or round
    change either waits for the save or is seen by it. Synced actuals are not affected.
    """
    s = migration_schema()
    # The touched plan cells, as parallel arrays: one query per event, because a statement may
    # only name the transition tables its event has.
    collect = (
        "SELECT array_agg(version_id), array_agg(dim_cost_center), array_agg(dim_account) "
        "INTO v, c, a FROM (SELECT DISTINCT version_id, dim_cost_center, dim_account FROM {rows}"
        f" WHERE version_id NOT IN (SELECT version_id FROM {s}.version WHERE kind = 'actuals')) x"
    )
    touched = "unnest(v, c, a) AS t(version_id, dim_cost_center, dim_account)"
    both = "(SELECT * FROM new_rows UNION ALL SELECT * FROM old_rows) u"
    op.execute(f"""
        CREATE FUNCTION {s}.lock_fact_cost_center() RETURNS trigger LANGUAGE plpgsql AS $$
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
                       WHEN r.status <> 'open' THEN 'the planning round is ' || r.status
                       WHEN NOT coalesce(m.enabled, false)
                           THEN 'the cost center is not enabled in the round'
                       WHEN w.status NOT IN ('not_started', 'in_progress')
                           THEN 'the cost center is ' || w.status
                       ELSE 'the account is hidden in the round'
                   END
            INTO problem
            FROM {touched}
            LEFT JOIN {s}.planning_round r ON r.version_id = t.version_id
            LEFT JOIN {s}.round_member m
                ON m.version_id = t.version_id AND m.module = 'cost_center'
                AND m.member_id = t.dim_cost_center
            LEFT JOIN {s}.workflow_status w
                ON w.version_id = t.version_id AND w.cost_center_id = t.dim_cost_center
            LEFT JOIN {s}.hidden_account h
                ON h.version_id = t.version_id AND h.module = 'cost_center'
                AND h.account_id = t.dim_account
            WHERE r.version_id IS NULL OR r.status <> 'open' OR NOT coalesce(m.enabled, false)
               OR w.status NOT IN ('not_started', 'in_progress') OR h.account_id IS NOT NULL
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
    for event, refs in (
        ("INSERT", "NEW TABLE AS new_rows"),
        ("UPDATE", "NEW TABLE AS new_rows OLD TABLE AS old_rows"),
        ("DELETE", "OLD TABLE AS old_rows"),
    ):
        op.execute(f"""
            CREATE TRIGGER lock_{event.lower()} AFTER {event} ON {s}.fact_cost_center
            REFERENCING {refs} FOR EACH STATEMENT EXECUTE FUNCTION {s}.lock_fact_cost_center()
        """)


def downgrade() -> None:
    s = migration_schema()
    op.execute(f"DROP FUNCTION {s}.lock_fact_cost_center() CASCADE")
    op.execute(f"ALTER TABLE {s}.version ADD COLUMN fiscal_year integer")
    version = _table("version", "version_id", "fiscal_year")
    planning_round = _table("planning_round", "version_id", "fiscal_year")
    op.execute(
        version.update()
        .where(version.c.version_id == planning_round.c.version_id)
        .values(fiscal_year=planning_round.c.fiscal_year)
    )
    op.execute(
        f"ALTER TABLE {s}.version ADD CONSTRAINT ck_version_fiscal_year "
        "CHECK ((kind = 'actuals') = (fiscal_year IS NULL))"
    )
    for name in (
        "approver_assignment",
        "workflow_event",
        "workflow_status",
        "hidden_account",
        "sheet_account",
        "comparison",
        "entry_form",
        "seasonality_profile",
        "round_member",
        "round_module",
        "planning_round",
    ):
        op.drop_table(name, schema=S)
