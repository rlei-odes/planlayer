"""How a round is planned (ADR 0007, ADR 0014): entry forms, comparison columns, seasonality
profiles, and accounts not planned."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from sqlalchemy import (
    Connection,
    Engine,
    delete,
    insert,
    select,
    union,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.access import User
from planlayer.domain.entry_forms import FORMS, Form
from planlayer.domain.periods import (
    calendar_days,
    working_days,
)
from planlayer.domain.spreading import Profile
from planlayer.rounds.core import (
    ComparisonKind,
    NotFoundError,
    RoundError,
    RoundRef,
    _member_id,
    _version_id,
    get_round,
)
from planlayer.store.schema import (
    ACCOUNT,
    MODULE_COST_CENTER,
    comparison,
    entry_form,
    fact_cost_center,
    installation,
    line_spread,
    member,
    not_planned_account,
    round_module,
    seasonality_profile,
    version,
)
from planlayer.store.uow import unit_of_work


@dataclass
class Comparison:
    """A comparison column: prior-year actuals, actuals of the year, or another version; with
    the controller's note to planners, e.g. "January to June only"."""

    kind: ComparisonKind
    version_code: str | None = None  # kind ``version`` only
    note: str | None = None


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


def update_profile(engine: Engine, actor: User, code: str, name: str, profile: Profile) -> None:
    """New name and values; they apply to the next spread. Months already stored stay."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        changed = conn.execute(
            update(seasonality_profile)
            .where(seasonality_profile.c.code == code)
            .values(name=name, entered_as=profile.entered_as, values=list(profile.values))
        ).rowcount
        if changed == 0:
            raise NotFoundError(f"seasonality profile {code} not found")


def delete_profile(engine: Engine, actor: User, code: str) -> None:
    """Refused while an entry form or a line in any round uses the profile."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        profile_id = conn.execute(
            select(seasonality_profile.c.profile_id)
            .where(seasonality_profile.c.code == code)
            .with_for_update()
        ).scalar()
        if profile_id is None:
            raise NotFoundError(f"seasonality profile {code} not found")
        users = union(
            select(entry_form.c.version_id).where(entry_form.c.profile_id == profile_id),
            select(line_spread.c.version_id).where(line_spread.c.profile_id == profile_id),
        ).subquery()
        used_by = (
            conn.execute(
                select(version.c.code)
                .where(version.c.version_id.in_(select(users.c.version_id)))
                .order_by(version.c.code)
            )
            .scalars()
            .all()
        )
        if used_by:
            raise RoundError(f"profile {code} is used by round {', '.join(used_by)}")
        conn.execute(
            delete(seasonality_profile).where(seasonality_profile.c.profile_id == profile_id)
        )


Preset = Literal["working_days", "calendar_days"]


def profile_preset(engine: Engine, actor: User, preset: Preset, fiscal_year: int) -> list[Decimal]:
    """Starting values for a new profile, one per fiscal month of the given year."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        start = conn.execute(select(installation.c.fiscal_year_start_month)).scalar_one()
    match preset:
        case "working_days":
            days = working_days(fiscal_year, start)
        case "calendar_days":
            days = calendar_days(fiscal_year, start)
    return [Decimal(d) for d in days]


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


def set_comparisons(engine: Engine, actor: User, code: str, columns: list[Comparison]) -> None:
    """The comparison columns, in order."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        _set_comparisons(conn, get_round(conn, code), columns)


def _set_comparisons(conn: Connection, r: RoundRef, columns: list[Comparison]) -> None:
    rows = []
    for position, column in enumerate(columns, start=1):
        kind, other = column.kind, column.version_code
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
                "note": (column.note or "").strip() or None,
            }
        )
    conn.execute(
        delete(comparison).where(
            comparison.c.version_id == r.version_id, comparison.c.module == MODULE_COST_CENTER
        )
    )
    if rows:
        conn.execute(insert(comparison), rows)


def set_account_planned(
    engine: Engine, actor: User, code: str, account_code: str, planned: bool
) -> None:
    """Mark an account planned or not planned in a round (ADR 0014). Not planned is refused while
    any cost center has plan values on it."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code)
        if planned:
            _include(conn, r, account_code)
        else:
            _exclude(conn, r, account_code)


def _exclude(conn: Connection, r: RoundRef, account_code: str) -> None:
    """Mark an account not planned. In a round in setup its plan values, which the controller
    carried over or prepared, are cleared with it; in an open round it must have none."""
    account_id = _member_id(conn, ACCOUNT, account_code, leaf=True)
    f = fact_cost_center
    in_round = (f.c.version_id == r.version_id) & (f.c.dim_account == account_id)
    if r.status == "setup":
        conn.execute(delete(f).where(in_round))
    with_values = list(
        conn.execute(
            select(member.c.code)
            .distinct()
            .join(f, f.c.dim_cost_center == member.c.member_id)
            .where(in_round)
            .order_by(member.c.code)
        ).scalars()
    )
    if with_values:
        raise RoundError(
            f"account {account_code} still has plan values on {', '.join(with_values)}"
        )
    conn.execute(
        pg_insert(not_planned_account)
        .values(version_id=r.version_id, module=MODULE_COST_CENTER, account_id=account_id)
        .on_conflict_do_nothing()
    )


def _include(conn: Connection, r: RoundRef, account_code: str) -> None:
    account_id = _member_id(conn, ACCOUNT, account_code, leaf=True)
    conn.execute(
        delete(not_planned_account).where(
            not_planned_account.c.version_id == r.version_id,
            not_planned_account.c.module == MODULE_COST_CENTER,
            not_planned_account.c.account_id == account_id,
        )
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
