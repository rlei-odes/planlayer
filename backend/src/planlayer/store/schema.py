"""Table definitions of the app store.

Tables are declared in the symbolic schema ``APP_SCHEMA``. The engine translates it to the
configured schema name (``schema_translate_map``), so code and migrations never format schema
names into SQL. Raw SQL strings are not translated; write them with SQLAlchemy constructs.
"""

from sqlalchemy import CheckConstraint, Column, DateTime, MetaData, SmallInteger, Table, func

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
