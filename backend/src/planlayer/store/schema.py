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
    ForeignKeyConstraint,
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
from sqlalchemy.dialects.postgresql import ARRAY

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
    CheckConstraint("kind IN ('actuals', 'budget', 'forecast')", name="kind"),
)

# --- Planning rounds (ADR 0007) -------------------------------------------------------------------

MODULE_COST_CENTER = "cost_center"

# A plan version for one fiscal year, one-to-one; the version's code and name are the round's.
planning_round = Table(
    "planning_round",
    metadata,
    Column("version_id", SmallInteger, ForeignKey("version.version_id"), primary_key=True),
    Column("fiscal_year", Integer, nullable=False),
    Column("deadline", Date),
    Column("status", Text, nullable=False, server_default="setup"),  # ADR 0008
    Column("copied_from", SmallInteger, ForeignKey("planning_round.version_id")),
)

round_module = Table(
    "round_module",
    metadata,
    Column("version_id", SmallInteger, ForeignKey("planning_round.version_id"), nullable=False),
    Column("module", Text, nullable=False),
    # Entry form of accounts without a setting of their own or of a group above them.
    Column("default_form", Text, nullable=False, server_default="monthly"),
    PrimaryKeyConstraint("version_id", "module"),
)


def _module_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["version_id", "module"], ["round_module.version_id", "round_module.module"]
    )


# The planning units a round has seen: enabled or disabled by the controller. A cost center that
# arrived after the round was created has no row, and isn't planned until it's enabled.
round_member = Table(
    "round_member",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    Column("module", Text, nullable=False),
    Column("member_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("enabled", Boolean, nullable=False),
    PrimaryKeyConstraint("version_id", "module", "member_id"),
    _module_fk(),
)

# Installation-wide seasonality profiles (D-28), one value per fiscal month, as entered.
seasonality_profile = Table(
    "seasonality_profile",
    metadata,
    Column("profile_id", SmallInteger, primary_key=True),
    Column("code", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("entered_as", Text, nullable=False),
    Column("values", ARRAY(Numeric), nullable=False),
)

# Entry form of an account group or a single account; the nearest setting above an account wins.
entry_form = Table(
    "entry_form",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    Column("module", Text, nullable=False),
    Column("account_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("form", Text, nullable=False),
    Column("profile_id", SmallInteger, ForeignKey("seasonality_profile.profile_id")),
    # Spread reference other than prior-year actuals.
    Column("reference_version_id", SmallInteger, ForeignKey("version.version_id")),
    PrimaryKeyConstraint("version_id", "module", "account_id"),
    _module_fk(),
)

# Comparison columns shown next to the plan, in order.
comparison = Table(
    "comparison",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    Column("module", Text, nullable=False),
    Column("position", SmallInteger, nullable=False),
    Column("kind", Text, nullable=False),  # prior_year_actuals / actuals / version
    Column("compare_version_id", SmallInteger, ForeignKey("version.version_id")),
    PrimaryKeyConstraint("version_id", "module", "position"),
    _module_fk(),
)

# Accounts a planner added to a cost center's sheet.
sheet_account = Table(
    "sheet_account",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    Column("module", Text, nullable=False),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("account_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("added_by", Text),
    Column("added_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("version_id", "module", "cost_center_id", "account_id"),
    _module_fk(),
)

# Accounts excluded from a module in a round; the lock trigger refuses plan values on them.
hidden_account = Table(
    "hidden_account",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    Column("module", Text, nullable=False),
    Column("account_id", Integer, ForeignKey("member.member_id"), nullable=False),
    PrimaryKeyConstraint("version_id", "module", "account_id"),
    _module_fk(),
)

# --- Workflow (ADR 0008) --------------------------------------------------------------------------

# Status per round x cost center. No row means not_started. Read by the lock trigger.
workflow_status = Table(
    "workflow_status",
    metadata,
    Column("version_id", SmallInteger, ForeignKey("planning_round.version_id"), nullable=False),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("status", Text, nullable=False),
    Column("changed_at", DateTime(timezone=True)),
    Column("changed_by", Text),
    PrimaryKeyConstraint("version_id", "cost_center_id"),
)

workflow_event = Table(
    "workflow_event",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("version_id", SmallInteger, ForeignKey("planning_round.version_id"), nullable=False),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("actor", Text),
    Column("from_status", Text, nullable=False),
    Column("to_status", Text, nullable=False),
    Column("comment", Text),
)

# Approvers of the cost centers below a node of the cost center hierarchy.
approver_assignment = Table(
    "approver_assignment",
    metadata,
    Column("member_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("user_name", Text, nullable=False),
    PrimaryKeyConstraint("member_id", "user_name"),
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
