"""Pay encrypted at rest (ADR 0029): HR's salaries and the pay of positions become sealed
values (bytea), the salaries in the pay log are sealed the same way, and the login settings get
a sealed client secret. Existing pay is sealed with PLANLAYER_PAY_KEY, which this needs when
there is any; their checks for negative amounts move to the app.

Revision ID: 0033
Revises: 0032
"""

import importlib.util
import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from types import ModuleType

from alembic import op
from sqlalchemy import text

from planlayer import crypto
from planlayer.store.migrate import migration_schema

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# table: (key column, pay columns)
PAY = {
    "hr_pay": ("code", ("monthly_salary",)),
    "position_pay": ("position_id", ("hr_salary", "salary", "bonus")),
}
LOG_FIELDS = ("hr_salary", "salary", "bonus")


def _need_key(s: str) -> None:
    conn = op.get_bind()
    counts = [
        conn.execute(text(f"SELECT count(*) FROM {s}.{table}")).scalar_one()
        for table in (*PAY, "position_pay_log")
    ]
    if any(counts):
        try:
            crypto.seal("", "check")
        except crypto.PayKeyError:
            raise RuntimeError(
                "the app store holds pay: set PLANLAYER_PAY_KEY before this upgrade "
                "(planlayer new-pay-key makes one; keep it safe, it can't be recovered)"
            ) from None


