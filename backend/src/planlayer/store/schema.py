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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

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
    # ADR 0012: synced from a source, or added by hand. ``name`` is effective (a local override
    # wins); ``source_name`` is what the source delivered.
    Column("origin", Text, nullable=False, server_default="source"),
    Column("source_name", Text),
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
    Column("parent_id", Integer, ForeignKey("member.member_id")),  # effective
    Column("sort_order", Integer, nullable=False, server_default="0"),
    Column("origin", Text, nullable=False, server_default="source"),
    Column("source_parent_id", Integer, ForeignKey("member.member_id")),
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
    Column("user_id", Integer, ForeignKey("app_user.user_id"), nullable=False),
    PrimaryKeyConstraint("member_id", "user_id"),
)

# --- Users and access (ADR 0009) ------------------------------------------------------------------

app_user = Table(
    "app_user",
    metadata,
    Column("user_id", Integer, primary_key=True),
    Column("login", Text, nullable=False, unique=True),  # lowercase
    Column("email", Text),
    Column("display_name", Text, nullable=False),
    Column("is_admin", Boolean, nullable=False, server_default="false"),
    Column("is_controller", Boolean, nullable=False, server_default="false"),
    Column("is_viewer", Boolean, nullable=False, server_default="false"),
    Column("is_system", Boolean, nullable=False, server_default="false"),
    Column("disabled", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Login sessions; only a hash of the token is stored.
user_session = Table(
    "user_session",
    metadata,
    Column("token_hash", Text, primary_key=True),
    Column("user_id", Integer, ForeignKey("app_user.user_id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)

# The controller's choice of planner for a cost center, replacing the matched owner. A NULL
# user means the cost center has no owner.
owner_override = Table(
    "owner_override",
    metadata,
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("app_user.user_id")),
    Column("set_by", Text),
    Column("set_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Deputies: planning rights on a cost center, or on every cost center below a node.
planner_grant = Table(
    "planner_grant",
    metadata,
    Column("member_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("user_id", Integer, ForeignKey("app_user.user_id"), nullable=False),
    Column("granted_by", Text),
    Column("granted_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("member_id", "user_id"),
)

# Views (migration 0004), declared for queries only.
cost_center_owner = Table(
    "cost_center_owner",
    metadata,
    Column("cost_center_id", Integer),
    Column("user_id", Integer),  # effective planner-owner
    Column("matched_user_id", Integer),  # matched from the synced owner
    Column("overridden", Boolean),
)
cost_center_access = Table(
    "cost_center_access",
    metadata,
    Column("user_id", Integer),
    Column("cost_center_id", Integer),
    Column("can_write", Boolean),
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
    Column("summary", JSONB),
)

# --- Master data (ADR 0012) -----------------------------------------------------------------------

NO_ENTITY = "-"  # the entity of cost centers that name none

# The admin's query per dataset and the source column of each field.
dataset_mapping = Table(
    "dataset_mapping",
    metadata,
    Column("dataset", Text, primary_key=True),
    Column("query", Text, nullable=False),
    Column("fields", JSONB, nullable=False),
    Column("hierarchy", JSONB),  # {"parent": column} or {"levels": [{"code": c, "name": c}]}
    Column("updated_by", Text),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# The controller's name for a synced member; survives syncs.
member_override = Table(
    "member_override",
    metadata,
    Column("member_id", Integer, ForeignKey("member.member_id"), primary_key=True),
    Column("name", Text, nullable=False),
    Column("set_by", Text),
    Column("set_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# The controller's parent for a synced member in a hierarchy; survives syncs.
edge_override = Table(
    "edge_override",
    metadata,
    Column("hier_id", SmallInteger, ForeignKey("hierarchy.hier_id"), nullable=False),
    Column("member_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("parent_id", Integer, ForeignKey("member.member_id")),
    Column("set_by", Text),
    Column("set_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("hier_id", "member_id"),
)

# What a manual member looked like when the source took it over.
member_shadow = Table(
    "member_shadow",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("member_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("code", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("attributes", JSONB, nullable=False),
    Column("parents", JSONB, nullable=False),
    Column("confirmed_by", Text),
    Column("confirmed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# One row per publish run (ADR 0011); finished_at NULL means the run failed.
publish_run = Table(
    "publish_run",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("version_id", SmallInteger, ForeignKey("version.version_id"), nullable=False),
    Column("scope", Text, nullable=False),  # approved / all
    Column("actor", Text),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    Column("rows", Integer),
    Column("added", Integer),
    Column("changed", Integer),
    Column("removed", Integer),
    Column("fingerprint", Text),
)
