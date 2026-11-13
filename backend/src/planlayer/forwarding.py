"""Cost forwarding (ADR 0018): a round's forwardings, the page that edits them, and the charges
and credits they write as plan values.

A forwarding passes costs from a source cost center to receivers: all of the source's planned
costs (``full``), a fixed amount (``fixed``), or a rate per unit times each receiver's quantity
(``rate``). Receivers are charged on the forwarding's charge account, the source credited on its
credit account. Saving recomputes every forwarding of the round; a planner's edit on a full
forwarding's source recomputes the forwardings downstream of it, as the system user, in the
planner's change batch.
"""

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import cast

from sqlalchemy import Connection, Engine, and_, delete, func, insert, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from planlayer.access import SYSTEM_LOGIN, AccessError, User
from planlayer.domain import forwarding as rules
from planlayer.domain.periods import closed_months
from planlayer.domain.workflow import RoundStatus, Status
from planlayer.planning import EditError, LockedError, _locked
from planlayer.rounds import NotFoundError, RoundRef, fiscal_periods, get_round
from planlayer.store.schema import (
    ACCOUNT,
    AMOUNT,
    BASE_LAYER,
    COST_CENTER,
    ENTITY,
    H_ACCOUNT,
    H_COST_CENTER,
    NO_ENTITY,
    app_user,
    credit_account,
    fact_cost_center,
    forwarding,
    forwarding_account,
    forwarding_receiver,
    hier_edge,
    member,
)
from planlayer.store.uow import unit_of_work
from planlayer.sync import rebuild_closure
from planlayer.workflow import enabled_members, status_of

MONTHS = 12


@dataclass
class ReceiverIn:
    cost_center_code: str
    quantity: Decimal


@dataclass
class AccountIn:
    code: str
    name: str  # used when the account is created


@dataclass
class ForwardingIn:
    code: str
    name: str
    method: rules.Method
    source_code: str
    group_code: str  # where new accounts go
    charge_account: AccountIn
    credit_account: AccountIn
    receivers: list[ReceiverIn]
    shares: rules.Shares | None = None
    amount: Decimal | None = None
    rate: Decimal | None = None
    unit: str | None = None


@dataclass
class ReceiverOut:
    cost_center_code: str
    cost_center_name: str
    quantity: Decimal
    percent: Decimal | None  # of what the forwarding passes on in the year
    year: Decimal | None  # charged in the round, as stored
    status: Status


@dataclass
class ForwardingOut:
    code: str
    name: str
    method: rules.Method
    source_code: str
    source_name: str
    shares: rules.Shares | None
    amount: Decimal | None
    rate: Decimal | None
    unit: str | None
    group_code: str | None  # the charge account's group
    charge_account: AccountIn
    credit_account: AccountIn
    receivers: list[ReceiverOut]
    passed_on: Decimal | None  # credited to the source in the round
    changed_by: str | None


@dataclass
class SourceOut:
    """A source cost center's costs in the round, what its forwardings pass on, and the rest:
    for rate forwardings, the over- or under-coverage."""

    cost_center_code: str
    cost_center_name: str
    costs: Decimal | None
    other_credits: Decimal | None  # revenue and capacity credits
    passed_on: Decimal | None
    remaining: Decimal | None


@dataclass
class GroupOut:
    code: str
    name: str
    depth: int


@dataclass
class TreeNode:
    code: str
    name: str
    depth: int
    is_group: bool


@dataclass
class ForwardingPage:
    version_code: str
    version_name: str
    fiscal_year: int
    round_status: RoundStatus
    lock_reason: str | None  # None: the user may change forwardings
    closed_months: int
    forwardings: list[ForwardingOut] = field(default_factory=list)
    sources: list[SourceOut] = field(default_factory=list)
    cost_centers: list[tuple[str, str]] = field(default_factory=list)  # enabled in the round
    groups: list[GroupOut] = field(default_factory=list)
    cost_center_tree: list["TreeNode"] = field(default_factory=list)


