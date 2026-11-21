"""Workforce planning (ADR 0020).

The fixture source's HR has, for CC1: two machinists (EMP/TECH, 100 % and 50 %), a planner
(EMP/OFF, 80 %, leaving end of June 2026), a freelancer (FRL/TECH, no contract %) and a
machinist who left in 2024; for CC2 an assembler. 2025's month-end actuals for CC1 are 2 heads
and 1.5 FTE in EMP/TECH, 1 and 0.8 in EMP/OFF, and 1 head in FRL/TECH.
"""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, insert, inspect, select, update
from sqlalchemy.exc import DBAPIError

from planlayer import access
from planlayer.config import Settings
from planlayer.rounds import RoundError, get_round
from planlayer.store.db import create_app_engine
from planlayer.store.output import create_output_engine, fact_plan_workforce
from planlayer.store.schema import (
    fact_workforce,
    member,
    version,
    workforce_change_log,
    workforce_position,
    workforce_position_log,
)
from planlayer.store.uow import unit_of_work
from planlayer.workforce.rounds import set_mode

from .conftest import BLANK, CONTROLLER, OWNER, VIEWER, limited_controller, login

R = "B2026"
SHEET = f"/api/versions/{R}/workforce/CC1"


@pytest.fixture
def engine(migrated: Settings, client: TestClient) -> Iterator[Engine]:
    e = create_app_engine(migrated)
    yield e
    e.dispose()


def mode(client: TestClient, value: str | None, code: str = R) -> int:
    setup = client.get(f"/api/rounds/{code}/setup").json()["setup"]
    response = client.put(f"/api/rounds/{code}/setup", json={**setup, "workforce_mode": value})
    return response.status_code


def lines(sheet: dict, measure: str) -> dict[str, list[str | None]]:  # type: ignore[type-arg]
    return {
        f"{r['staff_type_code']}/{r['staff_category_code']}": [c["value"] for c in r["plan"]]
        for r in sheet["rows"]
        if r["kind"] == "line" and r["measure"] == measure
    }


def row(sheet: dict, kind: str, measure: str, code: str | None = None) -> dict:  # type: ignore[type-arg]
    return next(
        r
        for r in sheet["rows"]
        if r["kind"] == kind
        and r["measure"] == measure
        and (code is None or r["staff_type_code"] == code)
    )


def versions_of(client: TestClient, line: str, measure: str) -> list[int | None]:
    t, c = line.split("/")
    found = next(
        r
        for r in client.get(SHEET).json()["rows"]
        if r["kind"] == "line"
        and r["measure"] == measure
        and (r["staff_type_code"], r["staff_category_code"]) == (t, c)
    )
    return [cell["row_version"] for cell in found["plan"]]


def edit(
    client: TestClient,
    line: str,
    measure: str,
    month: int | None,
    value: str | None,
    expected: list[int | None] = BLANK,
) -> object:
    t, c = line.split("/")
    body = {
        "staff_type_code": t,
        "staff_category_code": c,
        "measure": measure,
        "month": month,
        "value": value,
        "expected": expected,
    }
    return client.post(f"{SHEET}/edits", json={"edits": [body]})


def test_rounds_without_workforce_have_no_sheet(client: TestClient) -> None:
    assert client.get(SHEET).status_code == 404
    assert mode(client, "nonsense") == 422


