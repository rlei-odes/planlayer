"""Planning rounds and their setup (ADR 0007): modules, enabled members, entry forms,
comparisons, hidden accounts, seasonality profiles; round status and approvers (ADR 0008).

The yearly setup page (E-03) edits a round through these functions.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import Connection, Engine, and_, delete, insert, literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.domain.entry_forms import FORMS, Form
from planlayer.domain.periods import fiscal_year_periods
from planlayer.domain.spreading import Profile
from planlayer.domain.workflow import RoundStatus, round_transition
from planlayer.store.schema import (
    ACCOUNT,
    COST_CENTER,
    MODULE_COST_CENTER,
    approver_assignment,
    comparison,
    entry_form,
    fact_cost_center,
    hidden_account,
    installation,
    member,
    planning_round,
    round_member,
    round_module,
    seasonality_profile,
    version,
)
from planlayer.store.uow import unit_of_work

ComparisonKind = Literal["prior_year_actuals", "actuals", "version"]


class NotFoundError(LookupError):
    pass


class RoundError(ValueError):
    """A setup change that can't be made as asked; nothing was changed."""


@dataclass
class RoundRef:
    version_id: int
    code: str
    name: str
    kind: str
    fiscal_year: int
    status: RoundStatus
    deadline: date | None


_ROUNDS = select(
    version.c.version_id,
    version.c.code,
    version.c.name,
    version.c.kind,
    planning_round.c.fiscal_year,
    planning_round.c.status,
    planning_round.c.deadline,
).join(planning_round, planning_round.c.version_id == version.c.version_id)


def get_round(
    conn: Connection, code: str, lock: Literal["share", "update"] | None = None
) -> RoundRef:
    query = _ROUNDS.where(version.c.code == code)
    if lock is not None:
        query = query.with_for_update(read=lock == "share", of=planning_round)
    row = conn.execute(query).first()
    if row is None:
        raise NotFoundError(f"planning round {code} not found")
    return RoundRef(**row._mapping)


def fiscal_periods(conn: Connection, fiscal_year: int) -> list[date]:
    start = conn.execute(select(installation.c.fiscal_year_start_month)).scalar_one()
    return fiscal_year_periods(fiscal_year, start)


def valid_cost_centers(conn: Connection, fiscal_year: int) -> list[int]:
    """Leaf cost centers valid at some point of the fiscal year."""
    periods = fiscal_periods(conn, fiscal_year)
    return list(
        conn.execute(
            select(member.c.member_id).where(
                member.c.dim_id == COST_CENTER,
                member.c.is_group.is_(False),
                member.c.valid_from.is_(None) | (member.c.valid_from <= periods[-1]),
                member.c.valid_to.is_(None) | (member.c.valid_to >= periods[0]),
            )
        ).scalars()
    )


def _member_id(conn: Connection, dim_id: int, code: str, *, leaf: bool | None = None) -> int:
    query = select(member.c.member_id).where(member.c.dim_id == dim_id, member.c.code == code)
    if leaf is not None:
        query = query.where(member.c.is_group.is_(not leaf))
    found = conn.execute(query).scalar()
    if found is None:
        raise NotFoundError(f"{'account' if dim_id == ACCOUNT else 'member'} {code} not found")
    return int(found)


def _version_id(conn: Connection, code: str) -> int:
    found = conn.execute(select(version.c.version_id).where(version.c.code == code)).scalar()
    if found is None:
        raise NotFoundError(f"version {code} not found")
    return int(found)


