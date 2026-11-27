"""Row-level security on the change logs of plan values, planned hours and the workforce: a
reader sees the changes of the cost centers whose values they read, with the same rule as the
values themselves (small groups of personnel costs hidden, ADR 0022). The audit triggers keep
writing every row. Undo reads its own batch with system rights, after its own checks.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

from alembic import op

from planlayer.store.migrate import migration_schema

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USER = "(NULLIF(current_setting('app.user_id', true), ''))::integer"


def _reads(s: str) -> dict[str, str]:
    cost_centers = (
        f"dim_cost_center = ANY (ARRAY(SELECT cost_center_id FROM {s}.cost_center_access "
        f"WHERE user_id = {USER}))"
    )
    return {
        "change_log": (
            f"{cost_centers} AND ("
            "(version_id, dim_cost_center, dim_account) NOT IN ("
            f"SELECT version_id, cost_center_id, account_id FROM {s}.pay_small_group) "
            "OR (version_id, dim_cost_center) IN ("
            f"SELECT version_id, cost_center_id FROM {s}.pay_access WHERE user_id = {USER}))"
        ),
        "capacity_change_log": (
            f"dim_cost_center = ANY (ARRAY(SELECT cost_center_id FROM {s}.capacity_access "
            f"WHERE user_id = {USER}))"
        ),
        "workforce_change_log": cost_centers,
    }


def upgrade() -> None:
    s = migration_schema()
    for table, rule in _reads(s).items():
        op.execute(f"ALTER TABLE {s}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {s}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY add ON {s}.{table} FOR INSERT WITH CHECK (true)")
        op.execute(f"CREATE POLICY read ON {s}.{table} FOR SELECT USING ({rule})")


def downgrade() -> None:
    s = migration_schema()
    for table in _reads(s):
        op.execute(f"DROP POLICY read ON {s}.{table}")
        op.execute(f"DROP POLICY add ON {s}.{table}")
        op.execute(f"ALTER TABLE {s}.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {s}.{table} DISABLE ROW LEVEL SECURITY")
