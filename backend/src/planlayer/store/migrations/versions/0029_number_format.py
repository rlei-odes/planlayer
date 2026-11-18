"""How amounts are shown and typed: the installation's default, and each user's own choices
(NULL takes the default).

Revision ID: 0029
Revises: 0028
"""

from collections.abc import Sequence

from alembic import op

from planlayer.store.migrate import migration_schema

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEPARATORS = "IN ('comma_dot', 'dot_comma', 'apostrophe_dot', 'space_comma')"
SCALE = "IN ('full', 'k', 'M')"


def upgrade() -> None:
    s = migration_schema()
    op.execute(f"""
        ALTER TABLE {s}.installation
            ADD COLUMN number_separators text NOT NULL DEFAULT 'comma_dot',
            ADD COLUMN number_decimals boolean NOT NULL DEFAULT true,
            ADD COLUMN number_scale text NOT NULL DEFAULT 'full',
            ADD CONSTRAINT ck_installation_number_separators CHECK (number_separators {SEPARATORS}),
            ADD CONSTRAINT ck_installation_number_scale CHECK (number_scale {SCALE})
    """)
    op.execute(f"""
        ALTER TABLE {s}.app_user
            ADD COLUMN number_separators text,
            ADD COLUMN number_decimals boolean,
            ADD COLUMN number_scale text,
            ADD CONSTRAINT ck_app_user_number_separators CHECK (number_separators {SEPARATORS}),
            ADD CONSTRAINT ck_app_user_number_scale CHECK (number_scale {SCALE})
    """)


def downgrade() -> None:
    s = migration_schema()
    for table in ("installation", "app_user"):
        op.execute(f"""
            ALTER TABLE {s}.{table}
                DROP COLUMN number_separators,
                DROP COLUMN number_decimals,
                DROP COLUMN number_scale
        """)
