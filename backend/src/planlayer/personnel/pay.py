"""Pay per position and the personnel costs page (ADR 0022): what HR and the cost center's owner
read and write, HR's review, and the cost centers they read pay of."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Connection, Engine, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.access import AccessError, User, pay_cost_centers
from planlayer.domain.personnel import PayPosition, costs
from planlayer.personnel.common import (
    StaffTypePay,
    editable,
    load_rules,
    pay_accounts,
    pay_context,
    pay_round,
    run,
)
from planlayer.personnel.costs import recompute, small_groups
from planlayer.rounds import NotFoundError, RoundRef, not_open
from planlayer.sheet import EditError
from planlayer.store.schema import (
    AMOUNT,
    BASE_LAYER,
    COST_CENTER,
    MODULE_PERSONNEL,
    fact_cost_center,
    hr_pay,
    member,
    pay_handover,
    pay_review,
    position_pay,
    workforce_position,
)
from planlayer.store.uow import system_rights, unit_of_work
from planlayer.workflow import enabled_members, status_of

CENT = Decimal("0.01")


@dataclass
class PayRow:
    position_id: int
    code: str
    title: str
    person_name: str | None
    staff_type: str
    staff_category: str
    entry_date: date | None
    leave_date: date | None
    contract_percent: Decimal | None
    is_plan: bool
    typed: bool | None  # None: the staff type isn't costed in the round
    hr_salary: Decimal | None
    salary: Decimal | None
    bonus: Decimal | None
    charged_to: str | None
    year_cost: Decimal  # on every account, wherever charged
    transferred_to: str | None


@dataclass
class AccountTotal:
    code: str
    name: str
    months: list[Decimal | None]
    small: int | None  # positions, when a small group


@dataclass
class Review:
    confirmed_by: str
    confirmed_at: datetime


@dataclass
class PayPage:
    round_code: str
    round_name: str
    round_status: str
    fiscal_year: int
    cost_center_code: str
    cost_center_name: str
    status: str
    lock_reason: str | None
    periods: list[date]
    closed_months: int
    min_group: int
    positions: list[PayRow]
    accounts: list[AccountTotal]
    review: Review | None
    may_review: bool
    may_charge: bool
    charge_targets: list[tuple[str, str]] = field(default_factory=list)  # code, name
    # The order of the round (ADR 0024): access released, pay handed over, review switched on.
    released: bool = False
    handover: Review | None = None  # who handed it over, and when
    review_required: bool = True


def _code_names(conn: Connection, ids: set[int]) -> dict[int, tuple[str, str]]:
    if not ids:
        return {}
    return {
        r.member_id: (r.code, r.name)
        for r in conn.execute(
            select(member.c.member_id, member.c.code, member.c.name).where(
                member.c.member_id.in_(ids)
            )
        )
    }


def read_page(engine: Engine, user: User, version_code: str, cc_code: str) -> PayPage:
    with unit_of_work(engine, user) as conn:
        ctx = pay_context(conn, user, version_code, cc_code)
        rules = load_rules(conn, ctx.version_id)
        p, pay = workforce_position, position_pay
        rows = conn.execute(
            select(p, pay.c.hr_salary, pay.c.salary, pay.c.bonus, pay.c.charged_to_id)
            .outerjoin(pay, pay.c.position_id == p.c.position_id)
            .where(p.c.version_id == ctx.version_id, p.c.cost_center_id == ctx.cost_center_id)
            .order_by(p.c.is_plan, p.c.title, p.c.person_name, p.c.position_id)
        ).all()
        names = _code_names(
            conn,
            {r.staff_type_id for r in rows}
            | {r.staff_category_id for r in rows}
            | {r.charged_to_id for r in rows if r.charged_to_id}
            | {r.transferred_to_id for r in rows if r.transferred_to_id},
        )
        positions = []
        for r in rows:
            kind: StaffTypePay | None = rules.staff_types.get(r.staff_type_id)
            salary = r.salary if r.salary is not None else (r.hr_salary or Decimal(0))
            year = Decimal(0)
            if kind is not None:
                c = costs(
                    PayPosition(
                        0, kind.account_id, kind.typed, salary, r.entry_date, r.leave_date, r.bonus
                    ),
                    rules.rules,
                    ctx.periods,
                )
                year = sum((v for months in c.values() for v in months), Decimal(0))
            positions.append(
                PayRow(
                    r.position_id,
                    r.code,
                    r.title,
                    r.person_name,
                    names[r.staff_type_id][1],
                    names[r.staff_category_id][1],
                    r.entry_date,
                    r.leave_date,
                    r.contract_percent,
                    r.is_plan,
                    None if kind is None else kind.typed,
                    r.hr_salary,
                    r.salary,
                    r.bonus,
                    names[r.charged_to_id][0] if r.charged_to_id else None,
                    year.quantize(CENT),
                    names[r.transferred_to_id][0] if r.transferred_to_id else None,
                )
            )
        accounts = pay_accounts(conn, ctx.version_id)
        f = fact_cost_center
        index = {d: i for i, d in enumerate(ctx.periods)}
        by_account: dict[int, list[Decimal | None]] = {}
        # HR reads pay, not cost plans: the totals are read with the system user's rights.
        with system_rights(conn):
            totals_rows = conn.execute(
                select(f.c.dim_account, f.c.period, f.c.amount).where(
                    f.c.version_id == ctx.version_id,
                    f.c.layer_id == BASE_LAYER,
                    f.c.measure_id == AMOUNT,
                    f.c.dim_cost_center == ctx.cost_center_id,
                    f.c.dim_account.in_(accounts),
                    f.c.period.in_(ctx.periods),
                )
            ).all()
        for a, period, amount in totals_rows:
            by_account.setdefault(a, [None] * len(ctx.periods))[index[period]] = amount
        small = dict(small_groups(conn, ctx.version_id).get(ctx.cost_center_id, []))
        account_names = _code_names(conn, set(by_account) | set(small))
        totals = [
            AccountTotal(
                *account_names[a],
                by_account.get(a, [None] * len(ctx.periods)),
                small.get(a),
            )
            for a in sorted(set(by_account) | set(small), key=lambda a: account_names[a][0])
        ]
        review = conn.execute(
            select(pay_review.c.confirmed_by, pay_review.c.confirmed_at).where(
                pay_review.c.version_id == ctx.version_id,
                pay_review.c.cost_center_id == ctx.cost_center_id,
            )
        ).first()
        from planlayer.personnel.rights import released as round_released

        is_released = round_released(conn, ctx.version_id)
        handover = conn.execute(
            select(pay_handover.c.handed_by, pay_handover.c.handed_at).where(
                pay_handover.c.version_id == ctx.version_id,
                pay_handover.c.cost_center_id == ctx.cost_center_id,
            )
        ).first()
        targets: list[tuple[str, str]] = []
        if user.is_hr:
            enabled = [m for m, on in enabled_members(conn, ctx.version_id).items() if on]
            targets = sorted(_code_names(conn, set(enabled)).values())
    r_ = ctx.round
    return PayPage(
        r_.code,
        r_.name,
        r_.status,
        r_.fiscal_year,
        ctx.cost_center_code,
        ctx.cost_center_name,
        ctx.status,
        ctx.lock_reason,
        ctx.periods,
        ctx.closed,
        rules.min_group,
        positions,
        totals,
        Review(review.confirmed_by, review.confirmed_at) if review else None,
        may_review=user.is_hr and r_.status == "open",
        may_charge=user.is_hr and ctx.lock_reason is None,
        charge_targets=targets,
        released=is_released,
        handover=Review(handover.handed_by, handover.handed_at) if handover else None,
        review_required=rules.review_required,
    )


@dataclass
class PayUnit:
    code: str
    name: str
    status: str
    positions: int
    small_groups: int
    confirmed: bool
    handed_over: bool = False


def units(engine: Engine, user: User, version_code: str) -> tuple[bool, list[PayUnit]]:
    """Whether HR has released the round's pay, and the cost centers planned in it whose pay the
    user reads (none before the release, unless HR)."""
    from planlayer.personnel.rights import released

    with unit_of_work(engine, user) as conn:
        r = pay_round(conn, version_code)
        is_released = released(conn, r.version_id)
        mine = pay_cost_centers(conn, user, r.version_id)
        planned = [m for m, on in enabled_members(conn, r.version_id).items() if on and m in mine]
        names = _code_names(conn, set(planned))
        counts: dict[int, int] = dict(
            conn.execute(
                select(workforce_position.c.cost_center_id, func.count())
                .where(workforce_position.c.version_id == r.version_id)
                .group_by(workforce_position.c.cost_center_id)
            ).all()
        )
        small = small_groups(conn, r.version_id)
        handed = set(
            conn.execute(
                select(pay_handover.c.cost_center_id).where(
                    pay_handover.c.version_id == r.version_id
                )
            ).scalars()
        )
        confirmed = set(
            conn.execute(
                select(pay_review.c.cost_center_id).where(pay_review.c.version_id == r.version_id)
            ).scalars()
        )
        out = [
            PayUnit(
                *names[m],
                status_of(conn, r.version_id, m),
                counts.get(m, 0),
                len(small.get(m, [])),
                m in confirmed,
                m in handed,
            )
            for m in planned
        ]
    return is_released, sorted(out, key=lambda u: u.code)


@dataclass
class PayIn:
    salary: Decimal | None  # None: HR's
    bonus: Decimal | None
    charged_to: str | None = None  # a cost center's code; HR only
    hr_salary: Decimal | None = None  # HR only, and only with ``hr_salary_given``
    hr_salary_given: bool = False


def _amount(value: Decimal | None, what: str) -> Decimal | None:
    if value is None:
        return None
    if value < 0:
        raise EditError(f"a {what} isn't negative")
    if value != value.quantize(CENT):
        raise EditError(f"a {what} has at most two decimal places")
    return value


def save_pay(
    engine: Engine, user: User, version_code: str, cc_code: str, position_id: int, given: PayIn
) -> str:
    """Change a position's planned salary, bonus and (HR) the cost center charged; recompute the
    round's totals in the same change batch."""

    def work(conn: Connection) -> None:
        ctx = pay_context(conn, user, version_code, cc_code)
        editable(ctx)
        found = conn.execute(
            select(
                workforce_position.c.position_id,
                position_pay.c.charged_to_id,
                position_pay.c.hr_salary,
            )
            .outerjoin(position_pay, position_pay.c.position_id == workforce_position.c.position_id)
            .where(
                workforce_position.c.position_id == position_id,
                workforce_position.c.version_id == ctx.version_id,
                workforce_position.c.cost_center_id == ctx.cost_center_id,
            )
        ).first()
        if found is None:
            raise NotFoundError(f"position {position_id} not found on {cc_code}")
        charged = found.charged_to_id
        if user.is_hr:
            charged = _charge_target(conn, ctx.round, ctx.cost_center_id, given.charged_to)
        elif given.charged_to is not None and charged != _charge_target(
            conn, ctx.round, ctx.cost_center_id, given.charged_to
        ):
            raise AccessError("only HR charges a position to another cost center")
        hr_salary = found.hr_salary
        if given.hr_salary_given:
            # HR corrects HR's salary (ADR 0023); nobody else changes it.
            if not user.is_hr:
                raise AccessError("only HR changes HR's salary")
            hr_salary = _amount(given.hr_salary, "salary")
        values = {
            "hr_salary": hr_salary,
            "salary": _amount(given.salary, "salary"),
            "bonus": _amount(given.bonus, "bonus"),
            "charged_to_id": charged,
            "changed_by": user.login,
            "changed_at": func.now(),
        }
        stmt = pg_insert(position_pay).values(
            position_id=position_id, version_id=ctx.version_id, module=MODULE_PERSONNEL, **values
        )
        conn.execute(
            stmt.on_conflict_do_update(index_elements=[position_pay.c.position_id], set_=values)
        )
        recompute(conn, ctx.round, user.login, by_hr=user.is_hr, touched={ctx.cost_center_id})

    return run(engine, user, work)


