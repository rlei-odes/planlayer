"""The published output in ``planlayer_out`` (ADR 0011): BI-readable tables and a flat view.

Planlayer owns this schema (T-14); it is derived from the app store and rebuilt by republishing,
so its objects are created when missing rather than migrated. Tables are declared in the
symbolic schema ``OUT_SCHEMA``, translated by the output engine like the app store's.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    Connection,
    Date,
    DateTime,
    Engine,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    Table,
    Text,
    create_engine,
)

from planlayer.config import Settings

OUT_SCHEMA = "planlayer_out"
LEVELS = 6  # level columns of the main hierarchy in the dimension tables

out_metadata = MetaData(schema=OUT_SCHEMA)


def _levels() -> list[Column[str]]:
    return [
        c
        for i in range(1, LEVELS + 1)
        for c in (Column(f"level_{i}_code", Text), Column(f"level_{i}_name", Text))
    ]


fact_plan_cost_center = Table(
    "fact_plan_cost_center",
    out_metadata,
    Column("version_code", Text, nullable=False),
    Column("fiscal_year", Integer, nullable=False),
    Column("period", Date, nullable=False),  # first day of the month
    Column("entity_code", Text, nullable=False),
    Column("cost_center_code", Text, nullable=False),
    Column("account_code", Text, nullable=False),
    Column("measure", Text, nullable=False),
    Column("amount", Numeric(18, 2), nullable=False),
    # The amount with the installation's sign convention (ADR 0019); null before the first run
    # that wrote it.
    Column("amount_signed", Numeric(18, 2)),
    Column("status", Text, nullable=False),  # the cost center's workflow status at publish
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("publish_run_id", BigInteger, nullable=False),
    # ``plan``: the round's value; ``actual``: a forecast's closed month at publish (ADR 0015).
    Column("origin", Text, nullable=False, server_default="plan"),
    PrimaryKeyConstraint(
        "version_code", "period", "entity_code", "cost_center_code", "account_code", "measure"
    ),
)

# Planned hours of productive cost centers (ADR 0016); a forecast's closed months as actuals.
fact_plan_capacity = Table(
    "fact_plan_capacity",
    out_metadata,
    Column("version_code", Text, nullable=False),
    Column("fiscal_year", Integer, nullable=False),
    Column("period", Date, nullable=False),
    Column("entity_code", Text, nullable=False),
    Column("cost_center_code", Text, nullable=False),
    Column("work_center_code", Text, nullable=False),
    Column("activity_type_code", Text, nullable=False),
    Column("measure", Text, nullable=False),
    Column("amount", Numeric(18, 2), nullable=False),
    Column("status", Text, nullable=False),
    Column("origin", Text, nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("publish_run_id", BigInteger, nullable=False),
    PrimaryKeyConstraint(
        "version_code",
        "period",
        "entity_code",
        "cost_center_code",
        "work_center_code",
        "activity_type_code",
        "measure",
    ),
)

dim_work_center = Table(
    "dim_work_center",
    out_metadata,
    Column("code", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("cost_center_code", Text),
)

dim_activity_type = Table(
    "dim_activity_type",
    out_metadata,
    Column("code", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("account_code", Text),  # the account its credits go to
)

dim_version = Table(
    "dim_version",
    out_metadata,
    Column("code", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("fiscal_year", Integer, nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("scope", Text, nullable=False),
    Column("actuals_through", Date),  # a forecast's last closed month
)

dim_cost_center = Table(
    "dim_cost_center",
    out_metadata,
    Column("code", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("parent_code", Text),
    Column("depth", SmallInteger),
    Column("entity_code", Text),
    *_levels(),
)

dim_account = Table(
    "dim_account",
    out_metadata,
    Column("code", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("parent_code", Text),
    Column("depth", SmallInteger),
    Column("account_type", Text),
    Column("role", Text),  # leaf accounts: cost, revenue or credit (ADR 0019)
    *_levels(),
)


def create_output_engine(settings: Settings) -> Engine:
    engine = create_engine(settings.output_url, pool_pre_ping=True)
    return engine.execution_options(schema_translate_map={OUT_SCHEMA: settings.output_schema})


def ensure(conn: Connection, schema: str) -> None:
    """Create the output tables and view when missing. ``schema`` is the configured output
    schema, a validated plain identifier (``Settings``); the view's raw SQL needs it."""
    out_metadata.create_all(conn, checkfirst=True)
    s = conn.dialect.identifier_preparer.quote_schema(schema)
    # Columns added since the tables were first created (ADR 0015).
    conn.exec_driver_sql(
        f"ALTER TABLE {s}.fact_plan_cost_center"
        " ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'plan'"
    )
    conn.exec_driver_sql(
        f"ALTER TABLE {s}.dim_version ADD COLUMN IF NOT EXISTS actuals_through date"
    )
    # ADR 0019.
    conn.exec_driver_sql(
        f"ALTER TABLE {s}.fact_plan_cost_center"
        " ADD COLUMN IF NOT EXISTS amount_signed numeric(18,2)"
    )
    conn.exec_driver_sql(f"ALTER TABLE {s}.dim_account ADD COLUMN IF NOT EXISTS role text")
    conn.exec_driver_sql(f"""
        CREATE OR REPLACE VIEW {s}.v_plan_cost_center AS
        SELECT f.version_code, v.name AS version_name, f.fiscal_year, f.period,
               f.entity_code, f.cost_center_code, cc.name AS cost_center_name,
               f.account_code, a.name AS account_name, a.account_type,
               f.measure, f.amount, f.status, f.published_at, f.origin,
               f.amount_signed, a.role
        FROM {s}.fact_plan_cost_center f
        LEFT JOIN {s}.dim_version v ON v.code = f.version_code
        LEFT JOIN {s}.dim_cost_center cc ON cc.code = f.cost_center_code
        LEFT JOIN {s}.dim_account a ON a.code = f.account_code
    """)
    conn.exec_driver_sql(f"""
        CREATE OR REPLACE VIEW {s}.v_plan_capacity AS
        SELECT f.version_code, v.name AS version_name, f.fiscal_year, f.period,
               f.entity_code, f.cost_center_code, cc.name AS cost_center_name,
               f.work_center_code, w.name AS work_center_name,
               f.activity_type_code, t.name AS activity_type_name,
               f.measure, f.amount, f.status, f.origin, f.published_at
        FROM {s}.fact_plan_capacity f
        LEFT JOIN {s}.dim_version v ON v.code = f.version_code
        LEFT JOIN {s}.dim_cost_center cc ON cc.code = f.cost_center_code
        LEFT JOIN {s}.dim_work_center w ON w.code = f.work_center_code
        LEFT JOIN {s}.dim_activity_type t ON t.code = f.activity_type_code
    """)