@dataclass
class _Def:
    forwarding_id: int
    code: str
    name: str
    method: rules.Method
    source_id: int
    shares: rules.Shares | None
    amount: Decimal | None
    rate: Decimal | None
    unit: str | None
    charge_id: int
    credit_id: int
    receivers: dict[int, Decimal]
    changed_by: str | None = None

    def rule(self, enabled: dict[int, bool]) -> rules.Forwarding[int]:
        return rules.Forwarding(
            self.method,
            self.source_id,
            {k: q for k, q in self.receivers.items() if enabled.get(k, False)},
            self.shares,
            self.amount,
            self.rate,
        )


def _definitions(conn: Connection, version_id: int) -> dict[int, _Def]:
    f, fr = forwarding, forwarding_receiver
    defs = {
        r.forwarding_id: _Def(
            r.forwarding_id,
            r.code,
            r.name,
            r.method,
            r.source_id,
            r.shares,
            r.amount,
            r.rate,
            r.unit,
            r.charge_account_id,
            r.credit_account_id,
            {},
            r.changed_by,
        )
        for r in conn.execute(select(f).where(f.c.version_id == version_id).order_by(f.c.code))
    }
    for fid, cc, q in conn.execute(
        select(fr.c.forwarding_id, fr.c.cost_center_id, fr.c.quantity).where(
            fr.c.forwarding_id.in_(list(defs))
        )
    ):
        defs[fid].receivers[cc] = q
    return defs


def forwarded_accounts(conn: Connection, version_id: int) -> dict[int, tuple[str, int]]:
    """The round's forwarding accounts: per account, its side and forwarding."""
    return {
        a: (side, fid)
        for a, side, fid in conn.execute(
            select(
                forwarding_account.c.account_id,
                forwarding_account.c.side,
                forwarding_account.c.forwarding_id,
            ).where(forwarding_account.c.version_id == version_id)
        )
    }


# --- Computing ----------------------------------------------------------------------------------


def _order(defs: dict[int, _Def]) -> list[int]:
    return rules.order(
        {f: d.source_id for f, d in defs.items()},
        {f for f, d in defs.items() if d.method == "full"},
        {f: set(d.receivers) for f, d in defs.items()},
    )


def recompute(conn: Connection, r: RoundRef, only: set[int] | None = None) -> None:
    """Write the charges and credits of the round's forwardings (``only`` these, if given), in
    the order they run. Each forwarding's cells on its two accounts are replaced with what it
    computes: blank where it passes nothing on. A forecast's closed months are left alone."""
    defs = _definitions(conn, r.version_id)
    if not defs:
        return
    try:
        sequence = _order(defs)
    except rules.CycleError as e:
        codes = [defs[cast(int, f)].code for f in e.circle]
        raise EditError(
            "forwardings depend on each other in a circle: " + " → ".join(codes)
        ) from None
    if only is not None:
        sequence = [f for f in sequence if f in only]
    periods = fiscal_periods(conn, r.fiscal_year)
    closed = closed_months(periods, r.actuals_through)
    enabled = enabled_members(conn, r.version_id)
    credits = {d.credit_id for d in defs.values()} | set(
        conn.execute(select(credit_account.c.account_id)).scalars()
    )
    ids = {d.source_id for d in defs.values()} | {k for d in defs.values() for k in d.receivers}
    entities = _entities(conn, ids)
    for fid in sequence:
        d = defs[fid]
        wanted: dict[tuple[int, int, date], Decimal] = {}
        if enabled.get(d.source_id, False):
            base = (
                _balance(conn, r.version_id, d.source_id, periods, credits, {d.credit_id}).left()
                if d.method == "full"
                else None
            )
            charged = rules.charges(d.rule(enabled), base)
            credit = [Decimal(0)] * MONTHS
            for cc, months in charged.items():
                for m in range(closed, MONTHS):
                    if months[m]:
                        wanted[(cc, d.charge_id, periods[m])] = months[m]
                        credit[m] += months[m]
            for m in range(closed, MONTHS):
                if credit[m]:
                    wanted[(d.source_id, d.credit_id, periods[m])] = credit[m]
        _replace(conn, r.version_id, (d.charge_id, d.credit_id), periods[closed:], wanted, entities)


