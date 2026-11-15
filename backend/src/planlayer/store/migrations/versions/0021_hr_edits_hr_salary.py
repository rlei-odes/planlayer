"""HR corrects HR's salary of a position (ADR 0023): the lock on pay lets HR change it, as well
as the system user that takes it from the staff list.

Revision ID: 0021
Revises: 0020
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from planlayer.store.migrate import migration_schema

revision: str = "0021"
down_revision: str | None = "0020"
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
    _personnel_costs()._lock_position_pay(migration_schema(), hr_edits_hr_salary=True)


def downgrade() -> None:
    _personnel_costs()._lock_position_pay(migration_schema(), hr_edits_hr_salary=False)
