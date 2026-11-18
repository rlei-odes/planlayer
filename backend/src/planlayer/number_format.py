"""How amounts are shown and typed (E-13): separators, decimals and scale. The admin sets the
installation's default; each user may override any part of it for themselves. Display only:
values are entered, stored and published exactly (T-12)."""

from dataclasses import dataclass
from typing import Literal, cast

from sqlalchemy import Engine, select, update

from planlayer.access import User
from planlayer.store.schema import app_user, installation
from planlayer.store.uow import unit_of_work

# Thousands and decimal separator: 1,234.56 / 1.234,56 / 1'234.56 / 1 234,56.
Separators = Literal["comma_dot", "dot_comma", "apostrophe_dot", "space_comma"]
# Full amounts, in thousands or in millions.
Scale = Literal["full", "k", "M"]


@dataclass(frozen=True)
class NumberFormat:
    separators: Separators
    decimals: bool  # cents shown on full amounts; thousands and millions round on their own
    scale: Scale
    units: bool  # the k or M after a value in thousands or millions


@dataclass(frozen=True)
class Override:
    """A user's own choices; None takes the installation's."""

    separators: Separators | None = None
    decimals: bool | None = None
    scale: Scale | None = None
    units: bool | None = None


@dataclass(frozen=True)
class UserFormat:
    effective: NumberFormat
    default: NumberFormat
    own: Override


def default(engine: Engine, user: User) -> NumberFormat:
    with unit_of_work(engine, user) as conn:
        separators, decimals, scale, units = conn.execute(
            select(
                installation.c.number_separators,
                installation.c.number_decimals,
                installation.c.number_scale,
                installation.c.number_units,
            )
        ).one()
    return NumberFormat(cast(Separators, separators), decimals, cast(Scale, scale), units)


def of_user(engine: Engine, user: User) -> UserFormat:
    base = default(engine, user)
    with unit_of_work(engine, user) as conn:
        separators, decimals, scale, units = conn.execute(
            select(
                app_user.c.number_separators,
                app_user.c.number_decimals,
                app_user.c.number_scale,
                app_user.c.number_units,
            ).where(app_user.c.user_id == user.user_id)
        ).one()
    own = Override(cast(Separators | None, separators), decimals, cast(Scale | None, scale), units)
    return UserFormat(
        NumberFormat(
            own.separators or base.separators,
            base.decimals if own.decimals is None else own.decimals,
            own.scale or base.scale,
            base.units if own.units is None else own.units,
        ),
        base,
        own,
    )


def set_default(engine: Engine, user: User, value: NumberFormat) -> None:
    user.require_admin()
    with unit_of_work(engine, user) as conn:
        conn.execute(
            update(installation).values(
                number_separators=value.separators,
                number_decimals=value.decimals,
                number_scale=value.scale,
                number_units=value.units,
            )
        )


def set_own(engine: Engine, user: User, value: Override) -> None:
    """Any user's own choices, for themselves only."""
    with unit_of_work(engine, user) as conn:
        conn.execute(
            update(app_user)
            .where(app_user.c.user_id == user.user_id)
            .values(
                number_separators=value.separators,
                number_decimals=value.decimals,
                number_scale=value.scale,
                number_units=value.units,
            )
        )