def _charge_target(conn: Connection, r: RoundRef, own: int, code: str | None) -> int | None:
    """The cost center a position is charged to: None for its own."""
    if code is None:
        return None
    cc = conn.execute(
        select(member.c.member_id).where(
            member.c.dim_id == COST_CENTER, member.c.code == code, member.c.is_group.is_(False)
        )
    ).scalar()
    if cc is None:
        raise NotFoundError(f"cost center {code} not found")
    if cc == own:
        return None
    if not enabled_members(conn, r.version_id).get(cc, False):
        raise EditError(f"{code} is not planned in the round")
    return int(cc)


def confirm(engine: Engine, user: User, version_code: str, cc_code: str, on: bool) -> None:
    """HR confirms a cost center's pay, or takes the confirmation back."""
    user.require_hr()
    with unit_of_work(engine, user) as conn:
        ctx = pay_context(conn, user, version_code, cc_code)
        if ctx.round.status != "open":
            raise EditError(not_open(ctx.round.status))
        where = (
            pay_review.c.version_id == ctx.version_id,
            pay_review.c.cost_center_id == ctx.cost_center_id,
        )
        conn.execute(delete(pay_review).where(*where))
        if on:
            conn.execute(
                pg_insert(pay_review).values(
                    version_id=ctx.version_id,
                    cost_center_id=ctx.cost_center_id,
                    confirmed_by=user.login,
                )
            )


