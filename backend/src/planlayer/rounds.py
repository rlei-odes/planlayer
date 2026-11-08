"""Planning rounds and their setup (ADR 0007): modules, enabled members, entry forms,
comparisons, hidden accounts, seasonality profiles; round status and approvers (ADR 0008).

The yearly setup page (E-03) edits a round through these functions.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from sqlalchemy import Connection, Engine, delete, insert, literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.access import User
from planlayer.domain.entry_forms import FORMS, Form
from planlayer.domain.periods import fiscal_year_periods
from planlayer.domain.spreading import Profile
from planlayer.domain.workflow import RoundStatus, round_transition
from planlayer.store.schema import (
    ACCOUNT,
    COST_CENTER,
    MODULE_COST_CENTER,
    comparison,
    dataset_mapping,
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
    actor: User,
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
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        if conn.execute(select(version.c.version_id).where(version.c.code == code)).first():
            raise RoundError(f"a round or version {code} exists")
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


def set_round_status(engine: Engine, actor: User, code: str, status: RoundStatus) -> None:
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code, lock="update")
        round_transition(r.status, status)
        conn.execute(
            update(planning_round)
            .where(planning_round.c.version_id == r.version_id)
            .values(status=status)
        )


def set_deadline(engine: Engine, actor: User, code: str, deadline: date | None) -> None:
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code, lock="update")
        conn.execute(
            update(planning_round)
            .where(planning_round.c.version_id == r.version_id)
            .values(deadline=deadline)
        )


def set_member_enabled(engine: Engine, actor: User, code: str, cc_code: str, enabled: bool) -> None:
    actor.require_controller()
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


def add_profile(engine: Engine, actor: User, code: str, name: str, profile: Profile) -> int:
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        taken = conn.execute(
            select(seasonality_profile.c.profile_id).where(seasonality_profile.c.code == code)
        ).first()
        if taken is not None:
            raise RoundError(f"a profile {code} exists")
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
    actor: User,
    code: str,
    account_code: str | None,
    form: Form | None,
    profile_code: str | None = None,
    reference_version_code: str | None = None,
) -> None:
    """Set the entry form of an account group or account, or with ``account_code`` None the
    module default. ``form`` None removes an account's own setting."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code)
        if account_code is None:
            if form is None or profile_code or reference_version_code:
                raise RoundError("the module default is a form without a basis")
            _set_default_form(conn, r, form)
        else:
            _set_entry_form(conn, r, account_code, form, profile_code, reference_version_code)


def _set_default_form(conn: Connection, r: RoundRef, form: Form) -> None:
    if form not in FORMS:
        raise RoundError(f"unknown entry form {form}")
    conn.execute(
        update(round_module)
        .where(
            round_module.c.version_id == r.version_id, round_module.c.module == MODULE_COST_CENTER
        )
        .values(default_form=form)
    )


def _set_entry_form(
    conn: Connection,
    r: RoundRef,
    account_code: str,
    form: Form | None,
    profile_code: str | None,
    reference_version_code: str | None,
) -> None:
    if form is not None and form not in FORMS:
        raise RoundError(f"unknown entry form {form}")
    account_id = _member_id(conn, ACCOUNT, account_code)
    conn.execute(
        delete(entry_form).where(
            entry_form.c.version_id == r.version_id,
            entry_form.c.module == MODULE_COST_CENTER,
            entry_form.c.account_id == account_id,
        )
    )
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
        raise RoundError(f"{account_code}: a seasonality profile is needed")
    if profile_id is not None and form not in ("monthly", "annual_profile"):
        raise RoundError(f"{account_code}: {form} doesn't spread by a profile")
    if reference is not None and form not in ("monthly", "annual_reference"):
        raise RoundError(f"{account_code}: {form} doesn't spread by a reference")
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
    engine: Engine, actor: User, code: str, columns: list[tuple[ComparisonKind, str | None]]
) -> None:
    """The comparison columns, in order: (kind, version code for kind ``version``)."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        _set_comparisons(conn, get_round(conn, code), columns)


def _set_comparisons(
    conn: Connection, r: RoundRef, columns: list[tuple[ComparisonKind, str | None]]
) -> None:
    rows = []
    for position, (kind, other) in enumerate(columns, start=1):
        if kind not in ("prior_year_actuals", "actuals", "version"):
            raise RoundError(f"unknown comparison {kind}")
        if (kind == "version") != (other is not None):
            raise RoundError("a version comparison names a version, the others don't")
        other_id = _version_id(conn, other) if other else None
        if other_id == r.version_id:
            raise RoundError("a round can't compare with itself")
        rows.append(
            {
                "version_id": r.version_id,
                "module": MODULE_COST_CENTER,
                "position": position,
                "kind": kind,
                "compare_version_id": other_id,
            }
        )
    conn.execute(
        delete(comparison).where(
            comparison.c.version_id == r.version_id, comparison.c.module == MODULE_COST_CENTER
        )
    )
    if rows:
        conn.execute(insert(comparison), rows)


def hide_account(engine: Engine, actor: User, code: str, account_code: str) -> None:
    """Hide an account in a round; refused while any cost center has plan values on it."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        _hide(conn, get_round(conn, code), account_code)


def _hide(conn: Connection, r: RoundRef, account_code: str) -> None:
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


def unhide_account(engine: Engine, actor: User, code: str, account_code: str) -> None:
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        _unhide(conn, get_round(conn, code), account_code)


def _unhide(conn: Connection, r: RoundRef, account_code: str) -> None:
    account_id = _member_id(conn, ACCOUNT, account_code, leaf=True)
    conn.execute(
        delete(hidden_account).where(
            hidden_account.c.version_id == r.version_id,
            hidden_account.c.module == MODULE_COST_CENTER,
            hidden_account.c.account_id == account_id,
        )
    )


