"""HR's salary upload (ADR 0025): while the sync doesn't read salaries, HR downloads a template
of the round's people, fills in each one's monthly salary and yearly bonus and uploads it. A
check shows what the upload would do before anything is written; applying it writes the same.

An uploaded salary is HR's salary: on the round's positions with the person's number, and in HR's
staff list (``hr_pay``), from which positions taken later get it. A bonus belongs to the round's
positions only. Plan positions carry Planlayer's numbers and are never matched."""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from sqlalchemy import Connection, Engine, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.access import User
from planlayer.domain.workflow import EDITABLE
from planlayer.personnel.common import load_rules, pay_round, run
from planlayer.personnel.costs import recompute
from planlayer.personnel.salary_files import (
    FileRow,
    Format,
    SalaryFile,
    TemplateRow,
    read,
    template,
)
from planlayer.rounds import RoundRef, not_open
from planlayer.sheet import EditError
from planlayer.store.schema import (
    MODULE_PERSONNEL,
    hr_pay,
    hr_staff,
    installation,
    member,
    position_pay,
    workforce_position,
)
from planlayer.store.uow import unit_of_work
from planlayer.workflow import enabled_members, status_of

CHUNK = 1000

Outcome = Literal["changed", "unchanged", "blank", "not_in_round", "unknown", "failed"]


@dataclass
class UploadRow:
    line: int
    number: str | None
    name: str | None
    cost_center: str | None  # the position's, or the staff list's
    file_cost_center: str | None  # the file's, when it differs from ``cost_center``
    old: Decimal | None  # HR's salary now
    new: Decimal | None
    outcome: Outcome
    message: str | None = None
    old_bonus: Decimal | None = None  # the position's bonus now
    new_bonus: Decimal | None = None


@dataclass
class UploadResult:
    round_code: str
    applied: bool
    rows: list[UploadRow]
    ignored: list[str]  # the file's columns that aren't read
    positions: int  # positions whose HR salary or bonus changes
    staff_list: int  # people whose salary in HR's staff list changes
    batch_id: str | None = None


@dataclass
class UploadInfo:
    round_code: str
    round_status: str
    pay_from_source: bool
    people: int  # rows of the template
    with_salary: int  # the round's positions from HR that have HR's salary
    reason: str | None  # why HR can't upload now; None: HR can


# --- Who is in the template -----------------------------------------------------------------------


@dataclass
class _Position:
    position_id: int
    cost_center_id: int
    cost_center: str
    name: str | None
    title: str
    hr_salary: Decimal | None
    transferred: bool  # the sending half of a transfer
    bonus: Decimal | None = None
    staff_type_id: int = 0


@dataclass
class _Staff:
    cost_center_id: int
    cost_center: str
    name: str | None
    title: str


@dataclass
class _People:
    positions: dict[str, list[_Position]] = field(default_factory=dict)  # by number
    staff: dict[str, _Staff] = field(default_factory=dict)  # HR's staff list, by number
    staff_salary: dict[str, Decimal] = field(default_factory=dict)  # hr_pay, by number

    def current(self, number: str) -> _Position | None:
        """The position the person holds now: of a transfer, the receiving half."""
        ps = self.positions.get(number, [])
        return next((p for p in ps if not p.transferred), ps[0] if ps else None)


def _people(conn: Connection, version_id: int) -> _People:
    out = _People()
    p, pay, cc = workforce_position, position_pay, member
    for r in conn.execute(
        select(
            p.c.position_id,
            p.c.code,
            p.c.cost_center_id,
            cc.c.code.label("cc"),
            p.c.person_name,
            p.c.title,
            p.c.transferred_to_id,
            p.c.staff_type_id,
            pay.c.hr_salary,
            pay.c.bonus,
        )
        .join(cc, cc.c.member_id == p.c.cost_center_id)
        .outerjoin(pay, pay.c.position_id == p.c.position_id)
        .where(p.c.version_id == version_id, p.c.is_plan.is_(False))
        .order_by(p.c.position_id)
    ):
        out.positions.setdefault(r.code, []).append(
            _Position(
                r.position_id,
                r.cost_center_id,
                r.cc,
                r.person_name,
                r.title,
                r.hr_salary,
                r.transferred_to_id is not None,
                r.bonus,
                r.staff_type_id,
            )
        )
    for s in conn.execute(
        select(
            hr_staff.c.code,
            hr_staff.c.cost_center_id,
            cc.c.code.label("cc"),
            hr_staff.c.person_name,
            hr_staff.c.title,
        ).join(cc, cc.c.member_id == hr_staff.c.cost_center_id)
    ):
        out.staff[s.code] = _Staff(s.cost_center_id, s.cc, s.person_name, s.title)
    salaries = conn.execute(select(hr_pay.c.code, hr_pay.c.monthly_salary))
    out.staff_salary = {r.code: r.monthly_salary for r in salaries}
    return out