def _entities(conn: Connection, cost_centers: Iterable[int]) -> dict[int, int]:
    """Each cost center's entity member."""
    cc, ent = member.alias("cc"), member.alias("ent")
    return dict(
        conn.execute(
            select(cc.c.member_id, ent.c.member_id)
            .join(
                ent,
                and_(
                    ent.c.dim_id == ENTITY,
                    ent.c.code == func.coalesce(cc.c.entity_code, NO_ENTITY),
                ),
            )
            .where(cc.c.member_id.in_(set(cost_centers)))
        ).all()
    )


@dataclass
class _Balance:
    """A source's plan values per month: costs (charges received included), and what reduces
    them: revenue, capacity credits and forwarding credits."""

    costs: list[Decimal | None]
    reductions: list[Decimal | None]

    def left(self) -> list[Decimal | None]:
        """What is left on the source: what a full forwarding passes on."""
        return [
            None if c is None and r is None else (c or Decimal(0)) - (r or Decimal(0))
            for c, r in zip(self.costs, self.reductions, strict=True)
        ]


def _balance(
    conn: Connection,
    version_id: int,
    cc_id: int,
    periods: list[date],
    credits: set[int],
    leave_out: set[int] | None = None,
) -> _Balance:
    """The source's balance per month. ``credits``: credit accounts (capacity and forwarding);
    ``leave_out``: accounts not counted at all (a full forwarding's own credit)."""
    f = fact_cost_center
    reducing = or_(f.c.dim_account.in_(credits or {-1}), member.c.account_type == "revenue").label(
        "reducing"
    )
    out = _Balance([None] * MONTHS, [None] * MONTHS)
    index = {p: i for i, p in enumerate(periods)}
    for period, reduces, amount in conn.execute(
        select(f.c.period, reducing, func.sum(f.c.amount))
        .join(member, member.c.member_id == f.c.dim_account)
        .where(
            f.c.version_id == version_id,
            f.c.layer_id == BASE_LAYER,
            f.c.measure_id == AMOUNT,
            f.c.dim_cost_center == cc_id,
            f.c.period.in_(periods),
            f.c.dim_account.not_in(leave_out or {-1}),
        )
        .group_by(f.c.period, reducing)
    ):
        (out.reductions if reduces else out.costs)[index[period]] = amount
    return out


def _replace(
    conn: Connection,
    version_id: int,
    accounts: tuple[int, int],
    periods: list[date],
    wanted: dict[tuple[int, int, date], Decimal],
    entities: dict[int, int],
) -> None:
    f = fact_cost_center
    scope = and_(
        f.c.version_id == version_id,
        f.c.layer_id == BASE_LAYER,
        f.c.measure_id == AMOUNT,
        f.c.dim_account.in_(accounts),
        f.c.period.in_(periods),
    )
    stored = {
        (cc, a, p): v
        for cc, a, p, v in conn.execute(
            select(f.c.dim_cost_center, f.c.dim_account, f.c.period, f.c.amount)
            .where(scope)
            .with_for_update()
        )
    }
    gone = [k for k in stored if k not in wanted]
    if gone:
        conn.execute(
            delete(f).where(
                scope, tuple_(f.c.dim_cost_center, f.c.dim_account, f.c.period).in_(gone)
            )
        )
    changed = [
        {
            "version_id": version_id,
            "layer_id": BASE_LAYER,
            "measure_id": AMOUNT,
            "period": p,
            "dim_entity": entities[cc],
            "dim_cost_center": cc,
            "dim_account": a,
            "amount": v,
        }
        for (cc, a, p), v in wanted.items()
        if stored.get((cc, a, p)) != v
    ]
    if changed:
        stmt = pg_insert(f).values(changed)
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=list(f.primary_key.columns),
                set_={"amount": stmt.excluded.amount, "row_version": f.c.row_version + 1},
            )
        )