def create_round(
    engine: Engine,
    actor: str,
    code: str,
    name: str,
    kind: Literal["budget", "forecast"],
    fiscal_year: int,
    deadline: date | None = None,
    copy_from: str | None = None,
) -> int:
    """A plan version and its round, in status ``setup``.

    The setup is copied from ``copy_from`` (modules, entry forms, comparisons, hidden accounts,
    disabled cost centers), or starts from the module defaults: every account ``monthly``,
    prior-year actuals as the only comparison. Every cost center valid in the fiscal year is
    enabled, except those disabled in the copied round.
    """
    with unit_of_work(engine, actor) as conn:
        source = get_round(conn, copy_from) if copy_from is not None else None
        vid = int(
            conn.execute(
                insert(version)
                .values(code=code, name=name, kind=kind)
                .returning(version.c.version_id)
            ).scalar_one()
        )
        conn.execute(
            insert(planning_round).values(
                version_id=vid,
                fiscal_year=fiscal_year,
                deadline=deadline,
                copied_from=source.version_id if source else None,
            )
        )
        disabled: set[int] = set()
        if source is None:
            conn.execute(insert(round_module).values(version_id=vid, module=MODULE_COST_CENTER))
            conn.execute(
                insert(comparison).values(
                    version_id=vid,
                    module=MODULE_COST_CENTER,
                    position=1,
                    kind="prior_year_actuals",
                )
            )
        else:
            _copy_setup(conn, source.version_id, vid)
            disabled = set(
                conn.execute(
                    select(round_member.c.member_id).where(
                        round_member.c.version_id == source.version_id,
                        round_member.c.module == MODULE_COST_CENTER,
                        round_member.c.enabled.is_(False),
                    )
                ).scalars()
            )
        members = valid_cost_centers(conn, fiscal_year)
        if members:
            conn.execute(
                insert(round_member),
                [
                    {
                        "version_id": vid,
                        "module": MODULE_COST_CENTER,
                        "member_id": m,
                        "enabled": m not in disabled,
                    }
                    for m in members
                ],
            )
        return vid


def _copy_setup(conn: Connection, source_id: int, target_id: int) -> None:
    for table in (round_module, entry_form, comparison, hidden_account):
        columns = [c for c in table.c if c.name != "version_id"]
        conn.execute(
            insert(table).from_select(
                ["version_id", *(c.name for c in columns)],
                select(literal(target_id, table.c.version_id.type), *columns).where(
                    table.c.version_id == source_id
                ),
            )
        )


def set_round_status(engine: Engine, actor: str, code: str, status: RoundStatus) -> None:
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code, lock="update")
        round_transition(r.status, status)
        conn.execute(
            update(planning_round)
            .where(planning_round.c.version_id == r.version_id)
            .values(status=status)
        )


def set_deadline(engine: Engine, actor: str, code: str, deadline: date | None) -> None:
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code, lock="update")
        conn.execute(
            update(planning_round)
            .where(planning_round.c.version_id == r.version_id)
            .values(deadline=deadline)
        )


def set_member_enabled(engine: Engine, actor: str, code: str, cc_code: str, enabled: bool) -> None:
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code)
        cc_id = _member_id(conn, COST_CENTER, cc_code, leaf=True)
        stmt = pg_insert(round_member).values(
            version_id=r.version_id, module=MODULE_COST_CENTER, member_id=cc_id, enabled=enabled
        )
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=list(round_member.primary_key.columns),
                set_={"enabled": stmt.excluded.enabled},
            )
        )


def add_profile(engine: Engine, actor: str, code: str, name: str, profile: Profile) -> int:
    with unit_of_work(engine, actor) as conn:
        return int(
            conn.execute(
                insert(seasonality_profile)
                .values(
                    code=code, name=name, entered_as=profile.entered_as, values=list(profile.values)
                )
                .returning(seasonality_profile.c.profile_id)
            ).scalar_one()
        )


