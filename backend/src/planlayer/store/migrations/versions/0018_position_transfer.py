"""Transfers of a position to another cost center (ADR 0021): proposed by the sending cost
center, accepted or declined by the receiving one. A position records the cost center it came
from, and one transferred during the year the cost center it went to.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA


def upgrade() -> None:
    s = migration_schema()
    # Alembic's column operations aren't schema-translated.
    op.execute(
        f"ALTER TABLE {s}.workforce_position "
        f"ADD COLUMN transferred_from_id integer REFERENCES {s}.member (member_id), "
        f"ADD COLUMN transferred_to_id integer REFERENCES {s}.member (member_id), "
        "DROP CONSTRAINT uq_workforce_position_version_id_code"
    )
    # A person transferred during the year has two positions with one number: the one that
    # ended on the sending cost center, and the one on the receiving cost center.
    op.execute(
        f"CREATE UNIQUE INDEX uq_workforce_position_version_id_code ON {s}.workforce_position "
        "(version_id, code) WHERE transferred_to_id IS NULL"
    )
    op.create_table(
        "workforce_transfer",
        sa.Column("transfer_id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("module", sa.Text, nullable=False, server_default="workforce"),
        sa.Column(
            "position_id",
            sa.Integer,
            sa.ForeignKey(f"{S}.workforce_position.position_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_cost_center_id", sa.Integer, sa.ForeignKey(f"{S}.member.member_id"),
                  nullable=False),
        sa.Column("to_cost_center_id", sa.Integer, sa.ForeignKey(f"{S}.member.member_id"),
                  nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("starts", sa.Date),  # the first day on the receiving cost center
        sa.Column("status", sa.Text, nullable=False, server_default="proposed"),
        sa.Column("note", sa.Text),
        sa.Column("proposed_by", sa.Integer, sa.ForeignKey(f"{S}.app_user.user_id"),
                  nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("decided_by", sa.Integer, sa.ForeignKey(f"{S}.app_user.user_id")),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.Text),  # why it was declined
        sa.Column(
            "new_position_id",
            sa.Integer,
            sa.ForeignKey(f"{S}.workforce_position.position_id", ondelete="SET NULL"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id", "module"],
            [f"{S}.round_module.version_id", f"{S}.round_module.module"],
        ),
        sa.CheckConstraint("module = 'workforce'", name="module"),
        sa.CheckConstraint("kind IN ('full', 'during_year')", name="kind"),
        sa.CheckConstraint(
            "status IN ('proposed', 'accepted', 'declined', 'cancelled')", name="status"
        ),
        sa.CheckConstraint("(kind = 'full') = (starts IS NULL)", name="starts"),
        sa.CheckConstraint("starts IS NULL OR extract(day FROM starts) = 1", name="first_day"),
        sa.CheckConstraint("from_cost_center_id <> to_cost_center_id", name="other"),
        sa.CheckConstraint(
            "(status = 'proposed') = (decided_at IS NULL)", name="decided"
        ),
        schema=S,
    )  # fmt: skip
    # One open proposal per position.
    op.execute(
        f"CREATE UNIQUE INDEX uq_workforce_transfer_proposed ON {s}.workforce_transfer "
        "(position_id) WHERE status = 'proposed'"
    )

    # Row-level security: both sides read a transfer; the sending side proposes it; either side
    # decides or withdraws it. Who may do which is checked by the application on top.
    def scope(column: str, write: bool) -> str:
        return (
            f"{column} = ANY (ARRAY(SELECT cost_center_id FROM {s}.cost_center_access WHERE "
            "user_id = nullif(current_setting('app.user_id', true), '')::int"
            f"{' AND can_write' if write else ''}))"
        )

    def either(write: bool) -> str:
        return f"({scope('from_cost_center_id', write)} OR {scope('to_cost_center_id', write)})"

    t = f"{s}.workforce_transfer"
    op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY read ON {t} FOR SELECT USING ({either(False)})")
    op.execute(
        f"CREATE POLICY add ON {t} FOR INSERT WITH CHECK ({scope('from_cost_center_id', True)})"
    )
    op.execute(
        f"CREATE POLICY change ON {t} FOR UPDATE USING ({either(True)}) WITH CHECK ({either(True)})"
    )
    op.execute(f"CREATE POLICY remove ON {t} FOR DELETE USING ({either(True)})")


def downgrade() -> None:
    s = migration_schema()
    op.drop_table("workforce_transfer", schema=S)
    # The ended halves of transfers during the year share their number with the other half; the
    # old unique constraint can't hold them. They go, without moving any status.
    p = f"{s}.workforce_position"
    op.execute(f"ALTER TABLE {p} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {p} DISABLE TRIGGER USER")
    op.execute(f"DELETE FROM {p} WHERE transferred_to_id IS NOT NULL")
    op.execute(f"ALTER TABLE {p} ENABLE TRIGGER USER")
    op.execute(f"ALTER TABLE {p} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP INDEX {s}.uq_workforce_position_version_id_code")
    op.execute(
        f"ALTER TABLE {s}.workforce_position DROP COLUMN transferred_to_id, "
        "DROP COLUMN transferred_from_id, "
        "ADD CONSTRAINT uq_workforce_position_version_id_code UNIQUE (version_id, code)"
    )