def test_simple_mode_sheet_lines_actuals_and_year_end(client: TestClient) -> None:
    assert mode(client, "both") == 200
    sheet = client.get(SHEET).json()
    assert sheet["mode"] == "both"
    # Lines come from the prior year's actuals; both measures, FTE first.
    assert set(lines(sheet, "fte")) == {"EMP/TECH", "EMP/OFF", "FRL/TECH"}
    first = row(sheet, "line", "fte")  # rows in type order, categories by name
    assert (first["staff_type_code"], first["staff_category_code"]) == ("EMP", "OFF")
    assert first["comparisons"][0] == ["0.80"] * 12
    by = {
        f"{r['staff_type_code']}/{r['staff_category_code']}": r
        for r in sheet["rows"]
        if r["kind"] == "line" and r["measure"] == "headcount"
    }
    assert by["EMP/TECH"]["comparisons"][0] == ["2.00"] * 12
    assert by["FRL/TECH"]["external"] and by["EMP/TECH"]["editable"]
    # A year value fills every month (a level, not a flow); zero stays zero, blank blank.
    response = edit(client, "EMP/TECH", "headcount", None, "3")
    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    sheet = response.json()  # type: ignore[attr-defined]
    assert lines(sheet, "headcount")["EMP/TECH"] == ["3.00"] * 12
    edit(client, "EMP/TECH", "headcount", 11, "4", versions_of(client, "EMP/TECH", "headcount"))
    sheet = client.get(SHEET).json()
    assert lines(sheet, "headcount")["EMP/TECH"] == ["3.00"] * 11 + ["4.00"]
    edit(client, "EMP/OFF", "headcount", 0, "0")
    sheet = client.get(SHEET).json()
    assert lines(sheet, "headcount")["EMP/OFF"] == ["0.00"] + [None] * 11
    # Groups and the total sum their lines; the total's December is 4 + 0.
    assert row(sheet, "total", "headcount")["plan"][11]["value"] == "4.00"
    assert row(sheet, "group", "headcount", "EMP")["plan"][0]["value"] == "3.00"


def test_edit_rules(client: TestClient) -> None:
    mode(client, "fte")
    assert edit(client, "EMP/TECH", "headcount", 0, "1").status_code == 422  # type: ignore[attr-defined]
    assert edit(client, "EMP/TECH", "fte", 0, "-1").status_code == 422  # type: ignore[attr-defined]
    assert edit(client, "EMP/TECH", "fte", 0, "1.234").status_code == 422  # type: ignore[attr-defined]
    assert edit(client, "EMP/NOPE", "fte", 0, "1").status_code == 404  # type: ignore[attr-defined]
    assert edit(client, "EMP/TECH", "fte", 0, "1").status_code == 200  # type: ignore[attr-defined]
    # A stale row version is a conflict, and nothing is written.
    assert edit(client, "EMP/TECH", "fte", 0, "2").status_code == 409  # type: ignore[attr-defined]


def test_lines_added_by_a_planner(client: TestClient) -> None:
    mode(client, "headcount")
    login(client, OWNER)
    body = {"staff_type_code": "APP", "staff_category_code": "TECH"}
    sheet = client.post(f"{SHEET}/lines", json=body).json()
    assert lines(sheet, "headcount")["APP/TECH"] == BLANK
    assert client.post(f"{SHEET}/lines", json=body).status_code == 422


def test_rights_status_and_audit(engine: Engine, client: TestClient) -> None:
    mode(client, "headcount")
    login(client, VIEWER)
    assert client.get(SHEET).json()["lock_reason"] == "you may only read this cost center"
    assert edit(client, "EMP/TECH", "headcount", 0, "1").status_code == 423  # type: ignore[attr-defined]
    login(client, OWNER)
    response = edit(client, "EMP/TECH", "headcount", 0, "1")
    batch = response.json()["batch_id"]  # type: ignore[attr-defined]
    assert client.get(f"/api/versions/{R}/cost-centers").json()[0]["status"] == "in_progress"
    with engine.connect() as conn:
        logged = conn.execute(
            select(workforce_change_log.c.actor, workforce_change_log.c.new_amount).where(
                workforce_change_log.c.batch_id == batch
            )
        ).all()
    assert logged == [(OWNER, Decimal("1.00"))]
    undone = client.post(f"{SHEET}/undo", json={"batch_id": batch})
    assert undone.status_code == 200
    assert lines(undone.json(), "headcount")["EMP/TECH"] == BLANK
    client.post(f"/api/versions/{R}/workflow", json={"action": "submit", "cost_centers": ["CC1"]})
    assert edit(client, "EMP/TECH", "headcount", 1, "1").status_code == 423  # type: ignore[attr-defined]

    with unit_of_work(engine, access.system_user(engine)) as conn:
        ids = dict(conn.execute(select(member.c.code, member.c.member_id)).all())
        vid = conn.execute(select(version.c.version_id).where(version.c.code == R)).scalar_one()
        with pytest.raises(DBAPIError, match="the cost center is submitted"):
            conn.execute(
                insert(fact_workforce).values(
                    version_id=vid,
                    layer_id=1,
                    measure_id=4,
                    period=date(2026, 3, 1),
                    dim_entity=ids["DE"],
                    dim_cost_center=ids["CC1"],
                    dim_staff_type=ids["EMP"],
                    dim_staff_category=ids["TECH"],
                    amount=Decimal(1),
                )
            )


