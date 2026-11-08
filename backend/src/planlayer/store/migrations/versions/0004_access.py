"""Users, sessions and access (ADR 0009): owners matched to users, owner overrides, deputies,
approvers by user, the access views, and forced row-level security on the fact table.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA
COST_CENTER, H_COST_CENTER = 2, 1


def _fk(target: str, **kw: object) -> sa.ForeignKey:
    return sa.ForeignKey(f"{S}.{target}", **kw)  # type: ignore[arg-type]


def upgrade() -> None:
    app_user = op.create_table(
        "app_user",
        sa.Column("user_id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("login", sa.Text, nullable=False, unique=True),
        sa.Column("email", sa.Text),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_controller", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_viewer", sa.Boolean, nullable=False, server_default=sa.false()),
        # The actor of syncs and other jobs; can't log in.
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("disabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("login = lower(login)", name="login_lowercase"),
        schema=S,
    )
    op.create_table(
        "user_session",
        sa.Column("token_hash", sa.Text, primary_key=True),
        sa.Column(
            "user_id", sa.Integer, _fk("app_user.user_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema=S,
    )
    op.create_table(
        "owner_override",
        sa.Column("cost_center_id", sa.Integer, _fk("member.member_id"), primary_key=True),
        sa.Column("user_id", sa.Integer, _fk("app_user.user_id")),  # NULL: no owner
        sa.Column("set_by", sa.Text),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=S,
    )
    op.create_table(
        "planner_grant",
        sa.Column("member_id", sa.Integer, _fk("member.member_id"), nullable=False),
        sa.Column("user_id", sa.Integer, _fk("app_user.user_id"), nullable=False),
        sa.Column("granted_by", sa.Text),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("member_id", "user_id"),
        schema=S,
    )

    op.bulk_insert(
        app_user,
        [{"login": "system", "display_name": "System", "is_controller": True, "is_system": True}],
    )
    # Approvers were assigned by name; each name becomes a user.
    s = migration_schema()
    op.execute(f"""
        INSERT INTO {s}.app_user (login, email, display_name)
        SELECT DISTINCT lower(user_name), CASE WHEN user_name LIKE '%@%' THEN user_name END,
               user_name
        FROM {s}.approver_assignment
        ON CONFLICT (login) DO NOTHING
    """)
    op.execute(f"ALTER TABLE {s}.approver_assignment ADD COLUMN user_id integer")
    op.execute(f"""
        UPDATE {s}.approver_assignment a SET user_id = u.user_id
        FROM {s}.app_user u WHERE u.login = lower(a.user_name)
    """)
    op.execute(f"""
        ALTER TABLE {s}.approver_assignment
            DROP CONSTRAINT pk_approver_assignment,
            DROP COLUMN user_name,
            ALTER COLUMN user_id SET NOT NULL,
            ADD CONSTRAINT pk_approver_assignment PRIMARY KEY (member_id, user_id),
            ADD CONSTRAINT fk_approver_assignment_user_id_app_user
                FOREIGN KEY (user_id) REFERENCES {s}.app_user (user_id)
    """)

    # The planner of each leaf cost center: the controller's override, else the active user
    # whose e-mail or login matches the synced owner (case-insensitive).
    op.execute(f"""
        CREATE VIEW {s}.cost_center_owner AS
        SELECT m.member_id AS cost_center_id,
               CASE WHEN o.cost_center_id IS NOT NULL THEN o.user_id ELSE d.user_id END AS user_id,
               d.user_id AS matched_user_id,
               o.cost_center_id IS NOT NULL AS overridden
        FROM {s}.member m
        LEFT JOIN {s}.owner_override o ON o.cost_center_id = m.member_id
        LEFT JOIN LATERAL (
            SELECT u.user_id FROM {s}.app_user u
            WHERE NOT u.disabled AND NOT u.is_system AND m.owner IS NOT NULL
              AND (lower(u.email) = lower(m.owner) OR u.login = lower(m.owner))
            ORDER BY u.user_id LIMIT 1
        ) d ON true
        WHERE m.dim_id = {COST_CENTER} AND NOT m.is_group
    """)
    # Who may read (and write) which cost center's plan values and actuals.
    op.execute(f"""
        CREATE VIEW {s}.cost_center_access AS
        SELECT u.user_id, m.member_id AS cost_center_id, u.is_controller AS can_write
        FROM {s}.app_user u CROSS JOIN {s}.member m
        WHERE (u.is_controller OR u.is_viewer) AND NOT u.disabled
          AND m.dim_id = {COST_CENTER} AND NOT m.is_group
        UNION ALL
        SELECT o.user_id, o.cost_center_id, true
        FROM {s}.cost_center_owner o JOIN {s}.app_user u USING (user_id)
        WHERE NOT u.disabled
        UNION ALL
        SELECT g.user_id, c.descendant_id, true
        FROM {s}.planner_grant g JOIN {s}.app_user u USING (user_id)
        JOIN {s}.closure c ON c.hier_id = {H_COST_CENTER} AND c.ancestor_id = g.member_id
        WHERE NOT u.disabled
        UNION ALL
        SELECT a.user_id, c.descendant_id, false
        FROM {s}.approver_assignment a JOIN {s}.app_user u USING (user_id)
        JOIN {s}.closure c ON c.hier_id = {H_COST_CENTER} AND c.ancestor_id = a.member_id
        WHERE NOT u.disabled
    """)

    # Row-level security (ADR 0005): forced, because the app role owns the table. Without a
    # user in the transaction's context, no row matches.
    scope = (
        "dim_cost_center = ANY (ARRAY(SELECT cost_center_id FROM {s}.cost_center_access "
        "WHERE user_id = nullif(current_setting('app.user_id', true), '')::int{write}))"
    )
    read = scope.format(s=s, write="")
    write = scope.format(s=s, write=" AND can_write")
    op.execute(f"ALTER TABLE {s}.fact_cost_center ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {s}.fact_cost_center FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY read ON {s}.fact_cost_center FOR SELECT USING ({read})")
    op.execute(f"CREATE POLICY add ON {s}.fact_cost_center FOR INSERT WITH CHECK ({write})")
    op.execute(
        f"CREATE POLICY change ON {s}.fact_cost_center FOR UPDATE "
        f"USING ({write}) WITH CHECK ({write})"
    )
    op.execute(f"CREATE POLICY remove ON {s}.fact_cost_center FOR DELETE USING ({write})")


def downgrade() -> None:
    s = migration_schema()
    for policy in ("read", "add", "change", "remove"):
        op.execute(f"DROP POLICY {policy} ON {s}.fact_cost_center")
    op.execute(f"ALTER TABLE {s}.fact_cost_center NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {s}.fact_cost_center DISABLE ROW LEVEL SECURITY")
    op.execute(f"DROP VIEW {s}.cost_center_access")
    op.execute(f"DROP VIEW {s}.cost_center_owner")
    op.execute(f"""
        ALTER TABLE {s}.approver_assignment
            ADD COLUMN user_name text,
            DROP CONSTRAINT fk_approver_assignment_user_id_app_user
    """)
    op.execute(f"""
        UPDATE {s}.approver_assignment a SET user_name = u.login
        FROM {s}.app_user u WHERE u.user_id = a.user_id
    """)
    op.execute(f"""
        ALTER TABLE {s}.approver_assignment
            DROP CONSTRAINT pk_approver_assignment,
            DROP COLUMN user_id,
            ALTER COLUMN user_name SET NOT NULL,
            ADD CONSTRAINT pk_approver_assignment PRIMARY KEY (member_id, user_name)
    """)
    for name in ("planner_grant", "owner_override", "user_session", "app_user"):
        op.drop_table(name, schema=S)
