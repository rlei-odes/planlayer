"""Positions excluded from rules of the round, by HR (ADR 0026): ``position_pay.excluded_rules``
holds rule ids; only HR changes it.

Revision ID: 0025
Revises: 0024
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

from planlayer.store.migrate import migration_schema

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _personnel_costs() -> ModuleType:
    path = Path(__file__).with_name("0019_personnel_costs.py")
    spec = importlib.util.spec_from_file_location("personnel_costs_0019", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    s = migration_schema()
    op.execute(
        f"ALTER TABLE {s}.position_pay ADD COLUMN excluded_rules integer[] NOT NULL DEFAULT '{{}}'"
    )
    _personnel_costs()._lock_position_pay(
        s, hr_edits_hr_salary=True, handover=True, exclusions=True
    )


def downgrade() -> None:
    s = migration_schema()
    _personnel_costs()._lock_position_pay(s, hr_edits_hr_salary=True, handover=True)
    op.execute(f"ALTER TABLE {s}.position_pay DROP COLUMN excluded_rules")