@contextmanager
def _as_system(conn: Connection) -> Iterator[None]:
    """Write as the system user for a while, in the same transaction and change batch: the
    forwarding a planner's edit sets off writes into cost centers the planner may not write."""
    before = {
        name: conn.execute(select(func.current_setting(name, True))).scalar()
        for name in ("app.user_id", "app.actor")
    }
    system_id = conn.execute(
        select(app_user.c.user_id).where(app_user.c.login == SYSTEM_LOGIN)
    ).scalar_one()
    for name, value in (("app.user_id", str(system_id)), ("app.actor", SYSTEM_LOGIN)):
        conn.execute(select(func.set_config(name, value, True)))
    try:
        yield
    finally:
        for name, old in before.items():
            conn.execute(select(func.set_config(name, old or "", True)))


def follow(conn: Connection, r: RoundRef, cost_center_id: int) -> None:
    """Recompute the full forwardings fed by the cost center's plan values, and those
    downstream of them, after its values changed (ADR 0018)."""
    defs = _definitions(conn, r.version_id)
    full = {f for f, d in defs.items() if d.method == "full"}
    if not any(defs[f].source_id == cost_center_id for f in full):
        return
    affected = rules.downstream(
        {f: d.source_id for f, d in defs.items()},
        full,
        {f: set(d.receivers) for f, d in defs.items()},
        cost_center_id,
    )
    with _as_system(conn):
        recompute(conn, r, affected)


# --- Reading ------------------------------------------------------------------------------------


def read_page(engine: Engine, user: User, version_code: str) -> ForwardingPage:
    if not (user.is_controller or user.is_viewer):
        raise AccessError("only controllers and viewers see cost forwarding")
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, version_code)
        defs = _definitions(conn, r.version_id)
        periods = fiscal_periods(conn, r.fiscal_year)
        enabled = enabled_members(conn, r.version_id)
        ids = (
            {d.source_id for d in defs.values()}
            | {k for d in defs.values() for k in d.receivers}
            | {d.charge_id for d in defs.values()}
            | {d.credit_id for d in defs.values()}
        )
        names = {
            m.member_id: (m.code, m.name)
            for m in conn.execute(
                select(member.c.member_id, member.c.code, member.c.name).where(
                    member.c.member_id.in_(ids)
                )
            )
        }
        parents = dict(
            conn.execute(
                select(hier_edge.c.member_id, member.c.code)
                .join(member, member.c.member_id == hier_edge.c.parent_id)
                .where(
                    hier_edge.c.hier_id == H_ACCOUNT,
                    hier_edge.c.member_id.in_({d.charge_id for d in defs.values()}),
                )
            ).all()
        )
        totals = _totals(conn, r.version_id, defs, periods)
        own = {d.credit_id for d in defs.values()}
        capacity = set(conn.execute(select(credit_account.c.account_id)).scalars())
        sources: list[SourceOut] = []
        for cc in sorted({d.source_id for d in defs.values()}, key=lambda c: names[c][0]):
            # Forwarding credits apart: they are what is passed on.
            balance = _balance(conn, r.version_id, cc, periods, capacity, own)
            costs, other = _sum(balance.costs), _sum(balance.reductions)
            passed = _sum(totals.get((cc, d.credit_id)) for d in defs.values() if d.source_id == cc)
            parts = (costs, other, passed)
            remaining = (
                None
                if all(v is None for v in parts)
                else (costs or Decimal(0)) - (other or Decimal(0)) - (passed or Decimal(0))
            )
            sources.append(SourceOut(*names[cc], costs, other, passed, remaining))
        statuses = {
            cc: status_of(conn, r.version_id, cc) for d in defs.values() for cc in d.receivers
        }
        cost_centers = [
            (c, n)
            for mid, c, n in conn.execute(
                select(member.c.member_id, member.c.code, member.c.name)
                .where(member.c.dim_id == COST_CENTER, member.c.is_group.is_(False))
                .order_by(member.c.code)
            )
            if enabled.get(mid, False)
        ]
        groups = _groups(conn)
        tree = _cost_center_tree(conn, enabled)

    if r.status not in ("setup", "open"):
        reason: str | None = f"the planning round is {r.status}"
    elif not user.is_controller:
        reason = "only controllers change cost forwarding"
    else:
        reason = None
    page = ForwardingPage(
        r.code,
        r.name,
        r.fiscal_year,
        r.status,
        reason,
        closed_months(periods, r.actuals_through),
        sources=sources,
        cost_centers=cost_centers,
        groups=groups,
        cost_center_tree=tree,
    )
    for d in defs.values():
        weights = sum((q for q in d.receivers.values()), Decimal(0))
        receivers = []
        for cc, q in sorted(d.receivers.items(), key=lambda kv: names[kv[0]][0]):
            if d.method == "rate":
                percent = None
            elif d.shares == "percent":
                percent = q
            else:
                percent = (q * 100 / weights).quantize(Decimal("0.01")) if weights else None
            receivers.append(
                ReceiverOut(*names[cc], q, percent, totals.get((cc, d.charge_id)), statuses[cc])
            )
        page.forwardings.append(
            ForwardingOut(
                d.code,
                d.name,
                d.method,
                *names[d.source_id],
                d.shares,
                d.amount,
                d.rate,
                d.unit,
                parents.get(d.charge_id),
                AccountIn(*names[d.charge_id]),
                AccountIn(*names[d.credit_id]),
                receivers,
                totals.get((d.source_id, d.credit_id)),
                d.changed_by,
            )
        )
    return page


