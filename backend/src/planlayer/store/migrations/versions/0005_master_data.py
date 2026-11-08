"""Dataset mappings, member origins, local overrides, shadow records (ADR 0012).

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA
ORIGIN = "origin IN ('source', 'manual')"


def _fk(target: str) -> sa.ForeignKey:
    return sa.ForeignKey(f"{S}.{target}")


def _now() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        "set_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    s = migration_schema()
    # Alembic's column operations aren't schema-translated: raw ALTER TABLE.
    op.execute(f"""
        ALTER TABLE {s}.member
            ADD COLUMN origin text NOT NULL DEFAULT 'source',
            ADD COLUMN source_name text,
            ADD CONSTRAINT ck_member_origin CHECK ({ORIGIN})
    """)
    op.execute(f"UPDATE {s}.member SET source_name = name")
    op.execute(f"""
        ALTER TABLE {s}.hier_edge
            ADD COLUMN origin text NOT NULL DEFAULT 'source',
            ADD COLUMN source_parent_id integer REFERENCES {s}.member (member_id),
            ADD CONSTRAINT ck_hier_edge_origin CHECK ({ORIGIN})
    """)
    op.execute(f"UPDATE {s}.hier_edge SET source_parent_id = parent_id")
    op.execute(f"ALTER TABLE {s}.sync_run ADD COLUMN summary jsonb")

    op.create_table(
        "member_override",
        sa.Column("member_id", sa.Integer, _fk("member.member_id"), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("set_by", sa.Text),
        _now(),
        schema=S,
    )
    op.create_table(
        "edge_override",
        sa.Column("hier_id", sa.SmallInteger, _fk("hierarchy.hier_id"), nullable=False),
        sa.Column("member_id", sa.Integer, _fk("member.member_id"), nullable=False),
        sa.Column("parent_id", sa.Integer, _fk("member.member_id")),  # NULL: a root
        sa.Column("set_by", sa.Text),
        _now(),
        sa.PrimaryKeyConstraint("hier_id", "member_id"),
        schema=S,
    )
    op.create_table(
        "member_shadow",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("member_id", sa.Integer, _fk("member.member_id"), nullable=False),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("attributes", JSONB, nullable=False),
        sa.Column("parents", JSONB, nullable=False),  # hierarchy code -> parent code
        sa.Column("confirmed_by", sa.Text),
        sa.Column(
            "confirmed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=S,
    )
    op.create_table(
        "dataset_mapping",
        sa.Column("dataset", sa.Text, primary_key=True),
        sa.Column("query", sa.Text, nullable=False),
        sa.Column("fields", JSONB, nullable=False),  # field -> source column
        sa.Column("hierarchy", JSONB),  # {"parent": col} or {"levels": [{"code", "name"}]}
        sa.Column("updated_by", sa.Text),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "dataset IN ('entities', 'cost_centers', 'accounts', 'actuals')", name="dataset"
        ),
        schema=S,
    )
    # The entity of cost centers that name none.
    op.execute(f"""
        INSERT INTO {s}.member (dim_id, code, name, is_group, origin)
        VALUES (1, '-', 'No entity', false, 'manual')
        ON CONFLICT (dim_id, code) DO NOTHING
    """)


def downgrade() -> None:
    s = migration_schema()
    for name in ("dataset_mapping", "member_shadow", "edge_override", "member_override"):
        op.drop_table(name, schema=S)
    op.execute(f"ALTER TABLE {s}.sync_run DROP COLUMN summary")
    op.execute(f"""
        ALTER TABLE {s}.hier_edge DROP CONSTRAINT ck_hier_edge_origin,
            DROP COLUMN source_parent_id, DROP COLUMN origin
    """)
    op.execute(f"""
        DELETE FROM {s}.member m WHERE m.origin = 'manual' AND m.code = '-' AND m.dim_id = 1
            AND NOT EXISTS (SELECT FROM {s}.fact_cost_center f WHERE f.dim_entity = m.member_id)
    """)
    op.execute(f"""
        ALTER TABLE {s}.member DROP CONSTRAINT ck_member_origin,
            DROP COLUMN source_name, DROP COLUMN origin
    """)