def position(**fields: object) -> dict[str, object]:
    return {
        "title": "Machinist",
        "staff_type_code": "EMP",
        "staff_category_code": "TECH",
        "contract_percent": "100",
        **fields,
    }


def test_positions_roll_up_and_build_up(engine: Engine, client: TestClient) -> None:
    mode(client, "positions")
    sheet = client.get(SHEET).json()
    # HR's people active in 2026: four of CC1's five (the 2024 leaver isn't).
    assert sheet["hr_available"] == 4
    sheet = client.post(f"{SHEET}/hr").json()
    assert len(sheet["positions"]) == 4 and sheet["hr_available"] == 0
    assert lines(sheet, "headcount")["EMP/TECH"] == ["2.00"] * 12
    assert lines(sheet, "fte")["EMP/TECH"] == ["1.50"] * 12
    # The planner leaves at the end of June: counted through June, then 0.
    assert lines(sheet, "headcount")["EMP/OFF"] == ["1.00"] * 6 + ["0.00"] * 6
    # The freelancer counts as a head; no contract %, so no FTE.
    assert lines(sheet, "headcount")["FRL/TECH"] == ["1.00"] * 12
    assert lines(sheet, "fte")["FRL/TECH"] == BLANK
    # A replacement for the planner from July, and a new machinist from 15 October at 80 %.
    replacement = position(
        title="Planner",
        staff_category_code="OFF",
        entry_date="2026-07-01",
        is_plan=True,
        is_replacement=True,
    )
    response = client.post(f"{SHEET}/positions", json=replacement)
    assert response.status_code == 200, response.text
    new = position(entry_date="2026-10-15", contract_percent="80", is_plan=True)
    sheet = client.post(f"{SHEET}/positions", json=new).json()
    assert lines(sheet, "headcount")["EMP/OFF"] == ["1.00"] * 12
    assert lines(sheet, "fte")["EMP/TECH"] == ["1.50"] * 9 + ["2.30"] * 3
    b = sheet["build_up"]
    assert b["leaving"][6] == 1 and b["replacements"][6] == 1 and b["new"][9] == 1
    assert sum(b["entering"]) == 2
    # Typed values are refused in a positions round.
    assert edit(client, "EMP/TECH", "fte", 0, "9").status_code == 422  # type: ignore[attr-defined]
    # Changing a position follows through; deleting it too.
    planned = next(
        p for p in sheet["positions"] if p["is_plan"] and p["staff_category_code"] == "TECH"
    )
    changed = client.put(
        f"{SHEET}/positions/{planned['position_id']}", json={**new, "contract_percent": "100"}
    ).json()
    assert lines(changed, "fte")["EMP/TECH"][11] == "2.50"
    gone = client.delete(f"{SHEET}/positions/{planned['position_id']}").json()
    assert lines(gone, "fte")["EMP/TECH"][11] == "1.50"
    with engine.connect() as conn:
        ops = (
            conn.execute(
                select(workforce_position_log.c.op).where(
                    workforce_position_log.c.position_id == planned["position_id"]
                )
            )
            .scalars()
            .all()
        )
    assert list(ops) == ["INSERT", "UPDATE", "DELETE"]