def _totals(
    conn: Connection, version_id: int, defs: dict[int, _Def], periods: list[date]
) -> dict[tuple[int, int], Decimal]:
    """Year totals per cost center x forwarding account, as stored in the round."""
    f = fact_cost_center
    accounts = {d.charge_id for d in defs.values()} | {d.credit_id for d in defs.values()}
    return {
        (cc, a): v
        for cc, a, v in conn.execute(
            select(f.c.dim_cost_center, f.c.dim_account, func.sum(f.c.amount))
            .where(
                f.c.version_id == version_id,
                f.c.layer_id == BASE_LAYER,
                f.c.measure_id == AMOUNT,
                f.c.dim_account.in_(accounts),
                f.c.period.in_(periods),
            )
            .group_by(f.c.dim_cost_center, f.c.dim_account)
        )
    }


def _sum(values: Iterable[Decimal | None]) -> Decimal | None:
    present = [v for v in values if v is not None]
    return sum(present, Decimal(0)) if present else None


def _groups(conn: Connection) -> list[GroupOut]:
    """The account groups in hierarchy order, with their depth."""
    return [
        GroupOut(code, name, depth)
        for code, name, depth, is_group in _tree(conn, H_ACCOUNT, ACCOUNT)
        if is_group
    ]


def _cost_center_tree(conn: Connection, enabled: dict[int, bool]) -> list[TreeNode]:
    """The cost center hierarchy down to the cost centers planned in the round, for choosing
    receivers: nodes without any of them are left out."""
    rows = _tree(conn, H_COST_CENTER, COST_CENTER, keep=lambda mid: enabled.get(mid, False))
    return [TreeNode(code, name, depth, is_group) for code, name, depth, is_group in rows]


