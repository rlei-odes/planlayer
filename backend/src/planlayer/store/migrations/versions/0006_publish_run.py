"""Publish runs (ADR 0011).

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from planlayer.store.schema import APP_SCHEMA

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA


def upgrade() -> None:
    op.create_table(
        "publish_run",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "version_id", sa.SmallInteger, sa.ForeignKey(f"{S}.version.version_id"), nullable=False
        ),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("actor", sa.Text),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),  # NULL: the run failed
        sa.Column("rows", sa.Integer),
        sa.Column("added", sa.Integer),
        sa.Column("changed", sa.Integer),
        sa.Column("removed", sa.Integer),
        sa.Column("fingerprint", sa.Text),
        sa.CheckConstraint("scope IN ('approved', 'all')", name="scope"),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("publish_run", schema=S)