def _template_rows(people: _People) -> list[TemplateRow]:
    rows = {
        number: TemplateRow(number, s.name, s.title, s.cost_center)
        for number, s in people.staff.items()
    }
    for number in people.positions:
        p = people.current(number)
        assert p is not None
        rows[number] = TemplateRow(number, p.name, p.title, p.cost_center)
    return sorted(rows.values(), key=lambda r: (r.cost_center, r.number))


# --- Guards ---------------------------------------------------------------------------------------


def _from_source(conn: Connection) -> bool:
    return bool(conn.execute(select(installation.c.pay_from_source)).scalar_one())


def _refusal(conn: Connection, r: RoundRef) -> str | None:
    if _from_source(conn):
        return (
            "salaries come from the source; the admin turns this off under Master data, "
            "Sources, Staff (HR)"
        )
    if r.status not in ("setup", "open"):
        return not_open(r.status)
    return None


def info(engine: Engine, user: User, version_code: str) -> UploadInfo:
    user.require_hr()
    with unit_of_work(engine, user) as conn:
        r = pay_round(conn, version_code)
        people = _people(conn, r.version_id)
        return UploadInfo(
            r.code,
            r.status,
            _from_source(conn),
            len(_template_rows(people)),
            sum(
                1
                for ps in people.positions.values()
                for p in ps
                if p.hr_salary is not None and not p.transferred
            ),
            _refusal(conn, r),
        )


def template_file(engine: Engine, user: User, version_code: str, fmt: Format) -> bytes:
    """The round's people, one row each, the salary blank: no pay leaves in the template."""
    user.require_hr()
    with unit_of_work(engine, user) as conn:
        r = pay_round(conn, version_code)
        rows = _template_rows(_people(conn, r.version_id))
    return template(rows, fmt)


# --- Check and apply ------------------------------------------------------------------------------


@dataclass
class _Plan:
    rows: list[UploadRow]
    positions: dict[int, tuple[Decimal | None, Decimal | None]]  # id -> HR's salary, bonus
    touched: set[int]  # cost centers of those positions
    staff: dict[str, tuple[int, Decimal]]  # number -> cost center id, salary


def _plan(conn: Connection, r: RoundRef, upload: SalaryFile) -> _Plan:
    people = _people(conn, r.version_id)
    typed = {t for t, kind in load_rules(conn, r.version_id).staff_types.items() if kind.typed}
    enabled = enabled_members(conn, r.version_id)
    statuses: dict[int, str] = {}

    def lock(cc_id: int) -> str | None:
        if not enabled.get(cc_id, False):
            return "is not planned in the round"
        if cc_id not in statuses:
            statuses[cc_id] = status_of(conn, r.version_id, cc_id)
        status = statuses[cc_id]
        return None if status in EDITABLE else f"is {status.replace('_', ' ')}"

    lines: dict[str, list[int]] = {}
    for row in upload.rows:
        if row.number is not None:
            lines.setdefault(row.number, []).append(row.line)
    counts = Counter({n: len(ls) for n, ls in lines.items()})
    plan = _Plan([], {}, set(), {})
    for row in upload.rows:
        plan.rows.append(_row(row, people, counts, lines, lock, typed, plan))
    return plan