def _tree(
    conn: Connection, hier_id: int, dim: int, keep: Callable[[int], bool] | None = None
) -> list[tuple[str, str, int, bool]]:
    """A hierarchy in order: (code, name, depth, is group). With ``keep``, only the leaves it
    keeps and the groups above them."""
    edges = conn.execute(
        select(
            member.c.member_id,
            member.c.code,
            member.c.name,
            member.c.is_group,
            hier_edge.c.parent_id,
        )
        .join(hier_edge, hier_edge.c.member_id == member.c.member_id)
        .where(hier_edge.c.hier_id == hier_id, member.c.dim_id == dim)
        .order_by(hier_edge.c.sort_order, member.c.code)
    ).all()
    children: dict[int | None, list[tuple[int, str, str, bool]]] = defaultdict(list)
    for mid, code, name, is_group, parent in edges:
        children[parent].append((mid, code, name, is_group))

    def walk(parent: int | None, depth: int) -> list[tuple[str, str, int, bool]]:
        out: list[tuple[str, str, int, bool]] = []
        for mid, code, name, is_group in children.get(parent, []):
            below = walk(mid, depth + 1)
            if is_group and (keep is None or below):
                out += [(code, name, depth, True), *below]
            elif not is_group and (keep is None or keep(mid)):
                out.append((code, name, depth, False))
        return out

    return walk(None, 0)


# --- Writing ------------------------------------------------------------------------------------


def _run(engine: Engine, actor: User, work: Callable[[Connection], None]) -> str:
    actor.require_controller()
    batch_id = uuid.uuid4().hex
    try:
        with unit_of_work(engine, actor, batch_id) as conn:
            work(conn)
    except DBAPIError as e:
        locked = _locked(e)
        if locked is None:
            raise
        raise locked from None
    return batch_id


def _writable(conn: Connection, version_code: str) -> RoundRef:
    r = get_round(conn, version_code)
    if r.status not in ("setup", "open"):
        raise LockedError(f"the planning round is {r.status}")
    return r


def _cost_center(conn: Connection, code: str, enabled: dict[int, bool]) -> int:
    found = conn.execute(
        select(member.c.member_id).where(
            member.c.dim_id == COST_CENTER, member.c.code == code, member.c.is_group.is_(False)
        )
    ).scalar()
    if found is None:
        raise NotFoundError(f"cost center {code} not found")
    if not enabled.get(found, False):
        raise EditError(f"{code} is not planned in this round")
    return int(found)


def _account(conn: Connection, r: RoundRef, given: AccountIn, group_id: int, mine: set[int]) -> int:
    """The forwarding account with the given code: an existing leaf account free for it, or a
    new manual account in the group. ``mine``: the accounts the forwarding has now."""
    code, name = given.code.strip(), given.name.strip()
    if not code:
        raise EditError("a forwarding account needs a code")
    found = conn.execute(
        select(member.c.member_id, member.c.is_group).where(
            member.c.dim_id == ACCOUNT, member.c.code == code
        )
    ).first()
    if found is None:
        if not name:
            raise EditError(f"a new account {code} needs a name")
        account_id = int(
            conn.execute(
                insert(member)
                .values(
                    dim_id=ACCOUNT,
                    code=code,
                    name=name,
                    is_group=False,
                    origin="manual",
                    account_type="cost",
                )
                .returning(member.c.member_id)
            ).scalar_one()
        )
        sort = conn.execute(
            select(func.coalesce(func.max(hier_edge.c.sort_order), 0) + 1).where(
                hier_edge.c.hier_id == H_ACCOUNT, hier_edge.c.parent_id == group_id
            )
        ).scalar_one()
        conn.execute(
            insert(hier_edge).values(
                hier_id=H_ACCOUNT,
                member_id=account_id,
                parent_id=group_id,
                sort_order=sort,
                origin="manual",
            )
        )
        rebuild_closure(conn)
        return account_id
    account_id = int(found.member_id)
    if found.is_group:
        raise EditError(f"{code} is an account group")
    if account_id in mine:
        return account_id
    if account_id in forwarded_accounts(conn, r.version_id):
        raise EditError(f"{code} is used by another forwarding")
    if account_id in set(conn.execute(select(credit_account.c.account_id)).scalars()):
        raise EditError(f"{code} is credited by capacity planning")
    f = fact_cost_center
    if conn.execute(
        select(f.c.dim_account)
        .where(f.c.version_id == r.version_id, f.c.dim_account == account_id)
        .limit(1)
    ).first():
        raise EditError(f"{code} holds plan values in this round")
    return account_id


