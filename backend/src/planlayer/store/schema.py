"""Table definitions of the app store.

Tables are declared in the symbolic schema ``APP_SCHEMA``. The engine translates it to the
configured schema name (``schema_translate_map``), so code and migrations never format schema
names into SQL. Raw SQL strings are not translated; write them with SQLAlchemy constructs.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    func,
)

APP_SCHEMA = "planlayer_app"

# Constraint names are deterministic, so migrations can refer to them.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(schema=APP_SCHEMA, naming_convention=NAMING_CONVENTION)

# Fixed ids of the dimensions and hierarchies the cost center planning area uses. The dimension
# registry (D-04, E-03) replaces these constants.
ENTITY, COST_CENTER, ACCOUNT = 1, 2, 3
H_COST_CENTER, H_ACCOUNT = 1, 2
BASE_LAYER = 1  # T-23
AMOUNT = 1  # measure, T-23

AMOUNT_TYPE = Numeric(18, 2)  # T-12

# One row per installation: settings that hold for the whole installation.
installation = Table(
    "installation",
    metadata,
    Column("id", SmallInteger, primary_key=True, server_default="1"),
    # First month of the fiscal year (T-18).
    Column("fiscal_year_start_month", SmallInteger, nullable=False, server_default="1"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("id = 1", name="single_row"),
    CheckConstraint("fiscal_year_start_month BETWEEN 1 AND 12", name="fiscal_month"),
)

dimension = Table(
    "dimension",
    metadata,
    Column("dim_id", SmallInteger, primary_key=True, autoincrement=False),
    Column("code", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
)

# Members of every dimension, synced from the source (T-17). Codes are the source's codes.
member = Table(
    "member",
    metadata,
    Column("member_id", Integer, primary_key=True),
    Column("dim_id", SmallInteger, ForeignKey("dimension.dim_id"), nullable=False),
    Column("code", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("is_group", Boolean, nullable=False),
    # Cost centers: the entity they belong to, and their owner (T-17).
    Column("entity_code", Text),
    Column("owner", Text),
    Column("account_type", Text),  # accounts: revenue / cost (T-7)
    Column("valid_from", Date),
    Column("valid_to", Date),
    UniqueConstraint("dim_id", "code"),
)

hierarchy = Table(
    "hierarchy",
    metadata,
    Column("hier_id", SmallInteger, primary_key=True, autoincrement=False),
    Column("dim_id", SmallInteger, ForeignKey("dimension.dim_id"), nullable=False),
    Column("code", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
)

# Parent-child edges and their closure (ADR 0002). The closure is rebuilt after each sync.
hier_edge = Table(
    "hier_edge",
    metadata,
    Column("hier_id", SmallInteger, ForeignKey("hierarchy.hier_id"), nullable=False),
    Column("member_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("parent_id", Integer, ForeignKey("member.member_id")),
    Column("sort_order", Integer, nullable=False, server_default="0"),
    PrimaryKeyConstraint("hier_id", "member_id"),
)

closure = Table(
    "closure",
    metadata,
    Column("hier_id", SmallInteger, nullable=False),
    Column("ancestor_id", Integer, nullable=False),
    Column("descendant_id", Integer, nullable=False),
    Column("depth", SmallInteger, nullable=False),
    PrimaryKeyConstraint("hier_id", "ancestor_id", "descendant_id"),
)

version = Table(
    "version",
    metadata,
    Column("version_id", SmallInteger, primary_key=True),
    Column("code", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("kind", Text, nullable=False),
    # Plan versions cover one fiscal year; the actuals version covers all synced periods.
    Column("fiscal_year", Integer),
    CheckConstraint("kind IN ('actuals', 'budget', 'forecast')", name="kind"),
    CheckConstraint("(kind = 'actuals') = (fiscal_year IS NULL)", name="fiscal_year"),
)

# Fact table of the cost center planning area (ADR 0001): one column per dimension.
fact_cost_center = Table(
    "fact_cost_center",
    metadata,
    Column("version_id", SmallInteger, ForeignKey("version.version_id"), nullable=False),
    Column("layer_id", SmallInteger, nullable=False),
    Column("measure_id", SmallInteger, nullable=False),
    Column("period", Date, nullable=False),
    Column("dim_entity", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("dim_cost_center", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("dim_account", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("amount", AMOUNT_TYPE, nullable=False),
    # Per-cell optimistic concurrency (D-19).
    Column("row_version", BigInteger, nullable=False, server_default="1"),
    PrimaryKeyConstraint(
        "version_id", "layer_id", "measure_id", "period", "dim_entity", "dim_cost_center",
        "dim_account",
    ),
)  # fmt: skip

# One row per changed plan cell, written by statement-level triggers (ADR 0005).
change_log = Table(
    "change_log",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("actor", Text),
    Column("batch_id", Text),
    Column("op", Text, nullable=False),
    Column("version_id", SmallInteger, nullable=False),
    Column("period", Date, nullable=False),
    Column("dim_cost_center", Integer, nullable=False),
    Column("dim_account", Integer, nullable=False),
    Column("old_amount", AMOUNT_TYPE),
    Column("new_amount", AMOUNT_TYPE),
)

sync_run = Table(
    "sync_run",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    Column("actor", Text),
    Column("entity_filter", Text),
    Column("members", Integer),
    Column("actual_rows", Integer),
)