def set_entry_form(
    engine: Engine,
    actor: str,
    code: str,
    account_code: str | None,
    form: Form | None,
    profile_code: str | None = None,
    reference_version_code: str | None = None,
) -> None:
    """Set the entry form of an account group or account, or with ``account_code`` None the
    module default. ``form`` None removes an account's own setting."""
    if form is not None and form not in FORMS:
        raise RoundError(f"unknown entry form {form}")
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code)
        if account_code is None:
            if form is None or profile_code or reference_version_code:
                raise RoundError("the module default is a form without a basis")
            conn.execute(
                update(round_module)
                .where(
                    round_module.c.version_id == r.version_id,
                    round_module.c.module == MODULE_COST_CENTER,
                )
                .values(default_form=form)
            )
            return
        account_id = _member_id(conn, ACCOUNT, account_code)
        key = and_(
            entry_form.c.version_id == r.version_id,
            entry_form.c.module == MODULE_COST_CENTER,
            entry_form.c.account_id == account_id,
        )
        conn.execute(delete(entry_form).where(key))
        if form is None:
            return
        profile_id = None
        if profile_code is not None:
            profile_id = conn.execute(
                select(seasonality_profile.c.profile_id).where(
                    seasonality_profile.c.code == profile_code
                )
            ).scalar()
            if profile_id is None:
                raise NotFoundError(f"seasonality profile {profile_code} not found")
        reference = _version_id(conn, reference_version_code) if reference_version_code else None
        if form == "annual_profile" and profile_id is None:
            raise RoundError("annual_profile needs a seasonality profile")
        if profile_id is not None and form not in ("monthly", "annual_profile"):
            raise RoundError(f"{form} doesn't spread by a profile")
        if reference is not None and form not in ("monthly", "annual_reference"):
            raise RoundError(f"{form} doesn't spread by a reference")
        conn.execute(
            insert(entry_form).values(
                version_id=r.version_id,
                module=MODULE_COST_CENTER,
                account_id=account_id,
                form=form,
                profile_id=profile_id,
                reference_version_id=reference,
            )
        )


def set_comparisons(
    engine: Engine, actor: str, code: str, columns: list[tuple[ComparisonKind, str | None]]
) -> None:
    """The comparison columns, in order: (kind, version code for kind ``version``)."""
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code)
        rows = []
        for position, (kind, other) in enumerate(columns, start=1):
            if (kind == "version") != (other is not None):
                raise RoundError("a version comparison names a version, the others don't")
            rows.append(
                {
                    "version_id": r.version_id,
                    "module": MODULE_COST_CENTER,
                    "position": position,
                    "kind": kind,
                    "compare_version_id": _version_id(conn, other) if other else None,
                }
            )
        conn.execute(
            delete(comparison).where(
                comparison.c.version_id == r.version_id,
                comparison.c.module == MODULE_COST_CENTER,
            )
        )
        if rows:
            conn.execute(insert(comparison), rows)


def hide_account(engine: Engine, actor: str, code: str, account_code: str) -> None:
    """Hide an account in a round; refused while any cost center has plan values on it."""
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code)
        account_id = _member_id(conn, ACCOUNT, account_code, leaf=True)
        f = fact_cost_center
        with_values = list(
            conn.execute(
                select(member.c.code)
                .distinct()
                .join(f, f.c.dim_cost_center == member.c.member_id)
                .where(f.c.version_id == r.version_id, f.c.dim_account == account_id)
                .order_by(member.c.code)
            ).scalars()
        )
        if with_values:
            raise RoundError(
                f"account {account_code} still has plan values on {', '.join(with_values)}"
            )
        conn.execute(
            pg_insert(hidden_account)
            .values(version_id=r.version_id, module=MODULE_COST_CENTER, account_id=account_id)
            .on_conflict_do_nothing()
        )


def unhide_account(engine: Engine, actor: str, code: str, account_code: str) -> None:
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code)
        account_id = _member_id(conn, ACCOUNT, account_code, leaf=True)
        conn.execute(
            delete(hidden_account).where(
                hidden_account.c.version_id == r.version_id,
                hidden_account.c.module == MODULE_COST_CENTER,
                hidden_account.c.account_id == account_id,
            )
        )


def assign_approver(engine: Engine, actor: str, node_code: str, user_name: str) -> None:
    with unit_of_work(engine, actor) as conn:
        node_id = _member_id(conn, COST_CENTER, node_code)
        conn.execute(
            pg_insert(approver_assignment)
            .values(member_id=node_id, user_name=user_name)
            .on_conflict_do_nothing()
        )


def rounds(engine: Engine) -> list[RoundRef]:
    with engine.connect() as conn:
        rows = conn.execute(
            _ROUNDS.order_by(planning_round.c.fiscal_year.desc(), version.c.code)
        ).all()
    return [RoundRef(**r._mapping) for r in rows]
