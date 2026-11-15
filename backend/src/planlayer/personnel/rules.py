"""The round's personnel cost rules (ADR 0022): accounts per staff type, yearly extras, raises,
long-service awards, the bonus account, contributions and the minimum group size; turning the
module on and off, and carrying it into a new round."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal, cast

from sqlalchemy import Connection, Engine, delete, func, insert, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.access import AccessError, User
from planlayer.personnel import rights
from planlayer.personnel.common import (
    DEFAULT_MIN_GROUP,
    module_on,
    pay_accounts,
    run,
)
from planlayer.personnel.costs import clear, recompute
from planlayer.rounds import RoundError, RoundRef, fiscal_periods, get_round
from planlayer.sheet import EditError
from planlayer.store.schema import (
    ACCOUNT,
    ENTITY,
    MODULE_PERSONNEL,
    NO_ENTITY,
    STAFF_TYPE,
    credit_account,
    fact_cost_center,
    forwarding_account,
    hr_pay,
    member,
    pay_owner_seen,
    pay_reader,
    pay_release,
    pay_review,
    pay_rule,
    pay_rule_step,
    pay_setting,
    pay_small_group,
    pay_staff_type,
    position_pay,
    round_module,
    workforce_position,
)
from planlayer.store.uow import system_rights, unit_of_work
from planlayer.workflow import enabled_members

Kind = Literal["extra", "raise", "award", "contribution"]
KINDS: tuple[Kind, ...] = ("extra", "raise", "award", "contribution")


@dataclass
class StepIO:
    years: int
    salaries: Decimal | None = None
    amount: Decimal | None = None


@dataclass
class RuleIO:
    kind: Kind
    name: str
    account: str
    entity: str | None = None
    salaries: Decimal | None = None  # extra
    percent: Decimal | None = None  # raise, contribution
    ceiling: Decimal | None = None  # contribution
    starts: date | None = None  # raise
    steps: list[StepIO] = field(default_factory=list)  # award


@dataclass
class StaffTypeIO:
    code: str
    account: str | None  # None: not costed
    typed: bool
    name: str = ""
    external: bool = False


@dataclass
class RulesIO:
    min_group: int
    bonus_account: str | None
    staff_types: list[StaffTypeIO]
    rules: list[RuleIO]


@dataclass
class RulesPage:
    round_code: str
    round_status: str
    fiscal_year: int
    workforce_positions: bool
    on: bool
    may_edit: bool
    periods: list[date]
    rules: RulesIO
    accounts: list[tuple[str, str]]  # code, name: leaf accounts a rule may write
    entities: list[tuple[str, str]]


def _may_read(user: User) -> None:
    if not (user.is_hr or user.is_controller):
        raise AccessError("only HR and controllers see the personnel cost rules")


def _codes(conn: Connection, dim: int) -> dict[str, int]:
    return dict(
        conn.execute(
            select(member.c.code, member.c.member_id).where(
                member.c.dim_id == dim, member.c.is_group.is_(False)
            )
        ).all()
    )


def _current(conn: Connection, version_id: int) -> RulesIO:
    names = {
        m.member_id: m
        for m in conn.execute(
            select(member.c.member_id, member.c.code, member.c.name, member.c.external).where(
                member.c.dim_id.in_([ACCOUNT, ENTITY, STAFF_TYPE])
            )
        )
    }
    setting = conn.execute(
        select(pay_setting).where(pay_setting.c.version_id == version_id)
    ).first()
    chosen = {
        r.staff_type_id: r
        for r in conn.execute(
            select(pay_staff_type).where(pay_staff_type.c.version_id == version_id)
        )
    }
    staff_types = [
        StaffTypeIO(
            m.code,
            names[chosen[m.member_id].account_id].code if m.member_id in chosen else None,
            chosen[m.member_id].typed if m.member_id in chosen else bool(m.external),
            m.name,
            bool(m.external),
        )
        for m in sorted((m for m in names.values() if m.external is not None), key=lambda m: m.code)
    ]
    rows = list(
        conn.execute(
            select(pay_rule).where(pay_rule.c.version_id == version_id).order_by(pay_rule.c.rule_id)
        )
    )
    steps: dict[int, list[StepIO]] = {}
    for s in conn.execute(
        select(pay_rule_step)
        .where(pay_rule_step.c.rule_id.in_([r.rule_id for r in rows]))
        .order_by(pay_rule_step.c.years)
    ):
        steps.setdefault(s.rule_id, []).append(StepIO(s.years, s.salaries, s.amount))
    return RulesIO(
        setting.min_group if setting else DEFAULT_MIN_GROUP,
        names[setting.bonus_account_id].code if setting and setting.bonus_account_id else None,
        staff_types,
        [
            RuleIO(
                cast(Kind, r.kind),
                r.name,
                names[r.account_id].code,
                names[r.entity_id].code if r.entity_id else None,
                r.salaries,
                r.percent,
                r.ceiling,
                r.starts,
                steps.get(r.rule_id, []),
            )
            for r in rows
        ],
    )


def _not_writable(conn: Connection, version_id: int) -> set[int]:
    """Accounts written by another calculation: capacity's credits, the round's forwarding."""
    return set(conn.execute(select(credit_account.c.account_id)).scalars()) | set(
        conn.execute(
            select(forwarding_account.c.account_id).where(
                forwarding_account.c.version_id == version_id
            )
        ).scalars()
    )


def read_rules(engine: Engine, user: User, version_code: str) -> RulesPage:
    _may_read(user)
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, version_code)
        taken = _not_writable(conn, r.version_id)
        accounts = [
            (a.code, a.name)
            for a in conn.execute(
                select(member.c.member_id, member.c.code, member.c.name)
                .where(member.c.dim_id == ACCOUNT, member.c.is_group.is_(False))
                .order_by(member.c.code)
            )
            if a.member_id not in taken
        ]
        entities = [
            (e.code, e.name)
            for e in conn.execute(
                select(member.c.code, member.c.name)
                .where(
                    member.c.dim_id == ENTITY,
                    member.c.is_group.is_(False),
                    member.c.code != NO_ENTITY,
                )
                .order_by(member.c.code)
            )
        ]
        from planlayer.workforce import mode_of

        page = RulesPage(
            r.code,
            r.status,
            r.fiscal_year,
            mode_of(conn, r.version_id) == "positions",
            module_on(conn, r.version_id),
            r.status in ("setup", "open"),
            fiscal_periods(conn, r.fiscal_year),
            _current(conn, r.version_id),
            accounts,
            entities,
        )
    return page


def _positive(value: Decimal | None, what: str, name: str) -> Decimal:
    if value is None or value <= 0:
        raise EditError(f"{name}: {what} must be above 0")
    return value


def _rows(
    conn: Connection, r: RoundRef, given: RulesIO
) -> tuple[
    dict[str, object], list[dict[str, object]], list[tuple[dict[str, object], list[StepIO]]]
]:
    """The setting, staff type rows and rule rows to store, checked."""
    accounts, entities, types = (
        _codes(conn, ACCOUNT),
        _codes(conn, ENTITY),
        _codes(conn, STAFF_TYPE),
    )
    taken = _not_writable(conn, r.version_id)
    periods = fiscal_periods(conn, r.fiscal_year)

    def account(code: str | None, what: str) -> int:
        if code is None or code not in accounts:
            raise EditError(f"{what}: account {code} not found")
        if accounts[code] in taken:
            raise EditError(f"{what}: account {code} is written by capacity or cost forwarding")
        return accounts[code]

    if given.min_group < 1:
        raise EditError("the minimum group size is at least 1")
    setting: dict[str, object] = {
        "min_group": given.min_group,
        "bonus_account_id": account(given.bonus_account, "bonus") if given.bonus_account else None,
    }
    staff: list[dict[str, object]] = []
    for t in given.staff_types:
        if t.code not in types:
            raise EditError(f"staff type {t.code} not found")
        if t.account is not None:
            staff.append(
                {
                    "staff_type_id": types[t.code],
                    "account_id": account(t.account, t.code),
                    "typed": t.typed,
                }
            )
    rules: list[tuple[dict[str, object], list[StepIO]]] = []
    for g in given.rules:
        name = g.name.strip()
        if not name:
            raise EditError("a rule needs a name")
        if g.kind not in KINDS:
            raise EditError(f"{name}: unknown kind {g.kind}")
        if g.entity is not None and g.entity not in entities:
            raise EditError(f"{name}: entity {g.entity} not found")
        row: dict[str, object] = {
            "kind": g.kind,
            "name": name,
            "account_id": account(g.account, name),
            "entity_id": entities[g.entity] if g.entity else None,
            "salaries": None,
            "percent": None,
            "ceiling": None,
            "starts": None,
        }
        steps: list[StepIO] = []
        match g.kind:
            case "extra":
                row["salaries"] = _positive(g.salaries, "the salaries per year", name)
            case "raise":
                row["percent"] = _positive(g.percent, "the percentage", name)
                if g.starts not in periods:
                    raise EditError(f"{name}: a raise starts in a month of the fiscal year")
                row["starts"] = g.starts
            case "contribution":
                row["percent"] = _positive(g.percent, "the rate", name)
                if g.ceiling is not None:
                    row["ceiling"] = _positive(g.ceiling, "the ceiling", name)
            case "award":
                years = [s.years for s in g.steps]
                if not years or len(set(years)) != len(years) or min(years) < 1:
                    raise EditError(f"{name}: steps need distinct years of service above 0")
                for s in g.steps:
                    if (s.salaries is None) == (s.amount is None):
                        raise EditError(f"{name}: a step pays salaries or an amount")
                    _positive(s.salaries if s.salaries is not None else s.amount, "a step", name)
                steps = g.steps
        rules.append((row, steps))
    return setting, staff, rules


def save_rules(engine: Engine, user: User, version_code: str, given: RulesIO) -> str:
    """Replace the round's rules as a whole, and recompute every cost center's totals. Values on
    accounts that stop being personnel accounts go first. A controller's change withdraws every
    cost center's review; HR's doesn't."""
    _may_read(user)

    def work(conn: Connection) -> None:
        r = get_round(conn, version_code, lock="share")
        if not module_on(conn, r.version_id):
            raise RoundError(f"{version_code} has no personnel costs")
        if r.status not in ("setup", "open"):
            raise RoundError(f"the planning round is {r.status}")
        before = _current(conn, r.version_id)
        setting, staff, rules = _rows(conn, r, given)
        old = pay_accounts(conn, r.version_id)
        new = (
            {cast(int, s["account_id"]) for s in staff}
            | {cast(int, row["account_id"]) for row, _ in rules}
            | ({cast(int, setting["bonus_account_id"])} if setting["bonus_account_id"] else set())
        )
        clear(conn, r, old - new)
        vid = r.version_id
        conn.execute(delete(pay_rule).where(pay_rule.c.version_id == vid))
        conn.execute(delete(pay_staff_type).where(pay_staff_type.c.version_id == vid))
        values = setting | {"changed_by": user.login, "changed_at": func.now()}
        stmt = pg_insert(pay_setting).values(version_id=vid, module=MODULE_PERSONNEL, **values)
        conn.execute(
            stmt.on_conflict_do_update(index_elements=[pay_setting.c.version_id], set_=values)
        )
        if staff:
            conn.execute(
                insert(pay_staff_type),
                [{"version_id": vid, "module": MODULE_PERSONNEL, **s} for s in staff],
            )
        for row, steps in rules:
            rule_id = conn.execute(
                insert(pay_rule)
                .values(version_id=vid, module=MODULE_PERSONNEL, changed_by=user.login, **row)
                .returning(pay_rule.c.rule_id)
            ).scalar_one()
            if steps:
                conn.execute(
                    insert(pay_rule_step),
                    [
                        {
                            "rule_id": rule_id,
                            "years": s.years,
                            "salaries": s.salaries,
                            "amount": s.amount,
                        }
                        for s in steps
                    ],
                )
        changed = _current(conn, vid) != before
        touched = (
            {m for m, on in enabled_members(conn, vid).items() if on}
            if changed and not user.is_hr
            else set()
        )
        recompute(conn, r, user.login, by_hr=user.is_hr, touched=touched)

    return run(engine, user, work)