def needs_review(conn: Connection, version_id: int, cc_ids: list[int]) -> set[int]:
    """Of ``cc_ids``, those with positions in a personnel costs round and no confirmation by
    HR: they can't be approved yet (ADR 0022), unless HR switched the review off for the round
    (ADR 0024). Read with the system user's rights, as the approver may not read the
    positions."""
    from planlayer.personnel.common import load_rules, module_on

    if not cc_ids or not module_on(conn, version_id):
        return set()
    if not load_rules(conn, version_id).review_required:
        return set()
    with system_rights(conn):
        with_positions = set(
            conn.execute(
                select(workforce_position.c.cost_center_id)
                .where(
                    workforce_position.c.version_id == version_id,
                    workforce_position.c.cost_center_id.in_(cc_ids),
                )
                .distinct()
            ).scalars()
        )
    confirmed = set(
        conn.execute(
            select(pay_review.c.cost_center_id).where(pay_review.c.version_id == version_id)
        ).scalars()
    )
    return with_positions - confirmed


def take_hr_salaries(conn: Connection, r: RoundRef, position_ids: list[int], actor: str) -> None:
    """New positions taken from HR get HR's salary from the staff list (ADR 0022), copied with
    the system user's rights: whoever takes the staff may not read pay."""
    from planlayer.personnel.common import module_on

    if not position_ids or not module_on(conn, r.version_id):
        return
    with system_rights(conn, actor=actor):
        rows = conn.execute(
            select(workforce_position.c.position_id, hr_pay.c.monthly_salary)
            .join(hr_pay, hr_pay.c.code == workforce_position.c.code)
            .where(workforce_position.c.position_id.in_(position_ids))
        ).all()
        if rows:
            conn.execute(
                pg_insert(position_pay)
                .values(
                    [
                        {
                            "position_id": pid,
                            "version_id": r.version_id,
                            "module": MODULE_PERSONNEL,
                            "hr_salary": s,
                            "changed_by": actor,
                        }
                        for pid, s in rows
                    ]
                )
                .on_conflict_do_nothing()
            )