def _check_round(
    conn: Connection,
    defs: dict[str, _Def],
    existing: _Def | None,
    code: str,
    method: rules.Method,
    source_id: int,
    receivers: set[int],
) -> None:
    """The round's forwardings with this one as saved: no receiver by shares passes costs on."""
    others = {c: d for c, d in defs.items() if d is not existing}
    round_: dict[str, tuple[rules.Method, int, set[int]]] = {
        c: (d.method, d.source_id, set(d.receivers)) for c, d in others.items()
    }
    round_[code] = (method, source_id, receivers)
    codes = dict(
        conn.execute(
            select(member.c.member_id, member.c.code).where(
                member.c.member_id.in_(
                    {source_id, *receivers, *(d.source_id for d in others.values())}
                )
            )
        ).all()
    )
    named = {
        c: (m, codes.get(s, str(s)), {codes.get(r, str(r)) for r in rs})
        for c, (m, s, rs) in round_.items()
    }
    try:
        rules.check_round(named)
    except rules.ForwardingError as e:
        raise EditError(str(e)) from None


def _clear(conn: Connection, version_id: int, accounts: Iterable[int]) -> None:
    """Remove a forwarding's values from its accounts (while they still are its accounts)."""
    f = fact_cost_center
    conn.execute(
        delete(f).where(
            f.c.version_id == version_id,
            f.c.layer_id == BASE_LAYER,
            f.c.measure_id == AMOUNT,
            f.c.dim_account.in_(list(accounts)),
        )
    )