def test_position_rules(client: TestClient) -> None:
    mode(client, "positions")
    assert client.post(f"{SHEET}/positions", json=position(title=" ")).status_code == 422
    assert (
        client.post(f"{SHEET}/positions", json=position(contract_percent=None)).status_code == 422
    )
    freelancer = position(staff_type_code="FRL", contract_percent=None)
    assert client.post(f"{SHEET}/positions", json=freelancer).status_code == 200
    assert client.post(f"{SHEET}/positions", json=position(is_replacement=True)).status_code == 422
    dates = position(entry_date="2026-05-01", leave_date="2026-04-01")
    assert client.post(f"{SHEET}/positions", json=dates).status_code == 422


def test_positions_added_here_are_numbered(client: TestClient) -> None:
    mode(client, "positions")
    client.post(f"{SHEET}/positions", json=position())
    sheet = client.post(f"{SHEET}/positions", json=position(title="Planner")).json()
    assert sorted(p["code"] for p in sheet["positions"]) == ["N00001", "N00002"]
    # A number given by the client is ignored: Planlayer numbers positions added here.
    sheet = client.post(f"{SHEET}/positions", json={**position(), "code": "X1"}).json()
    assert "X1" not in {p["code"] for p in sheet["positions"]}
    assert all(p["origin"] == "manual" for p in sheet["positions"])


def test_numbers_count_positions_the_user_cant_read(client: TestClient) -> None:
    """The controller adds a position on CC2; CC1's owner, who reads only CC1, takes the next
    number, not the one CC2's position has."""
    mode(client, "positions")
    other = f"/api/versions/{R}/workforce/CC2/positions"
    assert client.post(other, json=position()).status_code == 200
    login(client, OWNER)
    sheet = client.post(f"{SHEET}/positions", json=position(title="Planner"))
    assert sheet.status_code == 200, sheet.text
    assert [p["code"] for p in sheet.json()["positions"] if p["origin"] == "manual"] == ["N00002"]


def test_positions_from_hr_keep_hrs_facts(engine: Engine, client: TestClient) -> None:
    mode(client, "positions")
    client.post(f"{SHEET}/hr")
    login(client, OWNER)
    sheet = client.get(SHEET).json()
    max_ = next(p for p in sheet["positions"] if p["code"] == "P001")
    assert max_["origin"] == "hr" and not max_["removable"]
    body = {
        **position(),
        "person_name": max_["person_name"],
        "entry_date": max_["entry_date"],
    }
    url = f"{SHEET}/positions/{max_['position_id']}"
    # Title, category, type, contract % and leave date are the planner's to change.
    changed = client.put(url, json={**body, "title": "Lead machinist", "contract_percent": "80",
                                    "leave_date": "2026-09-30"})  # fmt: skip
    assert changed.status_code == 200, changed.text
    assert lines(changed.json(), "fte")["EMP/TECH"][11] == "0.50"  # only Eva's 50 % is left
    # Name, entry date and the plan flags stay as HR has them.
    for wrong in ({"person_name": "Someone else"}, {"entry_date": "2016-01-01"},
                  {"is_plan": True}):  # fmt: skip
        assert client.put(url, json={**body, **wrong}).status_code == 422
    assert client.delete(url).status_code == 403
    login(client, CONTROLLER)
    assert client.delete(url).status_code == 200
    # The store refuses it too, whoever writes.
    refused = pytest.raises(DBAPIError, match="keeps its number, name and entry date")
    with unit_of_work(engine, access.system_user(engine)) as conn, refused:
        conn.execute(
            update(workforce_position)
            .where(workforce_position.c.code == "P002")
            .values(person_name="Renamed")
        )


