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
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
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

from planlayer.crypto import SealedAmount, SealedText

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
WORK_CENTER, ACTIVITY_TYPE = 4, 5  # capacity planning (ADR 0013)
STAFF_TYPE, STAFF_CATEGORY = 6, 7  # workforce planning (ADR 0020)
H_COST_CENTER, H_ACCOUNT = 1, 2
BASE_LAYER = 1  # T-23
AMOUNT, HOURS = 1, 2  # measures, T-23
FTE, HEADCOUNT = 3, 4  # workforce planning (ADR 0020)

AMOUNT_TYPE = Numeric(18, 2)  # T-12

NUMBER_SEPARATORS = "{} IN ('comma_dot', 'dot_comma', 'apostrophe_dot', 'space_comma')"
NUMBER_SCALE = "{} IN ('full', 'k', 'M')"

# One row per installation: settings that hold for the whole installation.
installation = Table(
    "installation",
    metadata,
    Column("id", SmallInteger, primary_key=True, server_default="1"),
    # First month of the fiscal year (T-18).
    Column("fiscal_year_start_month", SmallInteger, nullable=False, server_default="1"),
    # The sign convention of published amounts (ADR 0019): ledger or result.
    Column("published_signs", Text, nullable=False, server_default="ledger"),
    # Whether the sync reads HR's salaries from the dataset ``staff`` (ADR 0025); off, HR
    # uploads them on the personnel costs page.
    Column("pay_from_source", Boolean, nullable=False, server_default="false"),
    # How amounts are shown and typed, unless a user overrides it (planlayer.number_format).
    Column("number_separators", Text, nullable=False, server_default="comma_dot"),
    Column("number_decimals", Boolean, nullable=False, server_default="false"),
    Column("number_scale", Text, nullable=False, server_default="full"),
    Column("number_units", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("id = 1", name="single_row"),
    CheckConstraint("fiscal_year_start_month BETWEEN 1 AND 12", name="fiscal_month"),
    CheckConstraint(NUMBER_SEPARATORS.format("number_separators"), name="number_separators"),
    CheckConstraint(NUMBER_SCALE.format("number_scale"), name="number_scale"),
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
    # Work centers: their cost center. Activity types: the accounts they credit (ADR 0013).
    Column("cost_center_code", Text),
    Column("account_code", Text),
    Column("variable_account_code", Text),
    # ADR 0012: synced from a source, or added by hand. ``name`` is effective (a local override
    # wins); ``source_name`` is what the source delivered.
    Column("origin", Text, nullable=False, server_default="source"),
    Column("source_name", Text),
    # ADR 0014: since when the source no longer delivers a synced member; NULL while it does.
    Column("missing_since", DateTime(timezone=True)),
    Column("external", Boolean),  # staff types (ADR 0020): external staff, e.g. freelancers
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
MODULE_CAPACITY = "capacity"  # its enabled members are the productive cost centers (ADR 0013)
MODULE_WORKFORCE = "workforce"  # FTE, headcount or positions (ADR 0020)
MODULE_PERSONNEL = "personnel_costs"  # costs from positions (ADR 0022)

# A plan version for one fiscal year, one-to-one; the version's code and name are the round's.
planning_round = Table(
    "planning_round",
    metadata,
    Column("version_id", SmallInteger, ForeignKey("version.version_id"), primary_key=True),
    Column("fiscal_year", Integer, nullable=False),
    Column("deadline", Date),
    Column("status", Text, nullable=False, server_default="setup"),  # ADR 0008
    Column("copied_from", SmallInteger, ForeignKey("planning_round.version_id")),
    # How the round was created (ADR 0014): who, when, and what it carried over.
    Column("created_by", Text),
    Column("created_at", DateTime(timezone=True)),
    Column("carried", JSONB),
    # How the round is planned (ADR 0015): by month or by quarter; values are months either way.
    Column("grain", Text, nullable=False, server_default="month"),
    # A forecast's last closed month: months up to it show actuals and take no plan values.
    Column("actuals_through", Date),
)

round_module = Table(
    "round_module",
    metadata,
    Column("version_id", SmallInteger, ForeignKey("planning_round.version_id"), nullable=False),
    Column("module", Text, nullable=False),
    # Entry form of accounts without a setting of their own or of a group above them.
    Column("default_form", Text, nullable=False, server_default="monthly"),
    # Workforce planning's mode (ADR 0020): fte / headcount / both / positions.
    Column("mode", Text),
    # Capacity planning (ADR 0027): the hours a full-time position works in the year.
    Column("fte_hours", Numeric(7, 2)),
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
    Column("kind", Text, nullable=False),  # prior_year_actuals / actuals / version / projection
    Column("compare_version_id", SmallInteger, ForeignKey("version.version_id")),
    # A budget's projection: the last month of the prior year counted as booked.
    Column("through", Date),
    Column("note", Text),  # the controller's note to planners, e.g. "January to June only"
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

# How one cost center's line spreads its year total, chosen on the sheet instead of the entry
# form's basis; "reference" is prior-year actuals. Scoped and locked like plan values.
line_spread = Table(
    "line_spread",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    Column("module", Text, nullable=False),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("account_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("basis", Text, nullable=False),
    Column("profile_id", SmallInteger, ForeignKey("seasonality_profile.profile_id")),
    Column("changed_by", Text),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("version_id", "module", "cost_center_id", "account_id"),
    _module_fk(),
    CheckConstraint("basis IN ('even', 'reference', 'profile')", name="basis"),
    CheckConstraint("(basis = 'profile') = (profile_id IS NOT NULL)", name="profile"),
)

# Accounts a module doesn't plan in a round (ADR 0014): shown read only where they have
# comparison values; the lock trigger refuses plan values on them.
not_planned_account = Table(
    "not_planned_account",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    Column("module", Text, nullable=False),
    Column("account_id", Integer, ForeignKey("member.member_id"), nullable=False),
    PrimaryKeyConstraint("version_id", "module", "account_id"),
    _module_fk(),
)

# Named lists of accounts and account groups per round (ADR 0014), assigned to cost center nodes
# or cost centers; the nearest assignment above a cost center decides what its sheet plans.
account_set = Table(
    "account_set",
    metadata,
    Column("set_id", Integer, Identity(), primary_key=True),
    Column("version_id", SmallInteger, nullable=False),
    Column("module", Text, nullable=False),
    Column("name", Text, nullable=False),
    UniqueConstraint("version_id", "module", "name"),
    _module_fk(),
)
account_set_account = Table(
    "account_set_account",
    metadata,
    Column("set_id", Integer, ForeignKey("account_set.set_id", ondelete="CASCADE"), nullable=False),
    Column("account_id", Integer, ForeignKey("member.member_id"), nullable=False),
    PrimaryKeyConstraint("set_id", "account_id"),
)
account_set_assignment = Table(
    "account_set_assignment",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    Column("module", Text, nullable=False),
    Column("member_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("set_id", Integer, ForeignKey("account_set.set_id", ondelete="CASCADE"), nullable=False),
    PrimaryKeyConstraint("version_id", "module", "member_id"),
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
    Column("is_hr", Boolean, nullable=False, server_default="false"),  # reads pay (ADR 0022)
    Column("is_system", Boolean, nullable=False, server_default="false"),
    Column("disabled", Boolean, nullable=False, server_default="false"),
    # The user's own number format; NULL takes the installation's (planlayer.number_format).
    Column("number_separators", Text),
    Column("number_decimals", Boolean),
    Column("number_scale", Text),
    Column("number_units", Boolean),
    # A local account's password (scrypt, planlayer.login.passwords); NULL: no local password.
    Column("password_hash", Text),
    # The provider's key of the user once they logged in through it (ADR 0030):
    # ``oidc:<issuer> <sub>`` or ``ldap:<dn>``.
    Column("identity", Text, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(NUMBER_SEPARATORS.format("number_separators"), name="number_separators"),
    CheckConstraint(NUMBER_SCALE.format("number_scale"), name="number_scale"),
)

# The login provider, set up by the admin on the Settings page (ADR 0030). One row. No secret is
# kept here: OpenID Connect runs as a public client, LDAP binds as the user logging in.
login_setting = Table(
    "login_setting",
    metadata,
    Column("id", SmallInteger, primary_key=True, server_default="1"),
    Column("method", Text, nullable=False, server_default="none"),  # none, oidc, ldap
    Column("oidc_label", Text, nullable=False, server_default=""),  # the button: "Microsoft"
    Column("oidc_issuer", Text, nullable=False, server_default=""),
    Column("oidc_client_id", Text, nullable=False, server_default=""),
    Column("oidc_scopes", Text, nullable=False, server_default="openid profile email"),
    Column("oidc_login_claim", Text, nullable=False, server_default="preferred_username"),
    Column("oidc_email_claim", Text, nullable=False, server_default="email"),
    Column("oidc_name_claim", Text, nullable=False, server_default="name"),
    Column("oidc_groups_claim", Text, nullable=False, server_default="groups"),
    Column("oidc_hosted_domain", Text, nullable=False, server_default=""),  # Google's hd claim
    # Sealed (ADR 0029); PLANLAYER_OIDC_CLIENT_SECRET, when set, is used instead.
    Column("oidc_client_secret", SealedText("login_setting.oidc_client_secret")),
    Column("ldap_url", Text, nullable=False, server_default=""),
    Column("ldap_bind_dn", Text, nullable=False, server_default=""),  # with {login}
    Column("ldap_base_dn", Text, nullable=False, server_default=""),
    Column("ldap_login_attribute", Text, nullable=False, server_default="uid"),
    Column("ldap_email_attribute", Text, nullable=False, server_default="mail"),
    Column("ldap_name_attribute", Text, nullable=False, server_default="cn"),
    Column("ca_pem", Text, nullable=False, server_default=""),  # the provider's CA, if private
    Column("allowed_group", Text, nullable=False, server_default=""),
    Column("admin_group", Text, nullable=False, server_default=""),
    Column("controller_group", Text, nullable=False, server_default=""),
    Column("viewer_group", Text, nullable=False, server_default=""),
    Column("hr_group", Text, nullable=False, server_default=""),
    # The first run's setup code (hash), while no admin can log in.
    Column("setup_code_hash", Text),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("id = 1", name="single_row"),
    CheckConstraint("method IN ('none', 'oidc', 'ldap')", name="login_method"),
)

# OpenID Connect logins between the redirect to the provider and its callback, ten minutes at
# most. A test login carries the settings tried and keeps its result for the admin.
oidc_login = Table(
    "oidc_login",
    metadata,
    Column("state", Text, primary_key=True),
    Column("nonce", Text, nullable=False),
    Column("verifier", Text, nullable=False),
    Column("redirect_uri", Text, nullable=False),
    Column("test_by", Integer, ForeignKey("app_user.user_id"), index=True),
    Column("settings", JSONB),
    Column("result", JSONB),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Every login attempt (ADR 0030); also read to refuse logins after too many failures.
login_event = Table(
    "login_event",
    metadata,
    Column("event_id", BigInteger, Identity(always=True), primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("login", Text, nullable=False),  # as tried
    Column("method", Text, nullable=False),  # demo, password, oidc, setup
    Column("outcome", Text, nullable=False),  # success, failed, refused, blocked, error
    Column("address", Text),
    Column("user_agent", Text),
    Column("detail", Text),
    Column("user_id", Integer, ForeignKey("app_user.user_id"), index=True),
    CheckConstraint("method IN ('demo', 'password', 'oidc', 'setup')", name="login_event_method"),
    CheckConstraint(
        "outcome IN ('success', 'failed', 'refused', 'blocked', 'error')",
        name="login_event_outcome",
    ),
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

# --- Capacity planning (ADR 0013) -----------------------------------------------------------------

RATE_TYPE = Numeric(18, 4)

# The source's rate per work center and activity type; replaced by each sync.
work_center_rate = Table(
    "work_center_rate",
    metadata,
    Column("work_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("activity_type_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("rate", RATE_TYPE, nullable=False),
    PrimaryKeyConstraint("work_center_id", "activity_type_id"),
)

# A work center x activity type line of a productive cost center in a round: its plan rate,
# whether it is active, and how a year total of hours spreads. Scoped and locked like hours.
capacity_line = Table(
    "capacity_line",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    Column("module", Text, nullable=False, server_default=MODULE_CAPACITY),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("work_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("activity_type_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("rate", RATE_TYPE, nullable=False),
    Column("variable_rate", RATE_TYPE),  # credited to the variable account; no UI yet
    Column("active", Boolean, nullable=False, server_default="true"),
    Column("basis", Text),  # NULL: like prior-year actual hours
    Column("profile_id", SmallInteger, ForeignKey("seasonality_profile.profile_id")),
    # Its hours come from the cost center's positions (ADR 0027); kept by every recompute.
    Column("staffed", Boolean, nullable=False, server_default="false"),
    Column("changed_by", Text),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("version_id", "work_center_id", "activity_type_id"),
    _module_fk(),
)


def _line_fk(prefix: str) -> ForeignKeyConstraint:
    """A capacity line of the round; removing the line clears the reference."""
    return ForeignKeyConstraint(
        ["version_id", f"{prefix}work_center_id", f"{prefix}activity_type_id"],
        [
            "capacity_line.version_id",
            "capacity_line.work_center_id",
            "capacity_line.activity_type_id",
        ],
        ondelete=f"SET NULL ({prefix}work_center_id, {prefix}activity_type_id)",
    )


# Where a productive cost center's hours come from in a round (ADR 0027): typed per line (work
# centers), from its positions (workforce), or both. No row: typed only. With positions, the
# line their hours go to and their productive share unless a position has its own.
capacity_unit = Table(
    "capacity_unit",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    Column("module", Text, nullable=False, server_default=MODULE_CAPACITY),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("typed_hours", Boolean, nullable=False, server_default="true"),
    Column("staff_hours", Boolean, nullable=False, server_default="false"),
    Column("default_work_center_id", Integer),
    Column("default_activity_type_id", Integer),
    Column("default_percent", Numeric(5, 2)),
    Column("changed_by", Text),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("version_id", "cost_center_id"),
    _module_fk(),
    _line_fk("default_"),
)

# A position's productive share and its own line, where they differ from the cost center's;
# a position without a contract % has its year's hours typed instead.
capacity_position = Table(
    "capacity_position",
    metadata,
    Column(
        "position_id",
        Integer,
        ForeignKey("workforce_position.position_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("version_id", SmallInteger, nullable=False),
    Column("module", Text, nullable=False, server_default=MODULE_CAPACITY),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("productive_percent", Numeric(5, 2)),
    Column("hours", Numeric(10, 2)),  # a year's hours, for a position without a contract %
    Column("work_center_id", Integer),
    Column("activity_type_id", Integer),
    Column("changed_by", Text),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    _module_fk(),
    _line_fk(""),
)

# Changes to capacity_unit and capacity_position, written by a trigger, as old and new row.
capacity_staff_log = Table(
    "capacity_staff_log",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("actor", Text),
    Column("batch_id", Text),
    Column("op", Text, nullable=False),
    Column("target", Text, nullable=False),  # the table changed
    Column("version_id", SmallInteger, nullable=False),
    Column("cost_center_id", Integer, nullable=False),
    Column("old", JSONB),
    Column("new", JSONB),
)

# Planned and actual hours (ADR 0001 shape): one column per dimension.
fact_capacity = Table(
    "fact_capacity",
    metadata,
    Column("version_id", SmallInteger, ForeignKey("version.version_id"), nullable=False),
    Column("layer_id", SmallInteger, nullable=False),
    Column("measure_id", SmallInteger, nullable=False),
    Column("period", Date, nullable=False),
    Column("dim_entity", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("dim_cost_center", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("dim_work_center", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("dim_activity_type", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("amount", AMOUNT_TYPE, nullable=False),
    Column("row_version", BigInteger, nullable=False, server_default="1"),
    PrimaryKeyConstraint(
        "version_id", "layer_id", "measure_id", "period", "dim_entity", "dim_cost_center",
        "dim_work_center", "dim_activity_type",
    ),
)  # fmt: skip

capacity_change_log = Table(
    "capacity_change_log",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("actor", Text),
    Column("batch_id", Text),
    Column("op", Text, nullable=False),
    Column("version_id", SmallInteger, nullable=False),
    Column("period", Date, nullable=False),
    Column("dim_cost_center", Integer, nullable=False),
    Column("dim_work_center", Integer, nullable=False),
    Column("dim_activity_type", Integer, nullable=False),
    Column("old_amount", AMOUNT_TYPE),
    Column("new_amount", AMOUNT_TYPE),
)

# Changes to capacity lines, written by a trigger (migration 0009): a line's state before and
# after, under the request's change batch, for audit and undo. No old state: the line was added.
capacity_line_log = Table(
    "capacity_line_log",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("actor", Text),
    Column("batch_id", Text),
    Column("op", Text, nullable=False),
    Column("version_id", SmallInteger, nullable=False),
    Column("cost_center_id", Integer, nullable=False),
    Column("work_center_id", Integer, nullable=False),
    Column("activity_type_id", Integer, nullable=False),
    *(
        Column(f"{when}_{name}", kind)
        for when in ("old", "new")
        for name, kind in (
            ("rate", RATE_TYPE),
            ("variable_rate", RATE_TYPE),
            ("active", Boolean),
            ("basis", Text),
            ("profile_id", SmallInteger),
        )
    ),
)

# Capacity planners: they write the capacity of a cost center, or of every one below a node.
capacity_grant = Table(
    "capacity_grant",
    metadata,
    Column("member_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("user_id", Integer, ForeignKey("app_user.user_id"), nullable=False),
    Column("granted_by", Text),
    Column("granted_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("member_id", "user_id"),
)

# Views (migration 0008), declared for queries only.
capacity_access = Table(
    "capacity_access",
    metadata,
    Column("user_id", Integer),
    Column("cost_center_id", Integer),
    Column("can_write", Boolean),
)
credit_account = Table("credit_account", metadata, Column("account_id", Integer))


# --- Line notes and item lists (ADR 0017) --------------------------------------------------------


def _line() -> list[Column]:  # type: ignore[type-arg]
    """A line: a cost center with an account (module ``cost_center``), or with a work center and
    an activity type (module ``capacity``)."""
    return [
        Column("module", Text, nullable=False),
        Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
        Column("account_id", Integer, ForeignKey("member.member_id")),
        Column("work_center_id", Integer, ForeignKey("member.member_id")),
        Column("activity_type_id", Integer, ForeignKey("member.member_id")),
    ]


# A thread of notes on a line of a sheet, or on an investment (module ``capex``, migration
# 0028): an investment's thread keeps no cost center, as the investment moves between them.
note_thread = Table(
    "note_thread",
    metadata,
    Column("thread_id", BigInteger, Identity(), primary_key=True),
    Column("module", Text, nullable=False),
    Column("cost_center_id", Integer, ForeignKey("member.member_id")),
    Column("account_id", Integer, ForeignKey("member.member_id")),
    Column("work_center_id", Integer, ForeignKey("member.member_id")),
    Column("activity_type_id", Integer, ForeignKey("member.member_id")),
    Column("capex_item_id", Integer, ForeignKey("capex_item.item_id", ondelete="CASCADE")),
    # The round it was last written in: it shows there, and while open in every later round.
    Column("round_id", SmallInteger, ForeignKey("planning_round.version_id"), nullable=False),
    Column("assignee_id", Integer, ForeignKey("app_user.user_id")),  # a task's assignee
    Column("resolved_at", DateTime(timezone=True)),
    Column("resolved_by", Integer, ForeignKey("app_user.user_id")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("created_by", Integer, ForeignKey("app_user.user_id"), nullable=False),
)

note = Table(
    "note",
    metadata,
    Column("note_id", BigInteger, Identity(), primary_key=True),
    Column("thread_id", BigInteger, ForeignKey("note_thread.thread_id"), nullable=False),
    Column("author_id", Integer, ForeignKey("app_user.user_id"), nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("edited_at", DateTime(timezone=True)),
)

note_mention = Table(
    "note_mention",
    metadata,
    Column("note_id", BigInteger, ForeignKey("note.note_id"), nullable=False),
    Column("user_id", Integer, ForeignKey("app_user.user_id"), nullable=False),
    PrimaryKeyConstraint("note_id", "user_id"),
)

# A line's notepad: rows of a text and an amount.
note_grid = Table(
    "note_grid",
    metadata,
    Column("grid_id", BigInteger, Identity(), primary_key=True),
    *_line(),
    Column("round_id", SmallInteger, ForeignKey("planning_round.version_id"), nullable=False),
    Column("rows", JSONB, nullable=False),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("changed_by", Integer, ForeignKey("app_user.user_id"), nullable=False),
)

item_list = Table(
    "item_list",
    metadata,
    Column("list_id", BigInteger, Identity(), primary_key=True),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("account_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("owner_id", Integer, ForeignKey("app_user.user_id"), nullable=False),
    Column("visibility", Text, nullable=False, server_default="private"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("changed_by", Integer, ForeignKey("app_user.user_id"), nullable=False),
)

item = Table(
    "item",
    metadata,
    Column("item_id", BigInteger, Identity(), primary_key=True),
    Column("list_id", BigInteger, ForeignKey("item_list.list_id"), nullable=False),
    Column("position", Integer, nullable=False),
    # An item, or a category heading: a name only, grouping the items below it up to the next.
    Column("kind", Text, nullable=False, server_default="item"),
    Column("name", Text, nullable=False),
    Column("supplier", Text),
    Column("quantity", Numeric(18, 4), nullable=False, server_default="1"),
    Column("unit_price", Numeric(18, 2)),
    # quantity x unit price, rounded to the cent, computed by the database; NULL for a heading.
    Column("amount", Numeric(18, 2), Computed("round(quantity * unit_price, 2)", persisted=True)),
    Column("comment", Text),
    CheckConstraint("kind IN ('item', 'heading')", name="kind"),
    CheckConstraint(
        "(kind = 'item' AND unit_price IS NOT NULL) OR (kind = 'heading' AND unit_price IS NULL "
        "AND supplier IS NULL AND comment IS NULL AND quantity = 1)",
        name="heading",
    ),
)

annotation_log = Table(
    "annotation_log",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("actor", Text),
    Column("batch_id", Text),
    Column("table_name", Text, nullable=False),
    Column("op", Text, nullable=False),
    Column("old", JSONB),
    Column("new", JSONB),
)


# --- Cost forwarding (ADR 0018) ------------------------------------------------------------------

# A forwarding of a round: what a source cost center passes on, by which method, onto which
# accounts. Written by controllers only, while the round is in setup or open (guard trigger).
forwarding = Table(
    "forwarding",
    metadata,
    Column("forwarding_id", Integer, primary_key=True),
    Column("version_id", SmallInteger, ForeignKey("planning_round.version_id"), nullable=False),
    Column("code", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("method", Text, nullable=False),  # full / fixed / rate
    Column("source_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("shares", Text),  # full and fixed: percent / portion
    Column("amount", AMOUNT_TYPE),  # fixed: the year's amount
    Column("rate", RATE_TYPE),  # rate: per unit and year
    Column("unit", Text),  # rate: what a quantity counts
    Column("charge_account_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("credit_account_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("changed_by", Text),
    Column("changed_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("version_id", "code"),
)

# A receiver's share (percent or portion) or quantity of the forwarding's unit.
forwarding_receiver = Table(
    "forwarding_receiver",
    metadata,
    Column(
        "forwarding_id",
        Integer,
        ForeignKey("forwarding.forwarding_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("quantity", RATE_TYPE, nullable=False),
    PrimaryKeyConstraint("forwarding_id", "cost_center_id"),
)

# View (migration 0014): the accounts each round's forwardings charge and credit.
forwarding_account = Table(
    "forwarding_account",
    metadata,
    Column("version_id", SmallInteger),
    Column("account_id", Integer),
    Column("side", Text),  # charge / credit
    Column("forwarding_id", Integer),
)


# --- Workforce planning (ADR 0020) ---------------------------------------------------------------

# FTE and headcount per line and month, as levels at month end: planned values, actuals synced
# from HR, and in a positions round the positions' roll-up.
fact_workforce = Table(
    "fact_workforce",
    metadata,
    Column("version_id", SmallInteger, ForeignKey("version.version_id"), nullable=False),
    Column("layer_id", SmallInteger, nullable=False),
    Column("measure_id", SmallInteger, nullable=False),
    Column("period", Date, nullable=False),
    Column("dim_entity", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("dim_cost_center", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("dim_staff_type", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("dim_staff_category", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("amount", AMOUNT_TYPE, nullable=False),
    Column("row_version", BigInteger, nullable=False, server_default="1"),
    PrimaryKeyConstraint(
        "version_id", "layer_id", "measure_id", "period", "dim_entity", "dim_cost_center",
        "dim_staff_type", "dim_staff_category",
    ),
)  # fmt: skip

workforce_change_log = Table(
    "workforce_change_log",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("actor", Text),
    Column("batch_id", Text),
    Column("op", Text, nullable=False),
    Column("version_id", SmallInteger, nullable=False),
    Column("measure_id", SmallInteger, nullable=False),
    Column("period", Date, nullable=False),
    Column("dim_cost_center", Integer, nullable=False),
    Column("dim_staff_type", Integer, nullable=False),
    Column("dim_staff_category", Integer, nullable=False),
    Column("old_amount", AMOUNT_TYPE),
    Column("new_amount", AMOUNT_TYPE),
)

# A staff type x category line a planner added to a cost center's workforce sheet.
workforce_line = Table(
    "workforce_line",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    Column("module", Text, nullable=False, server_default=MODULE_WORKFORCE),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("staff_type_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("staff_category_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("changed_by", Text),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("version_id", "cost_center_id", "staff_type_id", "staff_category_id"),
    _module_fk(),
)

# A position of a cost center in a round with mode ``positions``; logged by a trigger.
workforce_position = Table(
    "workforce_position",
    metadata,
    Column("position_id", Integer, Identity(), primary_key=True),
    Column("version_id", SmallInteger, nullable=False),
    Column("module", Text, nullable=False, server_default=MODULE_WORKFORCE),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    # HR's personnel number, or Planlayer's (N00001, ...) for a position added here.
    Column("code", Text, nullable=False),
    # hr: taken from HR's staff list, keeping its number, name, entry date and flags; manual.
    Column("origin", Text, nullable=False, server_default="manual"),
    Column("title", Text, nullable=False),
    Column("person_name", Text),
    Column("staff_type_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("staff_category_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("entry_date", Date),  # None: before the fiscal year
    Column("leave_date", Date),  # None: stays
    Column("contract_percent", Numeric(5, 2)),
    Column("is_plan", Boolean, nullable=False, server_default="false"),
    Column("is_replacement", Boolean, nullable=False, server_default="false"),
    Column("description", Text),
    Column("changed_by", Text),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # Transfers (ADR 0021): the cost center a position came from, and for one that ended on this
    # cost center during the year, the cost center it went to. A number is unique per round among
    # the positions that haven't ended in a transfer.
    Column("transferred_from_id", Integer, ForeignKey("member.member_id")),
    Column("transferred_to_id", Integer, ForeignKey("member.member_id")),
    _module_fk(),
)

# A position's transfer to another cost center (ADR 0021): proposed by the sending side, decided
# by the receiving side. ``starts`` is the first day on the receiving cost center (during_year).
workforce_transfer = Table(
    "workforce_transfer",
    metadata,
    Column("transfer_id", Integer, Identity(), primary_key=True),
    Column("version_id", SmallInteger, nullable=False),
    Column("module", Text, nullable=False, server_default=MODULE_WORKFORCE),
    Column(
        "position_id",
        Integer,
        ForeignKey("workforce_position.position_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("from_cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("to_cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("kind", Text, nullable=False),  # full / during_year
    Column("starts", Date),
    Column("status", Text, nullable=False, server_default="proposed"),
    Column("note", Text),
    Column("proposed_by", Integer, ForeignKey("app_user.user_id"), nullable=False),
    Column("proposed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("decided_by", Integer, ForeignKey("app_user.user_id")),
    Column("decided_at", DateTime(timezone=True)),
    Column("reason", Text),
    Column(
        "new_position_id",
        Integer,
        ForeignKey("workforce_position.position_id", ondelete="SET NULL"),
    ),
    _module_fk(),
)

workforce_position_log = Table(
    "workforce_position_log",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("actor", Text),
    Column("batch_id", Text),
    Column("op", Text, nullable=False),
    Column("version_id", SmallInteger, nullable=False),
    Column("cost_center_id", Integer, nullable=False),
    Column("position_id", Integer, nullable=False),
    Column("old", JSONB),
    Column("new", JSONB),
)

# HR's list of people (dataset ``staff``), replaced by each sync; holds names, so row-level
# security shows it only to those who read the cost center.
hr_staff = Table(
    "hr_staff",
    metadata,
    Column("code", Text, primary_key=True),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("title", Text, nullable=False),
    Column("person_name", Text),
    Column("staff_type_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("staff_category_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("entry_date", Date),
    Column("leave_date", Date),
    Column("contract_percent", Numeric(5, 2)),
)


# --- Personnel costs (ADR 0022) ------------------------------------------------------------------
# Pay is guarded by row-level security through the view ``pay_access``.


def _personnel() -> list[Column | ForeignKeyConstraint]:  # type: ignore[type-arg]
    return [
        Column("module", Text, nullable=False, server_default=MODULE_PERSONNEL),
        ForeignKeyConstraint(
            ["version_id", "module"], ["round_module.version_id", "round_module.module"]
        ),
    ]


# HR's monthly salaries from the dataset ``staff``, replaced by each sync.
hr_pay = Table(
    "hr_pay",
    metadata,
    Column("code", Text, primary_key=True),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("monthly_salary", SealedAmount("hr_pay.monthly_salary"), nullable=False),  # ADR 0029
)

pay_setting = Table(
    "pay_setting",
    metadata,
    Column("version_id", SmallInteger, primary_key=True),
    *_personnel(),
    Column("min_group", SmallInteger, nullable=False, server_default="3"),
    # HR's review before approval (ADR 0024); off unless HR switches it on for the round.
    Column("review_required", Boolean, nullable=False, server_default="false"),
    Column("bonus_account_id", Integer, ForeignKey("member.member_id")),
    Column("changed_by", Text),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Per staff type: the salary account, and whether its positions' amounts are typed.
pay_staff_type = Table(
    "pay_staff_type",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    *_personnel(),
    Column("staff_type_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("account_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("typed", Boolean, nullable=False),
    PrimaryKeyConstraint("version_id", "staff_type_id"),
)

# A rule of the round: extra (salaries per year), raise (percent from a month), award (steps by
# years of service), contribution (percent up to a monthly ceiling).
pay_rule = Table(
    "pay_rule",
    metadata,
    Column("rule_id", Integer, Identity(), primary_key=True),
    Column("version_id", SmallInteger, nullable=False),
    *_personnel(),
    Column("kind", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("account_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("entity_id", Integer, ForeignKey("member.member_id")),
    Column("salaries", Numeric(8, 4)),
    Column("percent", Numeric(7, 4)),
    Column("ceiling", AMOUNT_TYPE),
    Column("starts", Date),
    Column("changed_by", Text),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

pay_rule_step = Table(
    "pay_rule_step",
    metadata,
    Column("rule_id", Integer, ForeignKey("pay_rule.rule_id", ondelete="CASCADE"), nullable=False),
    Column("years", SmallInteger, nullable=False),
    Column("salaries", Numeric(8, 4)),
    Column("amount", AMOUNT_TYPE),
    PrimaryKeyConstraint("rule_id", "years"),
)

position_pay = Table(
    "position_pay",
    metadata,
    Column(
        "position_id",
        Integer,
        ForeignKey("workforce_position.position_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("version_id", SmallInteger, nullable=False),
    *_personnel(),
    # Sealed (ADR 0029): readable only with PLANLAYER_PAY_KEY.
    Column("hr_salary", SealedAmount("position_pay.hr_salary")),  # taken from HR, read only
    Column("salary", SealedAmount("position_pay.salary")),  # planned, monthly; None: HR's
    Column("bonus", SealedAmount("position_pay.bonus")),  # yearly
    Column("charged_to_id", Integer, ForeignKey("member.member_id")),
    # Rules of the round HR excluded the position from (ADR 0026); ids of removed rules are
    # ignored.
    Column("excluded_rules", ARRAY(Integer), nullable=False, server_default="{}"),
    Column("changed_by", Text),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

position_pay_log = Table(
    "position_pay_log",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("actor", Text),
    Column("batch_id", Text),
    Column("op", Text, nullable=False),
    Column("version_id", SmallInteger, nullable=False),
    Column("cost_center_id", Integer, nullable=False),
    Column("position_id", Integer, nullable=False),
    Column("old", JSONB),
    Column("new", JSONB),
)

# Cost center x personnel account with values from fewer positions than the round's minimum;
# rewritten by every recompute, read by the read policy of ``fact_cost_center``.
pay_small_group = Table(
    "pay_small_group",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("account_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("positions", SmallInteger, nullable=False),
    PrimaryKeyConstraint("version_id", "cost_center_id", "account_id"),
)

pay_review = Table(
    "pay_review",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("confirmed_by", Text, nullable=False),
    Column("confirmed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("version_id", "cost_center_id"),
)

# Pay rights per round (ADR 0023): who reads a cost center's pay once HR has released the round.
pay_reader = Table(
    "pay_reader",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    *_personnel(),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("user_id", Integer, ForeignKey("app_user.user_id"), nullable=False),
    Column("added_by", Text),
    Column("added_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("version_id", "cost_center_id", "user_id"),
)

# The source owner HR last decided on per cost center; another one now is a proposal.
pay_owner_seen = Table(
    "pay_owner_seen",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    *_personnel(),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("owner_id", Integer, ForeignKey("app_user.user_id")),
    PrimaryKeyConstraint("version_id", "cost_center_id"),
)

pay_release = Table(
    "pay_release",
    metadata,
    Column("version_id", SmallInteger, primary_key=True),
    *_personnel(),
    Column("released_by", Text, nullable=False),
    Column("released_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# HR hands a cost center's pay over to its readers, who edit it from then on (ADR 0024).
pay_handover = Table(
    "pay_handover",
    metadata,
    Column("version_id", SmallInteger, nullable=False),
    *_personnel(),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("handed_by", Text, nullable=False),
    Column("handed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("version_id", "cost_center_id"),
)

# Every change of pay rights and of the HR role, written by triggers; read by HR.
pay_access_log = Table(
    "pay_access_log",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("actor", Text),
    Column("action", Text, nullable=False),
    Column("version_id", SmallInteger),
    Column("cost_center_id", Integer),
    Column("user_id", Integer),
)

# Views (migrations 0019, 0020).
pay_access = Table(
    "pay_access",
    metadata,
    Column("user_id", Integer),
    Column("version_id", SmallInteger),
    Column("cost_center_id", Integer),
)
pay_account = Table(
    "pay_account", metadata, Column("version_id", SmallInteger), Column("account_id", Integer)
)


# --- Capex planning (ADR 0028) -------------------------------------------------------------------

MODULE_CAPEX = "capex"

# The installation's investment types, set by controllers: the balance sheet account an
# investment of the type is capitalised on, and its default useful life for depreciation.
capex_type = Table(
    "capex_type",
    metadata,
    Column("type_id", SmallInteger, Identity(), primary_key=True),
    Column("code", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("asset_account", Text),
    Column("useful_life", SmallInteger),  # years
    Column("active", Boolean, nullable=False, server_default="true"),
)

# A round's capex settings: above which amount an investment is better planned as a large
# project, and whether planners schedule cash out.
capex_setting = Table(
    "capex_setting",
    metadata,
    Column("version_id", SmallInteger, primary_key=True),
    Column("module", Text, nullable=False, server_default=MODULE_CAPEX),
    Column("project_threshold", AMOUNT_TYPE),
    Column("cash_out", Boolean, nullable=False, server_default="false"),
    Column("changed_by", Text),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    _module_fk(),
)

# An investment (kind line) or a large project (kind project) grouping lines. A project's lines
# share its cost center, priority and confirmation; it has no type, start or amount of its own.
# Scoped by the cost center; confirmed by ``capex_confirmer``; locked with the round.
capex_item = Table(
    "capex_item",
    metadata,
    Column("item_id", Integer, Identity(), primary_key=True),
    Column("version_id", SmallInteger, nullable=False),
    Column("module", Text, nullable=False, server_default=MODULE_CAPEX),
    Column("kind", Text, nullable=False),
    Column("project_id", Integer),
    Column("cost_center_id", Integer, ForeignKey("member.member_id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("type_id", SmallInteger, ForeignKey("capex_type.type_id")),
    Column("priority", SmallInteger),  # 1 to 3
    Column("start_month", Date),  # first of the month it is taken into use
    Column("amount", AMOUNT_TYPE),
    Column("status", Text),  # open / confirmed / declined
    Column("status_by", Text),
    Column("status_at", DateTime(timezone=True)),
    Column("row_version", Integer, nullable=False, server_default="1"),
    Column("created_by", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("changed_by", Text),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    _module_fk(),
    UniqueConstraint("item_id", "cost_center_id", name="uq_capex_item_cost_center"),
    ForeignKeyConstraint(
        ["project_id", "cost_center_id"],
        ["capex_item.item_id", "capex_item.cost_center_id"],
        name="fk_capex_item_project",
        ondelete="CASCADE",
        onupdate="CASCADE",
    ),
)

# An investment's cash out by period: a month, or a quarter's first month in rounds planned by
# quarter, or the next fiscal year's first month for what is paid after the year. Never more
# than the investment's amount.
capex_cash_out = Table(
    "capex_cash_out",
    metadata,
    Column(
        "item_id", Integer, ForeignKey("capex_item.item_id", ondelete="CASCADE"), nullable=False
    ),
    Column("period", Date, nullable=False),
    Column("amount", AMOUNT_TYPE, nullable=False),
    PrimaryKeyConstraint("item_id", "period"),
)

capex_log = Table(
    "capex_log",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("actor", Text),
    Column("batch_id", Text),
    Column("op", Text, nullable=False),
    Column("target", Text, nullable=False),
    Column("version_id", SmallInteger),
    Column("item_id", Integer, nullable=False),
    Column("old", JSONB),
    Column("new", JSONB),
)

# View (migration 0027): who confirms the investments of which cost center.
capex_confirmer = Table(
    "capex_confirmer", metadata, Column("user_id", Integer), Column("cost_center_id", Integer)
)
