"""Development users for the Nordwerk test data (TD-09); refused in production.

The cost center owners are the generator's ``USERS`` (tools/gen_testdata, ``master.py``); its
``UNMATCHED_OWNER`` deliberately gets no user. Besides them: an admin, a controller, the CFO
(viewer, approver at the root node) and an auditor (viewer). One owner is also deputy for a
second, disjoint department.
"""

from dataclasses import replace

from sqlalchemy import Engine, select

from planlayer import access
from planlayer.store.schema import COST_CENTER, member

OWNERS = (
    "anna.keller@nordwerk.example",
    "jonas.brandt@nordwerk.example",
    "marta.nowak@nordwerk.example",
    "luca.meier@nordwerk.example",
    "emily.carter@nordwerk.example",
    "felix.wagner@nordwerk.example",
    "ryan.brooks@nordwerk.example",
    "sabine.roth@nordwerk.example",
)
ROOT, DEPUTY = "NW", ("anna.keller@nordwerk.example", "DEP_VALVE_PROD")


def _name(email: str) -> str:
    return " ".join(p.capitalize() for p in email.split("@")[0].split("."))


def seed(engine: Engine) -> list[str]:
    """Create the users that don't exist yet; returns the logins created."""
    system = access.system_user(engine)
    # The system user creates the users; it acts as admin for this only.
    admin = replace(system, is_admin=True)
    existing = {u.login for u in access.users(engine)}
    wanted: list[tuple[str, str, dict[str, bool]]] = [(e, _name(e), {}) for e in OWNERS] + [
        ("admin@nordwerk.example", "Admin", {"is_admin": True}),
        ("controller@nordwerk.example", "Controller", {"is_controller": True}),
        ("cfo@nordwerk.example", "CFO", {"is_viewer": True}),
        ("auditor@nordwerk.example", "Auditor", {"is_viewer": True}),
    ]
    created = []
    for login, name, roles in wanted:
        if login not in existing:
            access.create_user(engine, admin, login, name, login, **roles)
            created.append(login)
    with engine.connect() as conn:
        nodes = set(
            conn.execute(
                select(member.c.code).where(
                    member.c.dim_id == COST_CENTER, member.c.code.in_([ROOT, DEPUTY[1]])
                )
            ).scalars()
        )
    if ROOT in nodes:
        access.add_approver(engine, system, ROOT, "cfo@nordwerk.example")
    if DEPUTY[1] in nodes:
        access.add_deputy(engine, system, DEPUTY[1], DEPUTY[0])
    return created