def save(
    engine: Engine, actor: User, version_code: str, given: ForwardingIn, original: str | None
) -> str:
    """Create a forwarding, or change the one coded ``original``, and recompute the round's
    forwardings, in one change batch. Returns the batch."""

    def work(conn: Connection) -> None:
        r = _writable(conn, version_code)
        code, name = given.code.strip(), given.name.strip()
        if not code or not name:
            raise EditError("a forwarding needs a code and a name")
        if given.method not in ("full", "fixed", "rate"):
            raise EditError(f"unknown method {given.method}")
        defs = {d.code: d for d in _definitions(conn, r.version_id).values()}
        existing = None
        if original is not None:
            existing = defs.get(original)
            if existing is None:
                raise NotFoundError(f"forwarding {original} not found")
        if code in defs and defs[code] is not existing:
            raise EditError(f"a forwarding {code} exists in this round")
        enabled = enabled_members(conn, r.version_id)
        source_id = _cost_center(conn, given.source_code, enabled)
        receivers: dict[int, Decimal] = {}
        for rc in given.receivers:
            cc = _cost_center(conn, rc.cost_center_code, enabled)
            if cc in receivers:
                raise EditError(f"{rc.cost_center_code} is listed twice")
            receivers[cc] = rc.quantity
        method = given.method
        rule = rules.Forwarding(
            method,
            source_id,
            receivers,
            given.shares if method != "rate" else None,
            given.amount if method == "fixed" else None,
            given.rate if method == "rate" else None,
        )
        try:
            rules.check(rule)
        except rules.ForwardingError as e:
            raise EditError(str(e)) from None
        _check_round(conn, defs, existing, code, method, source_id, set(receivers))
        for value, places, label in (
            (rule.amount, 2, "an amount"),
            (rule.rate, 4, "a rate"),
            *((q, 4, "a share or quantity") for q in receivers.values()),
        ):
            exponent = value.as_tuple().exponent if value is not None else 0
            if isinstance(exponent, int) and exponent < -places:
                raise EditError(f"{label} has at most {places} decimal places")
        group_id = conn.execute(
            select(member.c.member_id).where(
                member.c.dim_id == ACCOUNT,
                member.c.code == given.group_code,
                member.c.is_group.is_(True),
            )
        ).scalar()
        if group_id is None:
            raise NotFoundError(f"account group {given.group_code} not found")
        mine = {existing.charge_id, existing.credit_id} if existing else set()
        charge_id = _account(conn, r, given.charge_account, group_id, mine)
        credit_id = _account(conn, r, given.credit_account, group_id, mine)
        if charge_id == credit_id:
            raise EditError("charge and credit need two different accounts")
        values = {
            "code": code,
            "name": name,
            "method": method,
            "source_id": source_id,
            "shares": rule.shares,
            "amount": rule.amount,
            "rate": rule.rate,
            "unit": (given.unit or "").strip() or None if method == "rate" else None,
            "charge_account_id": charge_id,
            "credit_account_id": credit_id,
            "changed_by": actor.login,
            "changed_at": func.now(),
        }
        if existing is not None:
            if mine != {charge_id, credit_id}:
                _clear(conn, r.version_id, mine)
            conn.execute(
                update(forwarding)
                .where(forwarding.c.forwarding_id == existing.forwarding_id)
                .values(**values)
            )
            fid = existing.forwarding_id
            conn.execute(
                delete(forwarding_receiver).where(forwarding_receiver.c.forwarding_id == fid)
            )
        else:
            fid = conn.execute(
                insert(forwarding)
                .values(version_id=r.version_id, **values)
                .returning(forwarding.c.forwarding_id)
            ).scalar_one()
        if receivers:
            conn.execute(
                insert(forwarding_receiver),
                [
                    {"forwarding_id": fid, "cost_center_id": cc, "quantity": q}
                    for cc, q in receivers.items()
                ],
            )
        recompute(conn, r)

    return _run(engine, actor, work)


def delete_forwarding(engine: Engine, actor: User, version_code: str, code: str) -> str:
    """Remove a forwarding with its charges and credits, and recompute the rest (a full
    forwarding may have passed on charges it made). Its accounts stay in master data."""

    def work(conn: Connection) -> None:
        r = _writable(conn, version_code)
        found = next((d for d in _definitions(conn, r.version_id).values() if d.code == code), None)
        if found is None:
            raise NotFoundError(f"forwarding {code} not found")
        _clear(conn, r.version_id, (found.charge_id, found.credit_id))
        conn.execute(delete(forwarding).where(forwarding.c.forwarding_id == found.forwarding_id))
        recompute(conn, r)

    return _run(engine, actor, work)


def carry(conn: Connection, source: RoundRef, target: RoundRef, planned: set[int]) -> int:
    """A new round's forwardings from the round it is set up like (ADR 0018), for sources the
    new round plans, with the receivers it plans; then computed. Returns how many."""
    defs = [d for d in _definitions(conn, source.version_id).values() if d.source_id in planned]
    for d in defs:
        fid = conn.execute(
            insert(forwarding)
            .values(
                version_id=target.version_id,
                code=d.code,
                name=d.name,
                method=d.method,
                source_id=d.source_id,
                shares=d.shares,
                amount=d.amount,
                rate=d.rate,
                unit=d.unit,
                charge_account_id=d.charge_id,
                credit_account_id=d.credit_id,
                changed_by=d.changed_by,
            )
            .returning(forwarding.c.forwarding_id)
        ).scalar_one()
        rows = [
            {"forwarding_id": fid, "cost_center_id": cc, "quantity": q}
            for cc, q in d.receivers.items()
            if cc in planned
        ]
        if rows:
            conn.execute(insert(forwarding_receiver), rows)
    if defs:
        recompute(conn, target)
    return len(defs)
