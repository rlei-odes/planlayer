"""Notes on investments (ADR 0017, ADR 0028): an investment's comments become line notes, with
threads, mentions, tasks and resolving. A note thread of module ``capex`` belongs to an
investment (``capex_item_id``, deleted with it) and is read by whoever reads the investment; it
keeps no cost center of its own, as an investment moves between cost centers. Each investment's
comments become one thread, oldest first; ``capex_comment`` goes.

Revision ID: 0028
Revises: 0027
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA
ME = "nullif(current_setting('app.user_id', true), '')::int"

SHEET_LINES = (
    "(module = 'cost_center' AND account_id IS NOT NULL AND work_center_id IS NULL "
    "AND activity_type_id IS NULL) OR (module = 'capacity' AND account_id IS NULL "
    "AND work_center_id IS NOT NULL AND activity_type_id IS NOT NULL)"
)
LINE_CHECK = (
    f"(({SHEET_LINES}) AND cost_center_id IS NOT NULL AND capex_item_id IS NULL) "
    "OR (module = 'capex' AND capex_item_id IS NOT NULL AND cost_center_id IS NULL "
    "AND account_id IS NULL AND work_center_id IS NULL AND activity_type_id IS NULL)"
)


def _sheet_line(s: str) -> str:
    """Who reads a line of a sheet (migration 0013): the cost sheet's access, or the capacity
    sheet's."""

    def scope(view: str) -> str:
        return (
            f"cost_center_id = ANY (ARRAY(SELECT cost_center_id FROM {s}.{view} "
            f"WHERE user_id = {ME}))"
        )

    return (
        f"(module = 'cost_center' AND {scope('cost_center_access')}) OR "
        f"(module = 'capacity' AND {scope('capacity_access')})"
    )


def _thread_policies(s: str, line: str) -> None:
    t = f"{s}.note_thread"
    for name in ("read", "add", "change", "remove"):
        op.execute(f"DROP POLICY {name} ON {t}")
    op.execute(f"CREATE POLICY read ON {t} FOR SELECT USING ({line})")
    op.execute(f"CREATE POLICY add ON {t} FOR INSERT WITH CHECK ({line})")
    op.execute(f"CREATE POLICY change ON {t} FOR UPDATE USING ({line}) WITH CHECK ({line})")
    op.execute(f"CREATE POLICY remove ON {t} FOR DELETE USING (({line}) AND created_by = {ME})")


def _unforced(s: str, tables: tuple[str, ...], force: bool) -> None:
    """Row-level security binds the owner too (FORCE); data is moved without it."""
    for table in tables:
        op.execute(f"ALTER TABLE {s}.{table} {'' if force else 'NO '}FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    s = migration_schema()
    op.execute(
        f"ALTER TABLE {s}.note_thread ADD COLUMN capex_item_id integer "
        f"CONSTRAINT fk_note_thread_capex_item_id_capex_item "
        f"REFERENCES {s}.capex_item (item_id) ON DELETE CASCADE, "
        "ALTER COLUMN cost_center_id DROP NOT NULL"
    )
    op.execute(f"ALTER TABLE {s}.note_thread DROP CONSTRAINT ck_note_thread_line")
    op.execute(
        f"ALTER TABLE {s}.note_thread ADD CONSTRAINT ck_note_thread_line CHECK ({LINE_CHECK})"
    )
    op.create_index("ix_note_thread_capex_item", "note_thread", ["capex_item_id"], schema=S)

    # An investment's notes are read and written by whoever reads the investment (its own
    # row-level security); a thread is deleted by its creator, as on the sheets.
    _thread_policies(
        s,
        f"{_sheet_line(s)} OR (module = 'capex' AND "
        f"capex_item_id IN (SELECT item_id FROM {s}.capex_item))",
    )

    # Each investment's comments become one thread in its round, oldest first.
    moved = ("note_thread", "note", "capex_comment", "capex_item")
    _unforced(s, moved, force=False)
    op.execute(f"""
        INSERT INTO {s}.note_thread (module, capex_item_id, round_id, created_at, created_by)
        SELECT DISTINCT ON (c.item_id) 'capex', c.item_id, i.version_id, c.created_at, c.author_id
        FROM {s}.capex_comment c JOIN {s}.capex_item i USING (item_id)
        ORDER BY c.item_id, c.created_at, c.comment_id
    """)
    op.execute(f"""
        INSERT INTO {s}.note (thread_id, author_id, body, created_at)
        SELECT t.thread_id, c.author_id, c.body, c.created_at
        FROM {s}.capex_comment c JOIN {s}.note_thread t ON t.capex_item_id = c.item_id
        ORDER BY c.created_at, c.comment_id
    """)
    _unforced(s, ("note_thread", "note", "capex_item"), force=True)
    op.drop_table("capex_comment", schema=S)


def downgrade() -> None:
    s = migration_schema()
    op.create_table(
        "capex_comment",
        sa.Column("comment_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "item_id",
            sa.Integer,
            sa.ForeignKey(f"{S}.capex_item.item_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author_id", sa.Integer, sa.ForeignKey(f"{S}.app_user.user_id"), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("btrim(body) <> ''", name="body"),
        schema=S,
    )
    op.create_index("ix_capex_comment_item", "capex_comment", ["item_id"], schema=S)
    # An investment's notes go back as its comments, every thread's in order; then the threads.
    _unforced(s, ("note_thread", "note", "capex_item"), force=False)
    op.execute(f"""
        INSERT INTO {s}.capex_comment (item_id, author_id, body, created_at)
        SELECT t.capex_item_id, n.author_id, n.body, n.created_at
        FROM {s}.note n JOIN {s}.note_thread t USING (thread_id)
        WHERE t.module = 'capex'
        ORDER BY n.created_at, n.note_id
    """)
    op.execute(f"DELETE FROM {s}.note_thread WHERE module = 'capex'")
    _unforced(s, ("note_thread", "note", "capex_item"), force=True)

    t = f"{s}.capex_comment"
    in_item = f"item_id IN (SELECT item_id FROM {s}.capex_item)"
    op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY read ON {t} FOR SELECT USING ({in_item})")
    op.execute(f"CREATE POLICY add ON {t} FOR INSERT WITH CHECK ({in_item} AND author_id = {ME})")
    op.execute(f"CREATE POLICY change ON {t} FOR UPDATE USING (false) WITH CHECK (false)")
    op.execute(f"CREATE POLICY remove ON {t} FOR DELETE USING ({in_item} AND author_id = {ME})")
    op.execute(f"""
        CREATE TRIGGER log AFTER INSERT OR UPDATE OR DELETE ON {t}
        FOR EACH ROW EXECUTE FUNCTION {s}.log_capex()
    """)

    _thread_policies(s, _sheet_line(s))
    op.drop_index("ix_note_thread_capex_item", "note_thread", schema=S)
    op.execute(f"ALTER TABLE {s}.note_thread DROP CONSTRAINT ck_note_thread_line")
    op.execute(
        f"ALTER TABLE {s}.note_thread ADD CONSTRAINT ck_note_thread_line CHECK ({SHEET_LINES})"
    )
    op.execute(
        f"ALTER TABLE {s}.note_thread DROP COLUMN capex_item_id, "
        "ALTER COLUMN cost_center_id SET NOT NULL"
    )
