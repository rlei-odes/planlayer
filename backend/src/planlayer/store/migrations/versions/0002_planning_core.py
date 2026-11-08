"""Planning core: dimensions, members, hierarchies, versions, the cost center fact table,
change log with audit triggers, sync runs.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA
AMOUNT = sa.Numeric(18, 2)


def upgrade() -> None:
    dimension = op.create_table(
        "dimension",
        sa.Column("dim_id", sa.SmallInteger, primary_key=True, autoincrement=False),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        schema=S,
    )
    op.create_table(
        "member",
        sa.Column("member_id", sa.Integer, primary_key=True),
        sa.Column(
            "dim_id", sa.SmallInteger, sa.ForeignKey(f"{S}.dimension.dim_id"), nullable=False
        ),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("is_group", sa.Boolean, nullable=False),
        sa.Column("entity_code", sa.Text),
        sa.Column("owner", sa.Text),
        sa.Column("account_type", sa.Text),
        sa.Column("valid_from", sa.Date),
        sa.Column("valid_to", sa.Date),
        sa.UniqueConstraint("dim_id", "code"),
        schema=S,
    )
    hierarchy = op.create_table(
        "hierarchy",
        sa.Column("hier_id", sa.SmallInteger, primary_key=True, autoincrement=False),
        sa.Column(
            "dim_id", sa.SmallInteger, sa.ForeignKey(f"{S}.dimension.dim_id"), nullable=False
        ),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        schema=S,
    )
    op.create_table(
        "hier_edge",
        sa.Column(
            "hier_id", sa.SmallInteger, sa.ForeignKey(f"{S}.hierarchy.hier_id"), nullable=False
        ),
        sa.Column("member_id", sa.Integer, sa.ForeignKey(f"{S}.member.member_id"), nullable=False),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey(f"{S}.member.member_id")),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("hier_id", "member_id"),
        schema=S,
    )
    op.create_table(
        "closure",
        sa.Column("hier_id", sa.SmallInteger, nullable=False),
        sa.Column("ancestor_id", sa.Integer, nullable=False),
        sa.Column("descendant_id", sa.Integer, nullable=False),
        sa.Column("depth", sa.SmallInteger, nullable=False),
        sa.PrimaryKeyConstraint("hier_id", "ancestor_id", "descendant_id"),
        schema=S,
    )
    op.create_index("ix_closure_descendant", "closure", ["hier_id", "descendant_id"], schema=S)
    op.create_table(
        "version",
        sa.Column("version_id", sa.SmallInteger, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("fiscal_year", sa.Integer),
        sa.CheckConstraint("kind IN ('actuals', 'budget', 'forecast')", name="kind"),
        sa.CheckConstraint("(kind = 'actuals') = (fiscal_year IS NULL)", name="fiscal_year"),
        schema=S,
    )
    op.create_table(
        "fact_cost_center",
        sa.Column(
            "version_id", sa.SmallInteger, sa.ForeignKey(f"{S}.version.version_id"), nullable=False
        ),
        sa.Column("layer_id", sa.SmallInteger, nullable=False),
        sa.Column("measure_id", sa.SmallInteger, nullable=False),
        sa.Column("period", sa.Date, nullable=False),
        sa.Column("dim_entity", sa.Integer, sa.ForeignKey(f"{S}.member.member_id"), nullable=False),
        sa.Column(
            "dim_cost_center", sa.Integer, sa.ForeignKey(f"{S}.member.member_id"), nullable=False
        ),
        sa.Column(
            "dim_account", sa.Integer, sa.ForeignKey(f"{S}.member.member_id"), nullable=False
        ),
        sa.Column("amount", AMOUNT, nullable=False),
        sa.Column("row_version", sa.BigInteger, nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint(
            "version_id",
            "layer_id",
            "measure_id",
            "period",
            "dim_entity",
            "dim_cost_center",
            "dim_account",
        ),
        schema=S,
    )
    op.create_index(
        "ix_fact_cost_center_cc",
        "fact_cost_center",
        ["version_id", "dim_cost_center", "period"],
        schema=S,
    )
    op.create_table(
        "change_log",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor", sa.Text),
        sa.Column("batch_id", sa.Text),
        sa.Column("op", sa.Text, nullable=False),
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("period", sa.Date, nullable=False),
        sa.Column("dim_cost_center", sa.Integer, nullable=False),
        sa.Column("dim_account", sa.Integer, nullable=False),
        sa.Column("old_amount", AMOUNT),
        sa.Column("new_amount", AMOUNT),
        schema=S,
    )
    op.create_table(
        "sync_run",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("actor", sa.Text),
        sa.Column("entity_filter", sa.Text),
        sa.Column("members", sa.Integer),
        sa.Column("actual_rows", sa.Integer),
        schema=S,
    )

    op.bulk_insert(
        dimension,
        [
            {"dim_id": 1, "code": "entity", "name": "Entity"},
            {"dim_id": 2, "code": "cost_center", "name": "Cost center"},
            {"dim_id": 3, "code": "account", "name": "Account"},
        ],
    )
    op.bulk_insert(
        hierarchy,
        [
            {"hier_id": 1, "dim_id": 2, "code": "cost_center", "name": "Cost centers"},
            {"hier_id": 2, "dim_id": 3, "code": "account", "name": "Accounts"},
        ],
    )

    # Audit (ADR 0005): statement-level triggers, one change-log row per changed plan cell.
    # Actor and batch come from the transaction's context. Actuals are not logged: the sync run
    # records them.
    s = migration_schema()
    columns = (
        "actor, batch_id, op, version_id, period, dim_cost_center, dim_account, "
        "old_amount, new_amount"
    )
    context = (
        "nullif(current_setting('app.actor', true), ''), "
        "nullif(current_setting('app.batch_id', true), '')"
    )
    keys = "version_id, period, dim_cost_center, dim_account"
    plan_only = f"version_id IN (SELECT version_id FROM {s}.version WHERE kind <> 'actuals')"
    op.execute(f"""
        CREATE FUNCTION {s}.audit_fact_cost_center() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO {s}.change_log ({columns})
                SELECT {context}, 'INSERT', {keys}, NULL, amount
                FROM new_rows WHERE {plan_only};
            ELSIF TG_OP = 'UPDATE' THEN
                INSERT INTO {s}.change_log ({columns})
                SELECT {context}, 'UPDATE', n.version_id, n.period, n.dim_cost_center,
                       n.dim_account, o.amount, n.amount
                FROM new_rows n JOIN old_rows o USING (version_id, layer_id, measure_id, period,
                                                      dim_entity, dim_cost_center, dim_account)
                WHERE n.{plan_only} AND n.amount IS DISTINCT FROM o.amount;
            ELSE
                INSERT INTO {s}.change_log ({columns})
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
        op.execute(f"""
            CREATE TRIGGER audit_{event.lower()} AFTER {event} ON {s}.fact_cost_center
            REFERENCING {refs} FOR EACH STATEMENT EXECUTE FUNCTION {s}.audit_fact_cost_center()
        """)


def downgrade() -> None:
    s = migration_schema()
    op.execute(f"DROP FUNCTION {s}.audit_fact_cost_center() CASCADE")
    for name in (
        "sync_run",
        "change_log",
        "fact_cost_center",
        "version",
        "closure",
        "hier_edge",
        "hierarchy",
        "member",
        "dimension",
    ):
        op.drop_table(name, schema=S)