def _personnel_costs() -> ModuleType:
    path = Path(__file__).with_name("0019_personnel_costs.py")
    spec = importlib.util.spec_from_file_location("personnel_costs_0019", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _every_row(s: str) -> Iterator[None]:
    """Every row of the pay tables, triggers off: the app role owns them, but forced row-level
    security would show it none (and rewriting pay must not trip the lock or the audit log)."""
    conn = op.get_bind()
    tables = (*PAY, "position_pay_log")
    forced = {
        t: conn.execute(
            text(f"SELECT relforcerowsecurity FROM pg_class WHERE oid = '{s}.{t}'::regclass")
        ).scalar_one()
        for t in tables
    }
    for t in tables:
        op.execute(f"ALTER TABLE {s}.{t} DISABLE TRIGGER USER")
        if forced[t]:
            op.execute(f"ALTER TABLE {s}.{t} NO FORCE ROW LEVEL SECURITY")
    yield
    for t in tables:
        op.execute(f"ALTER TABLE {s}.{t} ENABLE TRIGGER USER")
        if forced[t]:
            op.execute(f"ALTER TABLE {s}.{t} FORCE ROW LEVEL SECURITY")


def _hex(sealed: bytes) -> str:
    """A sealed value as row_to_json writes a bytea: how the log trigger records it."""
    return "\\x" + sealed.hex()


def upgrade() -> None:
    s = migration_schema()
    with _every_row(s):
        _need_key(s)
        _seal_all(s)
    # The lock keeps the stored HR's salary to compare: now sealed bytes.
    _personnel_costs()._lock_position_pay(
        s, hr_edits_hr_salary=True, handover=True, exclusions=True, salary_type="bytea"
    )
    op.execute(f"ALTER TABLE {s}.login_setting ADD COLUMN oidc_client_secret bytea")


def _seal_all(s: str) -> None:
    conn = op.get_bind()
    for table, (key, columns) in PAY.items():
        for c in columns:
            op.execute(f"ALTER TABLE {s}.{table} ADD COLUMN {c}_sealed bytea")
            rows = conn.execute(
                text(f"SELECT {key}, {c} FROM {s}.{table} WHERE {c} IS NOT NULL")
            ).all()
            if rows:
                conn.execute(
                    text(f"UPDATE {s}.{table} SET {c}_sealed = :v WHERE {key} = :k"),
                    [{"k": k, "v": crypto.seal(str(v), f"{table}.{c}")} for k, v in rows],
                )
            op.execute(f"ALTER TABLE {s}.{table} DROP COLUMN {c}")
            op.execute(f"ALTER TABLE {s}.{table} RENAME COLUMN {c}_sealed TO {c}")
    op.execute(f"ALTER TABLE {s}.hr_pay ALTER COLUMN monthly_salary SET NOT NULL")
    logged = conn.execute(
        text(
            f"SELECT id, old::text, new::text FROM {s}.position_pay_log "
            "WHERE old IS NOT NULL OR new IS NOT NULL"
        )
    ).all()
    changed = []
    for id_, *texts in logged:
        sealed = []
        # Amounts as Decimal, not through a float (T-12).
        for doc in (json.loads(x, parse_float=Decimal) if x else None for x in texts):
            if doc is not None:
                for f in LOG_FIELDS:
                    if doc.get(f) is not None:
                        doc[f] = _hex(crypto.seal(str(doc[f]), f"position_pay.{f}"))
            sealed.append(json.dumps(doc, default=str) if doc is not None else None)
        changed.append({"id": id_, "old": sealed[0], "new": sealed[1]})
    if changed:
        conn.execute(
            text(
                f"UPDATE {s}.position_pay_log SET old = CAST(:old AS jsonb), "
                "new = CAST(:new AS jsonb) WHERE id = :id"
            ),
            changed,
        )


def downgrade() -> None:
    s = migration_schema()
    op.execute(f"ALTER TABLE {s}.login_setting DROP COLUMN oidc_client_secret")
    with _every_row(s):
        _need_key(s)
        _open_all(s)
    _personnel_costs()._lock_position_pay(
        s, hr_edits_hr_salary=True, handover=True, exclusions=True
    )


def _open_all(s: str) -> None:
    conn = op.get_bind()
    for table, (key, columns) in PAY.items():
        for c in columns:
            op.execute(f"ALTER TABLE {s}.{table} ADD COLUMN {c}_plain numeric(18, 2)")
            rows = conn.execute(
                text(f"SELECT {key}, {c} FROM {s}.{table} WHERE {c} IS NOT NULL")
            ).all()
            if rows:
                conn.execute(
                    text(f"UPDATE {s}.{table} SET {c}_plain = :v WHERE {key} = :k"),
                    [
                        {"k": k, "v": Decimal(crypto.unseal(bytes(v), f"{table}.{c}"))}
                        for k, v in rows
                    ],
                )
            op.execute(f"ALTER TABLE {s}.{table} DROP COLUMN {c}")
            op.execute(f"ALTER TABLE {s}.{table} RENAME COLUMN {c}_plain TO {c}")
    op.execute(f"ALTER TABLE {s}.hr_pay ALTER COLUMN monthly_salary SET NOT NULL")
    op.execute(f"ALTER TABLE {s}.hr_pay ADD CONSTRAINT salary CHECK (monthly_salary >= 0)")
    op.execute(
        f"ALTER TABLE {s}.position_pay ADD CONSTRAINT amounts CHECK ("
        "coalesce(hr_salary, 0) >= 0 AND coalesce(salary, 0) >= 0 AND coalesce(bonus, 0) >= 0)"
    )
    logged = conn.execute(
        text(
            f"SELECT id, old::text, new::text FROM {s}.position_pay_log "
            "WHERE old IS NOT NULL OR new IS NOT NULL"
        )
    ).all()
    changed = []
    for id_, *texts in logged:
        plain = []
        for doc in (json.loads(x, parse_float=Decimal) if x else None for x in texts):
            if doc is not None:
                for f in LOG_FIELDS:
                    if isinstance(doc.get(f), str) and doc[f].startswith("\\x"):
                        # As text: the log is only kept, not read, and a JSON number would
                        # pass through a float.
                        doc[f] = crypto.unseal(bytes.fromhex(doc[f][2:]), f"position_pay.{f}")
            plain.append(json.dumps(doc, default=str) if doc is not None else None)
        changed.append({"id": id_, "old": plain[0], "new": plain[1]})
    if changed:
        conn.execute(
            text(
                f"UPDATE {s}.position_pay_log SET old = CAST(:old AS jsonb), "
                "new = CAST(:new AS jsonb) WHERE id = :id"
            ),
            changed,
        )
