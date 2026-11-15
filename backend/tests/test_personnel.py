"""Personnel costs (ADR 0022): who reads pay, totals on the cost center, small groups, HR's
review, the rules and the guards on closing and publishing.

The fixture's HR list gives CC1, active in 2026: P001 (EMP, 4000 a month), P002 (EMP, 2000),
P003 (EMP, 3200, leaving end of June) and P004 (a freelancer, 9000). CC2 has P009 (EMP, 3500).
The rules here put employees on 6000, freelancers (typed) on 7000, and raises and
contributions on 6010.
"""

from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update
from sqlalchemy.exc import DBAPIError

from planlayer import access, rounds
from planlayer.config import Settings
from planlayer.store.db import create_app_engine
from planlayer.store.schema import position_pay, position_pay_log
from planlayer.store.uow import unit_of_work

from .conftest import BLANK, CONTROLLER, OWNER, VIEWER, login, plan, rows

R = "B2026"
HR = "hr@x.example"
DEPUTY = "dep@x.example"
PAY = f"/api/versions/{R}/personnel"
GRID = f"/api/versions/{R}/cost-centers/CC1/grid"


@pytest.fixture
def engine(migrated: Settings, client: TestClient) -> Iterator[Engine]:
    e = create_app_engine(migrated)
    admin = replace(access.system_user(e), is_admin=True)
    access.create_user(e, admin, HR, "HR", is_hr=True)
    access.create_user(e, admin, DEPUTY, "Deputy")
    access.add_deputy(e, access.system_user(e), "CC1", DEPUTY)
    yield e
    e.dispose()


def rules(**changes: object) -> dict[str, object]:
    return {
        "min_group": 1,
        "bonus_account": None,
        "staff_types": [
            {"code": "EMP", "account": "6000", "typed": False},
            {"code": "FRL", "account": "7000", "typed": True},
        ],
        "rules": [],
        **changes,
    }


def start(client: TestClient, **changes: object) -> dict:  # type: ignore[type-arg]
    """Positions with HR's staff on CC1, personnel costs on, the rules saved by HR."""
    setup = client.get(f"/api/rounds/{R}/setup").json()["setup"]
    body = {**setup, "workforce_mode": "positions", "personnel_costs": True}
    assert client.put(f"/api/rounds/{R}/setup", json=body).status_code == 200
    assert client.post(f"/api/versions/{R}/workforce/CC1/hr").status_code == 200
    login(client, HR)
    response = client.put(f"{PAY}/rules", json=rules(**changes))
    assert response.status_code == 200, response.text
    page = client.get(f"{PAY}/CC1").json()
    login(client, CONTROLLER)
    return page  # type: ignore[no-any-return]


def account(page: dict, code: str) -> list[str | None]:  # type: ignore[type-arg]
    found = [a for a in page["accounts"] if a["code"] == code]
    return found[0]["months"] if found else BLANK


def by_code(page: dict) -> dict[str, dict]:  # type: ignore[type-arg]
    return {p["code"]: p for p in page["positions"]}


def test_totals_from_hrs_salaries(engine: Engine, client: TestClient) -> None:
    page = start(client)
    p = by_code(page)
    assert p["P001"]["hr_salary"] == "4000.00" and p["P001"]["salary"] is None
    assert p["P004"]["typed"] is True
    # Employees on 6000, the planner until June; the freelancer's fee on 7000.
    assert account(page, "6000") == ["9200.00"] * 6 + ["6000.00"] * 6
    assert account(page, "7000") == ["9000.00"] * 12
    assert p["P003"]["year_cost"] == "19200.00"
    # The cost center sheet shows the totals, read only, marked.
    grid = client.get(GRID).json()
    row = rows(grid)["6000"]
    assert row["personnel"] is True and not row["total_editable"]
    assert plan(grid, "6000") == ["9200.00"] * 6 + ["6000.00"] * 6
    edit = {"edits": [{"account_code": "6000", "month": 1, "value": "1", "expected": None}]}
    assert client.post(f"/api/versions/{R}/cost-centers/CC1/edits", json=edit).status_code == 422


