"""Baseline: the installation settings row.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from planlayer.store.schema import APP_SCHEMA

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table = op.create_table(
        "installation",
        sa.Column("id", sa.SmallInteger, primary_key=True, server_default="1"),
        sa.Column("fiscal_year_start_month", sa.SmallInteger, nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("id = 1", name="single_row"),
        sa.CheckConstraint("fiscal_year_start_month BETWEEN 1 AND 12", name="fiscal_month"),
        schema=APP_SCHEMA,
    )
    # Through the Table object: the schema translation applies to Tables, not to sa.table().
    op.execute(sa.insert(table))


def downgrade() -> None:
    op.drop_table("installation", schema=APP_SCHEMA)