def has_values(conn: Connection, r: RoundRef) -> bool:
    accounts = pay_accounts(conn, r.version_id)
    if not accounts:
        return False
    with system_rights(conn):
        found = conn.execute(
            select(func.count())
            .select_from(fact_cost_center)
            .where(
                fact_cost_center.c.version_id == r.version_id,
                fact_cost_center.c.dim_account.in_(accounts),
            )
        ).scalar_one()
    return bool(found)


def switch(conn: Connection, r: RoundRef, user: User, *, on: bool) -> None:
    """Turn personnel costs on (needs positions) or off, as part of the round's setup (ADR 0022).
    Off removes the rules, the positions' pay and the values they wrote; in an open round that
    is refused once values exist."""
    if module_on(conn, r.version_id) == on:
        return
    user.require_controller()
    if r.status not in ("setup", "open"):
        raise RoundError(f"the planning round is {r.status}")
    vid = r.version_id
    if on:
        from planlayer.workforce import mode_of

        if mode_of(conn, vid) != "positions":
            raise RoundError("personnel costs need workforce planning by positions")
        conn.execute(insert(round_module).values(version_id=vid, module=MODULE_PERSONNEL))
        conn.execute(
            insert(pay_setting).values(
                version_id=vid, module=MODULE_PERSONNEL, changed_by=user.login
            )
        )
        rights.seed(conn, r, user.login)
        return
    if r.status != "setup" and has_values(conn, r):
        raise RoundError(
            "the round has personnel cost values; personnel costs can be turned off only "
            "in setup, where they are cleared"
        )
    clear(conn, r, pay_accounts(conn, vid))
    with system_rights(conn, actor=user.login):
        conn.execute(delete(position_pay).where(position_pay.c.version_id == vid))
        for rights_table in (pay_reader, pay_owner_seen, pay_release):
            conn.execute(delete(rights_table).where(rights_table.c.version_id == vid))
    for table in (pay_rule, pay_staff_type, pay_setting, pay_small_group, pay_review):
        conn.execute(delete(table).where(table.c.version_id == vid))
    conn.execute(
        delete(round_module).where(
            round_module.c.version_id == vid, round_module.c.module == MODULE_PERSONNEL
        )
    )