def test_who_reads_pay(engine: Engine, client: TestClient) -> None:
    start(client)
    # The controller, the viewer and a deputy read the cost center, not its pay.
    for user in (CONTROLLER, VIEWER, DEPUTY):
        login(client, user)
        assert client.get(f"{PAY}/CC1").status_code == 404, user
        assert client.get(PAY).json() == []
    # The owner the source names reads their own cost center's; HR reads every one.
    login(client, OWNER)
    assert [u["code"] for u in client.get(PAY).json()] == ["CC1"]
    assert client.get(f"{PAY}/CC2").status_code == 404
    login(client, HR)
    assert [u["code"] for u in client.get(PAY).json()] == ["CC1", "CC2"]
    # Row-level security: the controller's own reads of the tables find nothing.
    with engine.connect() as c:
        controller = access.user_by_login(c, CONTROLLER)
    with unit_of_work(engine, controller) as conn:
        assert conn.execute(select(position_pay)).all() == []
        assert conn.execute(select(position_pay_log)).all() == []
    # An owner the controller sets plans the cost center but reads no pay.
    access.set_owner(engine, controller, "CC2", DEPUTY)
    login(client, DEPUTY)
    assert client.get(f"{PAY}/CC2").status_code == 404


def test_planned_salary_bonus_and_rules(engine: Engine, client: TestClient) -> None:
    page = start(
        client,
        bonus_account="6010",
        rules=[
            {"kind": "extra", "name": "13th salary", "account": "6000", "salaries": "1",
             "steps": []},
            {"kind": "raise", "name": "Raise", "account": "6010", "percent": "10",
             "starts": "2026-07-01", "steps": []},
        ],
    )  # fmt: skip
    # Base pay plus a 13th salary on 6000; the raise from July on 6010.
    assert account(page, "6000")[0] == f"{9200 * 13 / 12:.2f}"
    assert account(page, "6010")[6] == f"{6000 * 13 / 12 * 0.1:.2f}"
    p002 = by_code(page)["P002"]
    login(client, OWNER)
    body = {"salary": "2600", "bonus": "1200"}
    page = client.put(f"{PAY}/CC1/positions/{p002['position_id']}", json=body).json()
    assert by_code(page)["P002"]["salary"] == "2600.00"
    assert by_code(page)["P002"]["hr_salary"] == "2000.00"
    assert account(page, "6000")[0] == f"{9800 * 13 / 12:.2f}"
    # The bonus, 100 a month, joins the raise on 6010.
    assert account(page, "6010")[0] == "100.00"
    # The owner can't charge a position elsewhere, nor change HR's salary.
    body = {"salary": "2600", "bonus": "1200", "charged_to": "CC2"}
    assert client.put(f"{PAY}/CC1/positions/{p002['position_id']}", json=body).status_code == 403
    with engine.connect() as c:
        owner = access.user_by_login(c, OWNER)
    with pytest.raises(DBAPIError, match="HR's salary"), unit_of_work(engine, owner) as conn:
        conn.execute(update(position_pay).values(hr_salary=1))


def test_contributions_up_to_the_ceiling(engine: Engine, client: TestClient) -> None:
    page = start(
        client,
        rules=[
            {"kind": "contribution", "name": "Pension", "account": "6010", "percent": "10",
             "ceiling": "3000", "steps": []},
        ],
    )  # fmt: skip
    # P001 is capped at 3000; P002 2000 and P003 3000 of 3200.
    assert account(page, "6010")[0] == "800.00"
    assert account(page, "6010")[6] == "500.00"


def test_small_groups_hidden_and_resolved(engine: Engine, client: TestClient) -> None:
    start(client, min_group=3)
    # The freelancer alone on 7000: hidden from the controller, shown to the owner and HR.
    grid = client.get(GRID).json()
    assert plan(grid, "6000")[0] == "9200.00"
    assert "7000" not in rows(grid) or plan(grid, "7000") == BLANK
    login(client, OWNER)
    assert plan(client.get(GRID).json(), "7000")[0] == "9000.00"
    login(client, HR)
    page = client.get(f"{PAY}/CC1").json()
    assert next(a for a in page["accounts"] if a["code"] == "7000")["small"] == 1
    assert client.get("/api/home").json()["rounds"][0]["small_groups"] == ["CC1"]
    # Closing and publishing wait for HR.
    login(client, CONTROLLER)
    response = client.post(f"/api/versions/{R}/status", json={"status": "closed"})
    assert response.status_code == 422 and "minimum" in response.text
    assert client.get(f"/api/rounds/{R}/publish/preview").status_code == 409
    # HR charges the freelancer to CC2, where the assembler... is alone on 6000 too.
    login(client, HR)
    p004 = by_code(page)["P004"]
    body = {"salary": None, "bonus": None, "charged_to": "CC2"}
    page = client.put(f"{PAY}/CC1/positions/{p004['position_id']}", json=body).json()
    assert by_code(page)["P004"]["charged_to"] == "CC2"
    assert account(page, "7000") == BLANK
    cc2 = client.get(f"{PAY}/CC2").json()
    assert account(cc2, "7000")[0] == "9000.00"
    # Still small on CC2; a minimum of 1 lets them go.
    response = client.put(f"{PAY}/rules", json=rules(min_group=1))
    assert response.status_code == 200
    login(client, CONTROLLER)
    assert client.get(f"/api/rounds/{R}/publish/preview").status_code == 200