def continue_pay(conn: Connection, old_id: int, new_id: int) -> None:
    """The receiving half of a transfer during the year keeps the person's salary (ADR 0021,
    ADR 0022). The yearly bonus stays with the sending half, where it was planned, until
    someone moves it. Runs with the system user's rights of the transfer."""
    old = conn.execute(select(position_pay).where(position_pay.c.position_id == old_id)).first()
    if old is None:
        return
    conn.execute(
        pg_insert(position_pay).values(
            position_id=new_id,
            version_id=old.version_id,
            module=MODULE_PERSONNEL,
            hr_salary=old.hr_salary,
            salary=old.salary,
            changed_by=old.changed_by,
        )
    )


def hand_over(engine: Engine, user: User, version_code: str, cc_code: str, on: bool) -> None:
    """HR hands a cost center's pay over to its readers, who edit it from then on, or takes the
    handover back (ADR 0024). Only in an open round whose access HR has released."""
    from planlayer.personnel.rights import released

    user.require_hr()
    with unit_of_work(engine, user) as conn:
        ctx = pay_context(conn, user, version_code, cc_code)
        where = (
            pay_handover.c.version_id == ctx.version_id,
            pay_handover.c.cost_center_id == ctx.cost_center_id,
        )
        if not on:
            conn.execute(delete(pay_handover).where(*where))
            return
        if ctx.round.status != "open":
            raise EditError("the controller opens the round first; then HR hands pay over")
        if not released(conn, ctx.version_id):
            raise EditError("release access control first; then hand pay over")
        conn.execute(
            pg_insert(pay_handover)
            .values(
                version_id=ctx.version_id, cost_center_id=ctx.cost_center_id, handed_by=user.login
            )
            .on_conflict_do_nothing()
        )