def carry(conn: Connection, source: RoundRef, target: RoundRef, actor: str) -> None:
    """A new round's rules and its positions' pay, from the round it is set up like: raises
    move to the same month of the new year; positions are matched by number and cost center,
    and take HR's latest salary. Runs in the round's creating transaction, after the
    workforce."""
    if not (module_on(conn, source.version_id) and module_on(conn, target.version_id)):
        return
    shift = target.fiscal_year - source.fiscal_year
    s, t = source.version_id, target.version_id
    setting = conn.execute(select(pay_setting).where(pay_setting.c.version_id == s)).first()
    conn.execute(
        pg_insert(pay_setting)
        .values(
            version_id=t,
            module=MODULE_PERSONNEL,
            min_group=setting.min_group if setting else DEFAULT_MIN_GROUP,
            bonus_account_id=setting.bonus_account_id if setting else None,
            changed_by=actor,
        )
        .on_conflict_do_nothing()
    )
    conn.execute(
        insert(pay_staff_type).from_select(
            ["version_id", "module", "staff_type_id", "account_id", "typed"],
            select(
                literal(t, pay_staff_type.c.version_id.type),
                pay_staff_type.c.module,
                pay_staff_type.c.staff_type_id,
                pay_staff_type.c.account_id,
                pay_staff_type.c.typed,
            ).where(pay_staff_type.c.version_id == s),
        )
    )
    for rule in conn.execute(select(pay_rule).where(pay_rule.c.version_id == s)).all():
        starts = rule.starts.replace(year=rule.starts.year + shift) if rule.starts else None
        new_id = conn.execute(
            insert(pay_rule)
            .values(
                version_id=t,
                module=MODULE_PERSONNEL,
                kind=rule.kind,
                name=rule.name,
                account_id=rule.account_id,
                entity_id=rule.entity_id,
                salaries=rule.salaries,
                percent=rule.percent,
                ceiling=rule.ceiling,
                starts=starts,
                changed_by=actor,
            )
            .returning(pay_rule.c.rule_id)
        ).scalar_one()
        steps = conn.execute(
            select(pay_rule_step).where(pay_rule_step.c.rule_id == rule.rule_id)
        ).all()
        if steps:
            conn.execute(
                insert(pay_rule_step),
                [
                    {
                        "rule_id": new_id,
                        "years": x.years,
                        "salaries": x.salaries,
                        "amount": x.amount,
                    }
                    for x in steps
                ],
            )
    with system_rights(conn, actor=actor):
        old_p, new_p = workforce_position.alias("old_p"), workforce_position.alias("new_p")
        planned = {m for m, on in enabled_members(conn, t).items() if on}
        rows = conn.execute(
            select(
                new_p.c.position_id,
                position_pay.c.hr_salary,
                position_pay.c.salary,
                position_pay.c.bonus,
                position_pay.c.charged_to_id,
                hr_pay.c.monthly_salary,
            )
            .join(position_pay, position_pay.c.position_id == old_p.c.position_id)
            .join(
                new_p,
                (new_p.c.code == old_p.c.code)
                & (new_p.c.cost_center_id == old_p.c.cost_center_id)
                & (new_p.c.version_id == t),
            )
            .outerjoin(hr_pay, hr_pay.c.code == old_p.c.code)
            .where(old_p.c.version_id == s, old_p.c.transferred_to_id.is_(None))
        ).all()
        if rows:
            conn.execute(
                insert(position_pay),
                [
                    {
                        "position_id": x.position_id,
                        "version_id": t,
                        "module": MODULE_PERSONNEL,
                        "hr_salary": x.monthly_salary
                        if x.monthly_salary is not None
                        else x.hr_salary,
                        "salary": x.salary,
                        "bonus": x.bonus,
                        "charged_to_id": x.charged_to_id if x.charged_to_id in planned else None,
                        "changed_by": actor,
                    }
                    for x in rows
                ],
            )
    rights.carry(conn, source, target, actor)
    recompute(conn, target, actor, by_hr=False)