def test_approval_waits_for_hrs_review(engine: Engine, client: TestClient) -> None:
    start(client)
    login(client, OWNER)
    act = {"action": "submit", "cost_centers": ["CC1"]}
    assert client.post(f"/api/versions/{R}/workflow", json=act).status_code == 200
    login(client, CONTROLLER)
    act = {"action": "approve", "cost_centers": ["CC1"]}
    response = client.post(f"/api/versions/{R}/workflow", json=act)
    assert response.status_code == 422 and "HR" in response.text
    login(client, HR)
    assert client.get("/api/home").json()["rounds"][0]["to_review"] == ["CC1"]
    review = client.put(f"{PAY}/CC1/review", json={"confirmed": True}).json()
    assert review["review"]["confirmed_by"] == HR
    login(client, CONTROLLER)
    assert client.post(f"/api/versions/{R}/workflow", json=act).status_code == 200
    # Only HR confirms.
    assert client.put(f"{PAY}/CC1/review", json={"confirmed": True}).status_code == 403


def test_owners_change_withdraws_the_review(engine: Engine, client: TestClient) -> None:
    page = start(client)
    login(client, HR)
    assert client.put(f"{PAY}/CC1/review", json={"confirmed": True}).status_code == 200
    # HR's own change keeps it; the owner's withdraws it.
    p001 = by_code(page)["P001"]
    page = client.put(f"{PAY}/CC1/positions/{p001['position_id']}", json={"bonus": "10"}).json()
    assert page["review"] is not None
    login(client, OWNER)
    page = client.put(f"{PAY}/CC1/positions/{p001['position_id']}", json={"bonus": "20"}).json()
    assert page["review"] is None


def test_rules_for_hr_and_controllers_only(engine: Engine, client: TestClient) -> None:
    start(client)
    assert client.get(f"{PAY}/rules").status_code == 200
    login(client, OWNER)
    assert client.get(f"{PAY}/rules").status_code == 403
    login(client, HR)
    bad = rules(staff_types=[{"code": "EMP", "account": "4900", "typed": False}])
    assert client.put(f"{PAY}/rules", json=bad).status_code == 422  # a capacity credit account
    raise_ = {
        "kind": "raise",
        "name": "R",
        "account": "6010",
        "percent": "2",
        "starts": "2027-01-01",
        "steps": [],
    }
    assert client.put(f"{PAY}/rules", json=rules(rules=[raise_])).status_code == 422
    # Dropping an account from the rules removes the totals it held.
    page = client.put(f"{PAY}/rules", json=rules(staff_types=[])).json()
    assert page["rules"]["staff_types"][0]["account"] is None
    assert account(client.get(f"{PAY}/CC1").json(), "6000") == BLANK


def test_module_off_in_an_open_round_with_values_is_refused(
    engine: Engine, client: TestClient
) -> None:
    start(client)
    setup = client.get(f"/api/rounds/{R}/setup").json()["setup"]
    assert setup["personnel_costs"] is True
    off = {**setup, "personnel_costs": False}
    response = client.put(f"/api/rounds/{R}/setup", json=off)
    assert response.status_code == 422 and "only in setup" in response.text
    # Workforce can't leave positions while personnel costs are on.
    response = client.put(f"/api/rounds/{R}/setup", json={**setup, "workforce_mode": "fte"})
    assert response.status_code == 422


def test_a_new_round_carries_rules_and_pay(engine: Engine, client: TestClient) -> None:
    page = start(
        client,
        rules=[
            {"kind": "raise", "name": "Raise", "account": "6010", "percent": "2",
             "starts": "2026-04-01", "steps": []},
        ],
    )  # fmt: skip
    p002 = by_code(page)["P002"]
    login(client, OWNER)
    client.put(f"{PAY}/CC1/positions/{p002['position_id']}", json={"salary": "2500"})
    system = access.system_user(engine)
    rounds.create_round(engine, system, "B2027", "Budget 2027", "budget", 2027, copy_from=R)
    rounds.set_round_status(engine, system, "B2027", "open")
    login(client, HR)
    rules_2027 = client.get("/api/versions/B2027/personnel/rules").json()["rules"]
    assert rules_2027["rules"][0]["starts"] == "2027-04-01"
    page = client.get("/api/versions/B2027/personnel/CC1").json()
    p = by_code(page)
    # The planner left in 2026; the others keep their planned salary and HR's.
    assert "P003" not in p
    assert p["P002"]["salary"] == "2500.00" and p["P002"]["hr_salary"] == "2000.00"
    assert account(page, "6000")[0] == "6500.00"
