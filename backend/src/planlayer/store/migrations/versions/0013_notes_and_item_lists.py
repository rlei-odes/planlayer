"""Line notes and item lists (ADR 0017): threads of notes with mentions and tasks, one notepad
per line, and item lists per cost center x account; row-level security following the line's
sheet, the item list visibility rule, and an audit log.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA
NOW = sa.func.now()
LOGGED = ("note_thread", "note", "note_mention", "note_grid", "item_list", "item")


def _member(name: str, nullable: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Integer, sa.ForeignKey(f"{S}.member.member_id"), nullable=nullable)


def _user(name: str, nullable: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Integer, sa.ForeignKey(f"{S}.app_user.user_id"), nullable=nullable)


def _round(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        name, sa.SmallInteger, sa.ForeignKey(f"{S}.planning_round.version_id"), nullable=False
    )


def _line() -> list[sa.Column]:  # type: ignore[type-arg]
    """A line: a cost center with an account (cost sheet) or a work center and an activity type
    (capacity sheet)."""
    return [
        sa.Column("module", sa.Text, nullable=False),
        _member("cost_center_id"),
        _member("account_id", nullable=True),
        _member("work_center_id", nullable=True),
        _member("activity_type_id", nullable=True),
    ]


LINE_CHECK = (
    "(module = 'cost_center' AND account_id IS NOT NULL AND work_center_id IS NULL "
    "AND activity_type_id IS NULL) OR (module = 'capacity' AND account_id IS NULL "
    "AND work_center_id IS NOT NULL AND activity_type_id IS NOT NULL)"
)


def upgrade() -> None:
    s = migration_schema()
    op.create_table(
        "note_thread",
        sa.Column("thread_id", sa.BigInteger, sa.Identity(), primary_key=True),
        *_line(),
        # The round the thread was last written in (ADR 0017): it shows there, and while open in
        # every later round.
        _round("round_id"),
        _user("assignee_id", nullable=True),  # a task's assignee
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        _user("resolved_by", nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        _user("created_by"),
        sa.CheckConstraint(LINE_CHECK, name="line"),
        schema=S,
    )
    op.create_index(
        "note_thread_line",
        "note_thread",
        ["cost_center_id", "account_id", "work_center_id", "activity_type_id"],
        schema=S,
    )
    op.create_table(
        "note",
        sa.Column("note_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "thread_id",
            sa.BigInteger,
            sa.ForeignKey(f"{S}.note_thread.thread_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        _user("author_id"),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("edited_at", sa.DateTime(timezone=True)),
        schema=S,
    )
    op.create_table(
        "note_mention",
        sa.Column(
            "note_id",
            sa.BigInteger,
            sa.ForeignKey(f"{S}.note.note_id", ondelete="CASCADE"),
            nullable=False,
        ),
        _user("user_id"),
        sa.PrimaryKeyConstraint("note_id", "user_id"),
        schema=S,
    )
    op.create_table(
        "note_grid",
        sa.Column("grid_id", sa.BigInteger, sa.Identity(), primary_key=True),
        *_line(),
        _round("round_id"),
        # [{"text": "...", "amount": "123.45" | null}, ...]
        sa.Column("rows", JSONB, nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        _user("changed_by"),
        sa.CheckConstraint(LINE_CHECK, name="line"),
        schema=S,
    )
    # One notepad per line; the line's columns may be null, so the key treats nulls as equal.
    op.execute(
        f"CREATE UNIQUE INDEX note_grid_line ON {s}.note_grid "
        "(cost_center_id, account_id, work_center_id, activity_type_id) NULLS NOT DISTINCT"
    )
    op.create_table(
        "item_list",
        sa.Column("list_id", sa.BigInteger, sa.Identity(), primary_key=True),
        _member("cost_center_id"),
        _member("account_id"),
        _user("owner_id"),
        sa.Column("visibility", sa.Text, nullable=False, server_default="private"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        _user("changed_by"),
        sa.CheckConstraint("visibility IN ('private', 'cost_center', 'public')", name="visibility"),
        sa.UniqueConstraint("cost_center_id", "account_id"),  # one list per line
        schema=S,
    )
    op.create_table(
        "item",
        sa.Column("item_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "list_id",
            sa.BigInteger,
            sa.ForeignKey(f"{S}.item_list.list_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("supplier", sa.Text),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "amount",
            sa.Numeric(18, 2),
            sa.Computed("round(quantity * unit_price, 2)", persisted=True),
        ),
        sa.Column("comment", sa.Text),
        schema=S,
    )
    op.create_table(
        "annotation_log",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("actor", sa.Text),
        sa.Column("batch_id", sa.Text),
        sa.Column("table_name", sa.Text, nullable=False),
        sa.Column("op", sa.Text, nullable=False),
        sa.Column("old", JSONB),
        sa.Column("new", JSONB),
        schema=S,
    )

    me = "nullif(current_setting('app.user_id', true), '')::int"

    def scope(view: str, write: bool) -> str:
        return (
            f"cost_center_id = ANY (ARRAY(SELECT cost_center_id FROM {s}.{view} WHERE "
            f"user_id = {me}{' AND can_write' if write else ''}))"
        )

    def line(write: bool) -> str:
        """The line's sheet: the cost sheet's access, or the capacity sheet's."""
        return (
            f"((module = 'cost_center' AND {scope('cost_center_access', write)}) OR "
            f"(module = 'capacity' AND {scope('capacity_access', write)}))"
        )

    def secure(table: str) -> None:
        op.execute(f"ALTER TABLE {s}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {s}.{table} FORCE ROW LEVEL SECURITY")

    def policies(table: str, read: str, write: str, remove: str | None = None) -> None:
        secure(table)
        op.execute(f"CREATE POLICY read ON {s}.{table} FOR SELECT USING ({read})")
        op.execute(f"CREATE POLICY add ON {s}.{table} FOR INSERT WITH CHECK ({write})")
        op.execute(
            f"CREATE POLICY change ON {s}.{table} FOR UPDATE USING ({write}) WITH CHECK ({write})"
        )
        op.execute(f"CREATE POLICY remove ON {s}.{table} FOR DELETE USING ({remove or write})")

    # Everyone who reads a line writes notes on it, resolves and assigns its threads; authors
    # change their own notes.
    # A thread is deleted with its first note, by its creator (who wrote that note); the
    # replies go with it, as in a document's comment threads.
    policies("note_thread", line(False), line(False), f"{line(False)} AND created_by = {me}")
    in_thread = f"thread_id IN (SELECT thread_id FROM {s}.note_thread)"
    policies("note", in_thread, f"{in_thread} AND author_id = {me}")
    mine = f"note_id IN (SELECT note_id FROM {s}.note WHERE author_id = {me})"
    policies("note_mention", f"note_id IN (SELECT note_id FROM {s}.note)", mine)
    # The notepad: read with the line, written by whoever may write it.
    policies("note_grid", line(False), line(True))

    # Item lists (cost sheet only): private to the owner; cost center lists for whoever plans
    # the cost center; public lists read by whoever reads it and written by whoever plans it.
    owner = f"owner_id = {me}"
    plans = scope("cost_center_access", True)
    reads = scope("cost_center_access", False)
    read = (
        f"{owner} OR (visibility = 'cost_center' AND {plans}) "
        f"OR (visibility = 'public' AND {reads})"
    )
    write = f"{owner} OR (visibility IN ('cost_center', 'public') AND {plans})"
    secure("item_list")
    op.execute(f"CREATE POLICY read ON {s}.item_list FOR SELECT USING ({read})")
    op.execute(f"CREATE POLICY add ON {s}.item_list FOR INSERT WITH CHECK ({owner} AND {plans})")
    op.execute(
        f"CREATE POLICY change ON {s}.item_list FOR UPDATE USING ({write}) WITH CHECK ({write})"
    )
    op.execute(f"CREATE POLICY remove ON {s}.item_list FOR DELETE USING ({owner})")
    writable = (
        f"list_id IN (SELECT list_id FROM {s}.item_list WHERE {owner} OR "
        f"(visibility IN ('cost_center', 'public') AND {plans}))"
    )
    policies("item", f"list_id IN (SELECT list_id FROM {s}.item_list)", writable)

    # Only the owner changes a list's visibility or owner.
    op.execute(f"""
        CREATE FUNCTION {s}.guard_item_list() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.visibility IS DISTINCT FROM OLD.visibility
                OR NEW.owner_id IS DISTINCT FROM OLD.owner_id)
               AND OLD.owner_id IS DISTINCT FROM {me} THEN
                RAISE EXCEPTION 'only the list''s owner changes its visibility'
                    USING ERRCODE = 'PL002';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER guard BEFORE UPDATE ON {s}.item_list
        FOR EACH ROW EXECUTE FUNCTION {s}.guard_item_list()
    """)

    # Audit (ADR 0005): every change, with the request's actor and change batch.
    op.execute(f"""
        CREATE FUNCTION {s}.log_annotation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO {s}.annotation_log (actor, batch_id, table_name, op, old, new)
            VALUES (nullif(current_setting('app.actor', true), ''),
                    nullif(current_setting('app.batch_id', true), ''),
                    TG_TABLE_NAME, TG_OP,
                    CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
                    CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END);
            RETURN NULL;
        END $$
    """)
    for table in LOGGED:
        op.execute(f"""
            CREATE TRIGGER log AFTER INSERT OR UPDATE OR DELETE ON {s}.{table}
            FOR EACH ROW EXECUTE FUNCTION {s}.log_annotation()
        """)


def downgrade() -> None:
    s = migration_schema()
    for table in ("annotation_log", "item", "item_list", "note_grid", "note_mention", "note"):
        op.drop_table(table, schema=S)
    op.drop_table("note_thread", schema=S)
    op.execute(f"DROP FUNCTION {s}.log_annotation()")
    op.execute(f"DROP FUNCTION {s}.guard_item_list()")
