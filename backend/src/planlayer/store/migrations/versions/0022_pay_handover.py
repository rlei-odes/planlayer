"""The order of personnel costs in a round (ADR 0024): HR releases access only once the round is
open, then hands each cost center's pay over to its readers, who edit pay only after that; HR
confirms only in an open round, and the review before approval can be switched off per round.

Revision ID: 0022
Revises: 0021
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic import op

from planlayer.store.migrate import migration_schema
from planlayer.store.schema import APP_SCHEMA

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = APP_SCHEMA
MODULE = "personnel_costs"
ACTOR = "nullif(current_setting('app.actor', true), '')"


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
        f"ALTER TABLE {s}.pay_setting ADD COLUMN review_required boolean NOT NULL DEFAULT true"
    )
    op.create_table(
        "pay_handover",
        sa.Column("version_id", sa.SmallInteger, nullable=False),
        sa.Column("module", sa.Text, nullable=False, server_default=MODULE),
        sa.Column(
            "cost_center_id", sa.Integer, sa.ForeignKey(f"{S}.member.member_id"), nullable=False
        ),
        sa.Column("handed_by", sa.Text, nullable=False),
        sa.Column(
            "handed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("version_id", "cost_center_id"),
        sa.ForeignKeyConstraint(
            ["version_id", "module"],
            [f"{S}.round_module.version_id", f"{S}.round_module.module"],
        ),
        sa.CheckConstraint(f"module = '{MODULE}'", name="module"),
        schema=S,
    )
    # HR acts in an open round: releasing access, handing pay over, confirming it. Taking a
    # release or a handover back, and removing them with the module, stay possible.
    op.execute(f"""
        CREATE FUNCTION {s}.guard_pay_step() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            st text;
        BEGIN
            IF EXISTS (SELECT 1 FROM {s}.app_user u
                       WHERE u.user_id = nullif(current_setting('app.user_id', true), '')::int
                         AND u.is_system) THEN
                RETURN NULL;
            END IF;
            IF NOT {s}.writes_as_hr() THEN
                RAISE EXCEPTION 'plan values are locked: only HR does this'
                    USING ERRCODE = 'PL001';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN NULL;
            END IF;
            SELECT status INTO st FROM {s}.planning_round WHERE version_id = NEW.version_id;
            IF st <> 'open' THEN
                RAISE EXCEPTION 'plan values are locked: the controller opens the round first'
                    USING ERRCODE = 'PL001';
            END IF;
            IF TG_TABLE_NAME = 'pay_handover' AND NOT EXISTS (
                    SELECT 1 FROM {s}.pay_release x WHERE x.version_id = NEW.version_id) THEN
                RAISE EXCEPTION 'plan values are locked: HR releases access control first'
                    USING ERRCODE = 'PL001';
            END IF;
            RETURN NULL;
        END $$
    """)
    for table in ("pay_release", "pay_handover", "pay_review"):
        op.execute(f"""
            CREATE TRIGGER step AFTER INSERT OR UPDATE OR DELETE ON {s}.{table}
            FOR EACH ROW EXECUTE FUNCTION {s}.guard_pay_step()
        """)
    op.execute(f"""
        CREATE FUNCTION {s}.log_pay_handover() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            k {s}.pay_handover;
        BEGIN
            IF TG_OP = 'DELETE' THEN k := OLD; ELSE k := NEW; END IF;
            INSERT INTO {s}.pay_access_log (actor, action, version_id, cost_center_id)
            VALUES ({ACTOR}, CASE WHEN TG_OP = 'DELETE' THEN 'handover_withdrawn'
                                  ELSE 'handed_over' END,
                    k.version_id, k.cost_center_id);
            RETURN NULL;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER log AFTER INSERT OR DELETE ON {s}.pay_handover
        FOR EACH ROW EXECUTE FUNCTION {s}.log_pay_handover()
    """)
    _personnel_costs()._lock_position_pay(s, hr_edits_hr_salary=True, handover=True)


def downgrade() -> None:
    s = migration_schema()
    _personnel_costs()._lock_position_pay(s, hr_edits_hr_salary=True)
    for table in ("pay_release", "pay_handover", "pay_review"):
        op.execute(f"DROP TRIGGER step ON {s}.{table}")
    op.drop_table("pay_handover", schema=S)
    op.execute(f"DROP FUNCTION {s}.guard_pay_step()")
    op.execute(f"DROP FUNCTION {s}.log_pay_handover()")
    op.execute(f"ALTER TABLE {s}.pay_setting DROP COLUMN review_required")
