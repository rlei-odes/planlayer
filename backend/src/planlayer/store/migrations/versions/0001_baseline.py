"""The app store of Planlayer 0.5.0, created in one step: tables, functions, triggers, row-level
security policies and the seed rows (dimensions, hierarchies, staff types, the system user,
the installation's and the login's settings), from 0001_baseline.sql.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op
from sqlalchemy import text

from planlayer.store.migrate import migration_schema

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL = Path(__file__).with_name("0001_baseline.sql")


def upgrade() -> None:
    schema = migration_schema().strip('"')
    sql = SQL.read_text(encoding="utf-8").replace("__SCHEMA__", schema)
    # Functions refer to tables created after them, as pg_dump orders them.
    op.execute("SET LOCAL check_function_bodies = false")
    # Straight to the driver: the file is many statements, with colons and percent signs that
    # SQLAlchemy's and psycopg's parameter syntax would read.
    cursor = op.get_bind().connection.cursor()
    try:
        cursor.execute(sql)
    finally:
        cursor.close()


def downgrade() -> None:
    """Empty the schema, which stays: the install script made it."""
    s = migration_schema()
    conn = op.get_bind()
    name = s.strip('"')
    tables = conn.execute(
        text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :s AND c.relkind IN ('r', 'p') AND c.relname <> 'alembic_version'"
        ),
        {"s": name},
    ).scalars()
    for table in list(tables):
        op.execute(f'DROP TABLE IF EXISTS {s}."{table}" CASCADE')
    functions = conn.execute(
        text(
            "SELECT p.oid::regprocedure::text FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = :s"
        ),
        {"s": name},
    ).scalars()
    for function in list(functions):
        op.execute(f"DROP FUNCTION IF EXISTS {function} CASCADE")
    # Views went with their tables; sequences not owned by a column would be left.
    sequences = conn.execute(
        text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :s AND c.relkind = 'S'"
        ),
        {"s": name},
    ).scalars()
    for sequence in list(sequences):
        op.execute(f'DROP SEQUENCE IF EXISTS {s}."{sequence}" CASCADE')