# --- The setup page (E-03) ------------------------------------------------------------------------


@dataclass
class EntryFormRule:
    account_code: str  # an account group or an account
    form: Form
    profile_code: str | None = None
    reference_version_code: str | None = None


@dataclass
class Setup:
    """A round's whole setup, as the setup page edits it."""

    default_form: Form
    entry_forms: list[EntryFormRule]
    comparisons: list[tuple[ComparisonKind, str | None]]
    hidden_accounts: list[str]
    deadline: date | None


@dataclass
class SetupView:
    round: RoundRef
    copied_from: str | None
    setup: Setup
    actuals_available: bool  # synced actuals exist, so actuals comparisons and references work
    enabled_cost_centers: int
    sample_cost_center: str | None  # a name for the preview
    periods: list[date]


def setup_of(engine: Engine, user: User, code: str) -> SetupView:
    user.require_controller()
    with engine.connect() as conn:
        r = get_round(conn, code)
        default = conn.execute(
            select(round_module.c.default_form).where(
                round_module.c.version_id == r.version_id,
                round_module.c.module == MODULE_COST_CENTER,
            )
        ).scalar_one()
        acc, ref = member.alias("acc"), version.alias("ref")
        rules = [
            EntryFormRule(a, f, p, v)
            for a, f, p, v in conn.execute(
                select(acc.c.code, entry_form.c.form, seasonality_profile.c.code, ref.c.code)
                .join(acc, acc.c.member_id == entry_form.c.account_id)
                .outerjoin(
                    seasonality_profile,
                    seasonality_profile.c.profile_id == entry_form.c.profile_id,
                )
                .outerjoin(ref, ref.c.version_id == entry_form.c.reference_version_id)
                .where(
                    entry_form.c.version_id == r.version_id,
                    entry_form.c.module == MODULE_COST_CENTER,
                )
                .order_by(acc.c.code)
            )
        ]
        columns = [
            (k, v)
            for k, v in conn.execute(
                select(comparison.c.kind, version.c.code)
                .outerjoin(version, version.c.version_id == comparison.c.compare_version_id)
                .where(
                    comparison.c.version_id == r.version_id,
                    comparison.c.module == MODULE_COST_CENTER,
                )
                .order_by(comparison.c.position)
            )
        ]
        hidden = list(
            conn.execute(
                select(member.c.code)
                .join(hidden_account, hidden_account.c.account_id == member.c.member_id)
                .where(hidden_account.c.version_id == r.version_id)
                .order_by(member.c.code)
            ).scalars()
        )
        copied = conn.execute(
            select(version.c.code)
            .join(planning_round, planning_round.c.copied_from == version.c.version_id)
            .where(planning_round.c.version_id == r.version_id)
        ).scalar()
        periods = fiscal_periods(conn, r.fiscal_year)
        actuals = (
            conn.execute(
                select(dataset_mapping.c.dataset).where(dataset_mapping.c.dataset == "actuals")
            ).first()
            is not None
        )
        enabled = list(
            conn.execute(
                select(member.c.name)
                .join(round_member, round_member.c.member_id == member.c.member_id)
                .where(
                    round_member.c.version_id == r.version_id,
                    round_member.c.enabled.is_(True),
                )
                .order_by(member.c.code)
            ).scalars()
        )
    return SetupView(
        r,
        copied,
        Setup(default, rules, columns, hidden, r.deadline),
        actuals,
        len(enabled),
        enabled[0] if enabled else None,
        periods,
    )


def save_setup(engine: Engine, actor: User, code: str, setup: Setup) -> None:
    """Replace a round's setup in one transaction; any invalid part changes nothing."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code, lock="update")
        if r.status == "closed":
            raise RoundError("the round is closed; reopen it to change its setup")
        _set_default_form(conn, r, setup.default_form)
        conn.execute(
            delete(entry_form).where(
                entry_form.c.version_id == r.version_id,
                entry_form.c.module == MODULE_COST_CENTER,
            )
        )
        seen: set[str] = set()
        for rule in setup.entry_forms:
            if rule.account_code in seen:
                raise RoundError(f"{rule.account_code} has two entry forms")
            seen.add(rule.account_code)
            _set_entry_form(
                conn,
                r,
                rule.account_code,
                rule.form,
                rule.profile_code,
                rule.reference_version_code,
            )
        _set_comparisons(conn, r, setup.comparisons)
        now_hidden = set(
            conn.execute(
                select(member.c.code)
                .join(hidden_account, hidden_account.c.account_id == member.c.member_id)
                .where(hidden_account.c.version_id == r.version_id)
            ).scalars()
        )
        for account in sorted(now_hidden - set(setup.hidden_accounts)):
            _unhide(conn, r, account)
        for account in sorted(set(setup.hidden_accounts) - now_hidden):
            _hide(conn, r, account)
        conn.execute(
            update(planning_round)
            .where(planning_round.c.version_id == r.version_id)
            .values(deadline=setup.deadline)
        )


@dataclass
class ProfileRow:
    code: str
    name: str
    entered_as: str
    values: list[Decimal]


def profiles(engine: Engine) -> list[ProfileRow]:
    with engine.connect() as conn:
        return [
            ProfileRow(r.code, r.name, r.entered_as, list(r.values))
            for r in conn.execute(select(seasonality_profile).order_by(seasonality_profile.c.code))
        ]


def rounds(engine: Engine) -> list[RoundRef]:
    with engine.connect() as conn:
        rows = conn.execute(
            _ROUNDS.order_by(planning_round.c.fiscal_year.desc(), version.c.code)
        ).all()
    return [RoundRef(**r._mapping) for r in rows]