def _row(
    row: FileRow,
    people: _People,
    counts: Counter[str],
    lines: dict[str, list[int]],
    lock: Callable[[int], str | None],
    typed: set[int],
    plan: _Plan,
) -> UploadRow:
    """One row of the file: what happens to it, added to ``plan``."""
    number, salary, bonus = row.number, row.salary, row.bonus
    current, staff = (people.current(number), people.staff.get(number)) if number else (None, None)
    name: str | None = None
    cc: str | None = None
    old: Decimal | None = None
    if current is not None:
        name, cc, old = current.name, current.cost_center, current.hr_salary
    elif staff is not None:
        name, cc, old = staff.name, staff.cost_center, people.staff_salary.get(number or "")
    differs = row.cost_center if cc is not None and row.cost_center not in (None, cc) else None

    def out(outcome: Outcome, message: str | None = None) -> UploadRow:
        return UploadRow(
            row.line,
            number,
            name,
            cc,
            differs,
            old,
            salary,
            outcome,
            message,
            current.bonus if current else None,
            bonus,
        )

    if number is None:
        return out("failed", "no number")
    if counts[number] > 1:
        others = ", ".join(str(n) for n in lines[number] if n != row.line)
        return out("failed", f"the number is also on line {others}")
    if cc is None:
        return out("unknown", "number not found")
    if row.error is not None:
        return out("failed", row.error)
    if salary is None and bonus is None:
        return out("blank", "blank: kept as is")
    if current is None:
        if salary is None:
            return out("failed", "not in the round: no position for the bonus")
        result = out(
            "not_in_round",
            "saved to the staff list only" + ("; bonus skipped" if bonus is not None else ""),
        )
    else:
        ps = people.positions[number]
        if bonus is not None and any(p.staff_type_id in typed for p in ps):
            return out("failed", "typed amount: no bonus")
        moving = [
            p
            for p in ps
            if (salary is not None and p.hr_salary != salary)
            or (bonus is not None and p.bonus != bonus)
        ]
        locked = {p.cost_center: why for p in moving if (why := lock(p.cost_center_id))}
        applied = [p for p in moving if p.cost_center not in locked]
        if moving and not applied:
            text = "; ".join(f"{c} {why}" for c, why in sorted(locked.items()))
            return out("failed", f"{text}: locked")
        result = out("changed" if moving else "unchanged")
        for p in applied:
            plan.positions[p.position_id] = (
                salary if salary is not None else p.hr_salary,
                bonus if bonus is not None else p.bonus,
            )
            plan.touched.add(p.cost_center_id)
    if staff is not None and salary is not None and people.staff_salary.get(number) != salary:
        plan.staff[number] = (staff.cost_center_id, salary)
    return result


def _result(r: RoundRef, upload: SalaryFile, plan: _Plan, applied: bool) -> UploadResult:
    return UploadResult(
        r.code, applied, plan.rows, upload.ignored, len(plan.positions), len(plan.staff)
    )


def _start(conn: Connection, user: User, version_code: str) -> RoundRef:
    user.require_hr()
    r = pay_round(conn, version_code)
    reason = _refusal(conn, r)
    if reason is not None:
        raise EditError(reason)
    return r


def check(
    engine: Engine, user: User, version_code: str, data: bytes, filename: str
) -> UploadResult:
    """What the upload would do; writes nothing."""
    upload = read(data, filename)
    with unit_of_work(engine, user) as conn:
        r = _start(conn, user, version_code)
        return _result(r, upload, _plan(conn, r, upload), applied=False)


def apply(
    engine: Engine, user: User, version_code: str, data: bytes, filename: str
) -> UploadResult:
    """Write HR's salaries as the check shows them, and recompute the totals in the same change
    batch. Rows that fail are left out; the rest is written."""
    upload = read(data, filename)
    found: list[UploadResult] = []

    def work(conn: Connection) -> None:
        r = _start(conn, user, version_code)
        plan = _plan(conn, r, upload)
        ids = list(plan.positions)
        for i in range(0, len(ids), CHUNK):
            stmt = pg_insert(position_pay).values(
                [
                    {
                        "position_id": pid,
                        "version_id": r.version_id,
                        "module": MODULE_PERSONNEL,
                        "hr_salary": hr_salary,
                        "bonus": bonus,
                        "changed_by": user.login,
                    }
                    for pid in ids[i : i + CHUNK]
                    for hr_salary, bonus in [plan.positions[pid]]
                ]
            )
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=[position_pay.c.position_id],
                    set_={
                        "hr_salary": stmt.excluded.hr_salary,
                        "bonus": stmt.excluded.bonus,
                        "changed_by": user.login,
                        "changed_at": func.now(),
                    },
                )
            )
        numbers = list(plan.staff)
        for i in range(0, len(numbers), CHUNK):
            stmt = pg_insert(hr_pay).values(
                [
                    {"code": n, "cost_center_id": cc, "monthly_salary": salary}
                    for n in numbers[i : i + CHUNK]
                    for cc, salary in [plan.staff[n]]
                ]
            )
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=[hr_pay.c.code],
                    set_={
                        "cost_center_id": stmt.excluded.cost_center_id,
                        "monthly_salary": stmt.excluded.monthly_salary,
                    },
                )
            )
        if plan.touched:
            recompute(conn, r, user.login, by_hr=True, touched=plan.touched)
        found.append(_result(r, upload, plan, applied=True))

    batch = run(engine, user, work)
    result = found[0]
    result.batch_id = batch
    return result