def test_mode_changes(client: TestClient) -> None:
    mode(client, "headcount")
    edit(client, "EMP/TECH", "headcount", 0, "1")
    # The round is open and has workforce values: the mode can't change.
    assert mode(client, "fte") == 422
    assert mode(client, None) == 422
    edit(client, "EMP/TECH", "headcount", 0, None, versions_of(client, "EMP/TECH", "headcount"))
    assert lines(client.get(SHEET).json(), "headcount")["EMP/TECH"] == BLANK
    assert mode(client, "fte") == 200


def test_a_new_round_carries_positions(client: TestClient) -> None:
    mode(client, "positions")
    client.post(f"{SHEET}/hr")
    new = {"code": "B2027", "name": "Budget 2027", "kind": "budget", "fiscal_year": 2027,
           "copy_from": R}  # fmt: skip
    assert client.post("/api/rounds", json=new).status_code == 200
    sheet = client.get("/api/versions/B2027/workforce/CC1").json()
    assert sheet["mode"] == "positions"
    # The planner who leaves in June 2026 stays behind.
    assert len(sheet["positions"]) == 3
    assert lines(sheet, "headcount")["EMP/TECH"] == ["2.00"] * 12
    assert "EMP/OFF" not in lines(sheet, "headcount")


def test_typed_values_carry_into_a_forecasts_open_months(client: TestClient) -> None:
    mode(client, "headcount")
    edit(client, "EMP/TECH", "headcount", None, "5")
    new = {"code": "F2026", "name": "Forecast", "kind": "forecast", "fiscal_year": 2026,
           "copy_from": R, "actuals_through": "2026-03-01",
           "carry": {"values": True}}  # fmt: skip
    assert client.post("/api/rounds", json=new).status_code == 200
    sheet = client.get("/api/versions/F2026/workforce/CC1").json()
    assert sheet["closed_months"] == 3
    # No 2026 actuals in the fixture: the closed months are blank, the open ones carried.
    assert lines(sheet, "headcount")["EMP/TECH"] == [None] * 3 + ["5.00"] * 9


def test_publish_aggregates_without_names(client: TestClient, migrated: Settings) -> None:
    mode(client, "positions")
    client.post(f"{SHEET}/hr")
    p = client.get(f"/api/rounds/{R}/publish/preview", params={"scope": "all"}).json()
    # Three lines x two measures x 12 months; the freelancer's FTE stays blank.
    assert p["workforce_rows"] == 3 * 2 * 12 - 12
    body = {"scope": "all", "fingerprint": p["fingerprint"]}
    assert client.post(f"/api/rounds/{R}/publish", json=body).status_code == 200
    output = create_output_engine(migrated)
    try:
        with output.connect() as conn:
            december = dict(
                conn.execute(
                    select(fact_plan_workforce.c.measure, func.sum(fact_plan_workforce.c.amount))
                    .where(
                        fact_plan_workforce.c.version_code == R,
                        fact_plan_workforce.c.period == date(2026, 12, 1),
                    )
                    .group_by(fact_plan_workforce.c.measure)
                ).all()
            )
            columns = {
                c["name"]
                for c in inspect(conn).get_columns("v_plan_workforce", migrated.output_schema)
            }
    finally:
        output.dispose()
    # December: two machinists and the freelancer; the planner has left.
    assert december == {"headcount": Decimal("3.00"), "fte": Decimal("1.50")}
    assert not {"person_name", "title"} & columns


def test_a_mode_change_sees_every_cost_centers_positions(
    engine: Engine, client: TestClient
) -> None:
    mode(client, "positions")
    client.post(f"{SHEET}/hr")  # positions on CC1
    limited = limited_controller(engine)
    with unit_of_work(engine, limited) as conn:
        r = get_round(conn, R)
        with pytest.raises(RoundError, match="only in setup"):
            set_mode(conn, r, "fte")
