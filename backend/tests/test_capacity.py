"""Productive cost centers and capacity planning (ADR 0013).

The fixture source gives CC1 a lathe (WC1, machine hours at 80) and a mill (WC2: machine hours at
100.50, repair work at 60), and CC2 a press (WC9). Machine hours credit account 4900; repair
work names no account. The lathe ran 100 hours a month in 2025, 20 in August.
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import DBAPIError

from planlayer import access
from planlayer.config import Settings
from planlayer.store.db import create_app_engine
from planlayer.store.schema import (
    capacity_change_log,
    change_log,
    fact_capacity,
    fact_cost_center,
    member,
    version,
    work_center_rate,
)
from planlayer.store.uow import unit_of_work

from .conftest import BLANK, CONTROLLER, OWNER, login, plan, rows

R = "B2026"
SHEET = f"/api/versions/{R}/capacity/CC1"
CAPACITY = "cap@x.example"


@pytest.fixture
def engine(migrated: Settings, client: TestClient) -> Iterator[Engine]:
    e = create_app_engine(migrated)
    yield e
    e.dispose()


def productive(client: TestClient, *codes: str) -> int:
    setup = client.get(f"/api/rounds/{R}/setup").json()["setup"]
    return client.put(
        f"/api/rounds/{R}/setup", json={**setup, "productive_cost_centers": list(codes)}
    ).status_code


def lines(sheet: dict) -> dict[str, dict]:  # type: ignore[type-arg]
    return {
        f"{r['work_center_code']}/{r['activity_type_code']}": r
        for r in sheet["rows"]
        if r["kind"] == "line"
    }


def credits(sheet: dict) -> dict[str, list[str | None]]:  # type: ignore[type-arg]
    return {
        r["activity_type_code"]: [c["value"] for c in r["plan"]]
        for r in sheet["rows"]
        if r["kind"] == "credit"
    }


def hours(
    client: TestClient,
    wc: str,
    at: str,
    month: int | None,
    value: str | None,
    expected: list[int | None] = BLANK,
) -> object:
    edit = {
        "work_center_code": wc,
        "activity_type_code": at,
        "month": month,
        "value": value,
        "expected": expected,
    }
    return client.post(f"{SHEET}/edits", json={"edits": [edit]})


def test_sync_brings_work_centers_rates_and_actual_hours(engine: Engine) -> None:
    with unit_of_work(engine, access.system_user(engine)) as conn:
        cc = dict(
            conn.execute(
                select(member.c.code, member.c.cost_center_code).where(member.c.dim_id == 4)
            ).all()
        )
        assert cc == {"WC1": "CC1", "WC2": "CC1", "WC9": "CC2"}
        assert len(conn.execute(select(work_center_rate)).all()) == 4
        actual = conn.execute(
            select(fact_capacity.c.amount)
            .join(version, version.c.version_id == fact_capacity.c.version_id)
            .where(version.c.kind == "actuals")
        ).scalars()
        assert sum(actual) == Decimal(1120)


def test_productive_cost_centers_get_a_capacity_sheet(client: TestClient) -> None:
    assert client.get(SHEET).status_code == 404  # not productive yet
    assert productive(client, "CC1") == 200
    assert client.get(f"/api/rounds/{R}/setup").json()["setup"]["productive_cost_centers"] == [
        "CC1"
    ]
    assert [u["code"] for u in client.get(f"/api/versions/{R}/capacity").json()] == ["CC1"]
    sheet = client.get(SHEET).json()
    assert [(r["kind"], r["activity_type_code"]) for r in sheet["rows"]] == [
        ("group", "MH"),
        ("line", "MH"),
        ("line", "MH"),
        ("credit", "MH"),
        ("group", "RW"),
        ("line", "RW"),
        ("credit", "RW"),
    ]
    lathe = lines(sheet)["WC1/MH"]
    assert (lathe["rate"], lathe["basis"], lathe["comparisons"][0][7]) == (
        "80.00",
        "prior-year actual hours",
        "20.00",
    )
    assert lines(sheet)["WC2/MH"]["rate"] == "100.50"
    credit_rows = [r for r in sheet["rows"] if r["kind"] == "credit"]
    assert [r["account_code"] for r in credit_rows] == ["4900", None]
    assert [w["code"] for w in sheet["work_centers"]] == ["WC1", "WC2"]
    assert productive(client, "CC404") == 404


def test_hours_spread_and_credit_the_cost_center(engine: Engine, client: TestClient) -> None:
    productive(client, "CC1")
    response = hours(client, "WC1", "MH", None, "1140")
    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    sheet = response.json()  # type: ignore[attr-defined]
    # Like the prior year: 100 a month, 20 in August (1120 in all).
    months = [c["value"] for c in lines(sheet)["WC1/MH"]["plan"]]
    assert months[0] == "101.79" and months[7] == "20.36"
    assert sum(Decimal(v) for v in months) == Decimal(1140)
    assert credits(sheet)["MH"][7] == "1628.80"  # 20.36 h x 80
    assert credits(sheet)["RW"] == [None] * 12

    # The cost sheet shows the credit, read only, and refuses edits to it.
    grid = client.get(f"/api/versions/{R}/cost-centers/CC1/grid").json()
    row = rows(grid)["4900"]
    assert (row["capacity"], row["total_editable"], row["months_editable"]) == (True, False, False)
    assert plan(grid, "4900")[7] == "-1628.80"  # credits show negative (ADR 0018)
    edit = {"account_code": "4900", "month": 0, "value": "1", "expected": BLANK}
    refused = client.post(f"/api/versions/{R}/cost-centers/CC1/edits", json={"edits": [edit]})
    assert refused.status_code == 422 and "capacity" in refused.json()["detail"]

    # Hours and credits are one change batch.
    batch = sheet["batch_id"]
    with unit_of_work(engine, access.system_user(engine)) as conn:
        logged = conn.execute(
            select(change_log.c.batch_id).where(change_log.c.batch_id == batch)
        ).all()
        hours_logged = conn.execute(
            select(capacity_change_log.c.batch_id).where(capacity_change_log.c.batch_id == batch)
        ).all()
    assert len(logged) == 12 and len(hours_logged) == 12

    # A new rate recomputes the credits; blank hours remove them.
    sheet = client.patch(f"{SHEET}/lines/WC1/MH", json={"rate": "90"}).json()
    assert (lines(sheet)["WC1/MH"]["rate"], credits(sheet)["MH"][7]) == ("90.00", "1832.40")
    seen = [c["row_version"] for c in lines(sheet)["WC1/MH"]["plan"]]
    assert hours(client, "WC1", "MH", None, None).status_code == 409  # type: ignore[attr-defined]
    sheet = hours(client, "WC1", "MH", None, None, seen).json()  # type: ignore[attr-defined]
    assert credits(sheet)["MH"] == [None] * 12
    # Nothing credited any more: the account leaves the cost sheet, as any account without values.
    assert "4900" not in rows(client.get(f"/api/versions/{R}/cost-centers/CC1/grid").json())


def test_line_spread_and_states(client: TestClient) -> None:
    productive(client, "CC1")
    client.patch(f"{SHEET}/lines/WC2/MH", json={"spread": {"basis": "even"}})
    sheet = hours(client, "WC2", "MH", None, "1200").json()  # type: ignore[attr-defined]
    mill = lines(sheet)["WC2/MH"]
    assert mill["chosen_spread"] == {"basis": "even", "profile_code": None}
    assert [c["value"] for c in mill["plan"]] == ["100.00"] * 12
    assert credits(sheet)["MH"][0] == "10050.00"
    # No prior-year hours: the default spread falls back to even, and says so.
    assert lines(sheet)["WC2/RW"]["fallback"] is True
    assert hours(client, "WC2", "MH", 0, "-1").status_code == 422  # type: ignore[attr-defined]

    # A controller sets a line inactive: it leaves the sheet and stops crediting.
    sheet = client.patch(f"{SHEET}/lines/WC2/MH", json={"active": False}).json()
    assert "WC2/MH" not in lines(sheet) and credits(sheet)["MH"] == [None] * 12
    assert sheet["inactive"][0]["work_center_code"] == "WC2"
    assert hours(client, "WC2", "MH", 0, "1").status_code == 422  # type: ignore[attr-defined]
    login(client, OWNER)
    assert client.patch(f"{SHEET}/lines/WC2/MH", json={"active": True}).status_code == 403
    login(client, CONTROLLER)
    sheet = client.patch(f"{SHEET}/lines/WC2/MH", json={"active": True}).json()
    assert credits(sheet)["MH"][0] == "10050.00"


def test_planners_add_lines_and_new_work_centers(engine: Engine, client: TestClient) -> None:
    productive(client, "CC1")
    login(client, OWNER)
    new = {
        "work_center_code": "WC_LASER",
        "activity_type_code": "MH",
        "rate": "120,5",
        "new_work_center_name": "Laser (to buy)",
    }
    sheet = client.post(f"{SHEET}/lines", json=new).json()
    laser = lines(sheet)["WC_LASER/MH"]
    assert (laser["rate"], laser["added"], laser["comparisons"][0]) == ("120.50", True, [None] * 12)
    # No history: it spreads evenly, without a warning, unless the line chooses otherwise.
    assert (laser["spread"], laser["fallback"], laser["chosen_spread"]) == ("even", False, None)
    assert client.post(f"{SHEET}/lines", json=new).status_code == 422  # exists
    other = {"work_center_code": "WC9", "activity_type_code": "MH", "rate": "1"}
    refused = client.post(f"{SHEET}/lines", json=other)
    assert refused.status_code == 422 and "CC2" in refused.json()["detail"]
    with unit_of_work(engine, access.system_user(engine)) as conn:
        origin, cc = conn.execute(
            select(member.c.origin, member.c.cost_center_code).where(member.c.code == "WC_LASER")
        ).one()
    assert (origin, cc) == ("manual", "CC1")


def test_capacity_planner(engine: Engine, client: TestClient) -> None:
    productive(client, "CC1")
    admin = replace(access.system_user(engine), is_admin=True)
    access.create_user(engine, admin, CAPACITY, "Cap")
    assert (
        client.post(
            "/api/responsibilities/CC1/capacity-planners", json={"login": CAPACITY}
        ).status_code
        == 200
    )
    responsibility = next(
        r for r in client.get("/api/responsibilities").json()["rows"] if r["code"] == "CC1"
    )
    assert responsibility["capacity_planners"] == [CAPACITY]

    login(client, CAPACITY)
    assert [
        (u["code"], u["can_write"]) for u in client.get(f"/api/versions/{R}/capacity").json()
    ] == [("CC1", True)]
    sheet = hours(client, "WC1", "MH", 0, "10").json()  # type: ignore[attr-defined]
    assert credits(sheet)["MH"][0] == "800.00"
    # They read the cost sheet, but don't plan costs.
    grid = client.get(f"/api/versions/{R}/cost-centers/CC1/grid").json()
    assert grid["lock_reason"] == "you may only read this cost center"
    assert plan(grid, "4900")[0] == "-800.00"

    # The database lets them write credit accounts only.
    with engine.connect() as conn:
        user = access.user_by_login(conn, CAPACITY)
    with unit_of_work(engine, access.system_user(engine)) as conn:
        ids = dict(conn.execute(select(member.c.code, member.c.member_id)).all())
        vid = conn.execute(select(version.c.version_id).where(version.c.code == R)).scalar_one()
    cell = {
        "version_id": vid,
        "layer_id": 1,
        "measure_id": 1,
        "period": date(2026, 2, 1),
        "dim_entity": ids["DE"],
        "dim_cost_center": ids["CC1"],
        "amount": Decimal(1),
    }
    with pytest.raises(DBAPIError, match="row-level security"), unit_of_work(engine, user) as conn:
        conn.execute(insert(fact_cost_center).values(**cell, dim_account=ids["6000"]))
    with unit_of_work(engine, user) as conn:
        conn.execute(insert(fact_cost_center).values(**cell, dim_account=ids["4900"]))


def test_locks(engine: Engine, client: TestClient) -> None:
    productive(client, "CC1")
    hours(client, "WC1", "MH", 0, "10")
    assert client.get(f"/api/versions/{R}/cost-centers").json()[0]["status"] == "in_progress"
    # Capacity planned: CC1 can't stop being productive.
    refused = productive(client)
    assert refused == 422
    client.post(f"/api/versions/{R}/workflow", json={"action": "submit", "cost_centers": ["CC1"]})
    assert hours(client, "WC1", "MH", 1, "10").status_code == 423  # type: ignore[attr-defined]
    assert client.patch(f"{SHEET}/lines/WC1/MH", json={"rate": "1"}).status_code == 423

    def raw(cc: str) -> None:
        with unit_of_work(engine, access.system_user(engine)) as conn:
            ids = dict(conn.execute(select(member.c.code, member.c.member_id)).all())
            vid = conn.execute(select(version.c.version_id).where(version.c.code == R)).scalar_one()
            conn.execute(
                insert(fact_capacity).values(
                    version_id=vid,
                    layer_id=1,
                    measure_id=2,
                    period=date(2026, 3, 1),
                    dim_entity=ids["DE"],
                    dim_cost_center=ids[cc],
                    dim_work_center=ids["WC9" if cc == "CC2" else "WC1"],
                    dim_activity_type=ids["MH"],
                    amount=Decimal(1),
                )
            )

    with pytest.raises(DBAPIError, match="the cost center is submitted"):
        raw("CC1")
    with pytest.raises(DBAPIError, match="not productive"):
        raw("CC2")


def test_a_new_round_copies_productive_cost_centers(client: TestClient) -> None:
    productive(client, "CC1")
    new = {"code": "F2026", "name": "Forecast", "kind": "forecast", "fiscal_year": 2026,
           "copy_from": R}  # fmt: skip
    assert client.post("/api/rounds", json=new).status_code == 200
    setup = client.get("/api/rounds/F2026/setup").json()["setup"]
    assert setup["productive_cost_centers"] == ["CC1"]


def undo(client: TestClient, batch: str) -> object:
    return client.post(f"{SHEET}/undo", json={"batch_id": batch})


def test_undo_and_redo(client: TestClient) -> None:
    productive(client, "CC1")
    edited = hours(client, "WC1", "MH", None, "1140").json()  # type: ignore[attr-defined]
    rated = client.patch(f"{SHEET}/lines/WC1/MH", json={"rate": "90"}).json()
    assert credits(rated)["MH"][7] == "1832.40"

    # Undo the rate: back to 80, credits follow.
    sheet = undo(client, rated["batch_id"]).json()  # type: ignore[attr-defined]
    assert (lines(sheet)["WC1/MH"]["rate"], credits(sheet)["MH"][7]) == ("80.00", "1628.80")
    # Then the hours: nothing changed them since, so the batch can be undone too.
    sheet = undo(client, edited["batch_id"]).json()  # type: ignore[attr-defined]
    assert lines(sheet)["WC1/MH"]["plan"][0]["value"] is None
    assert credits(sheet)["MH"] == [None] * 12
    # Undoing the undo redoes it.
    sheet = undo(client, sheet["batch_id"]).json()  # type: ignore[attr-defined]
    assert lines(sheet)["WC1/MH"]["plan"][0]["value"] == "101.79"
    assert credits(sheet)["MH"][7] == "1628.80"


def test_undo_is_refused_after_later_changes(client: TestClient) -> None:
    productive(client, "CC1")
    first = hours(client, "WC1", "MH", 0, "10").json()  # type: ignore[attr-defined]
    seen = [c["row_version"] for c in lines(first)["WC1/MH"]["plan"]]
    hours(client, "WC1", "MH", 0, "20", seen)
    refused = undo(client, first["batch_id"])
    assert refused.status_code == 409  # type: ignore[attr-defined]
    assert refused.json() == [{"account_code": "WC1 / MH", "month": 0}]  # type: ignore[attr-defined]
    assert undo(client, "nope").status_code == 404  # type: ignore[attr-defined]
    login(client, OWNER)  # someone else's batch is not theirs to undo
    assert undo(client, first["batch_id"]).status_code == 404  # type: ignore[attr-defined]


def test_undo_line_changes(client: TestClient) -> None:
    productive(client, "CC1")
    new = {"work_center_code": "WC_NEW", "activity_type_code": "MH", "rate": "50",
           "new_work_center_name": "New"}  # fmt: skip
    added = client.post(f"{SHEET}/lines", json=new).json()
    assert "WC_NEW/MH" in lines(added)
    assert "WC_NEW/MH" not in lines(undo(client, added["batch_id"]).json())  # type: ignore[attr-defined]
    off = client.patch(f"{SHEET}/lines/WC2/MH", json={"active": False}).json()
    assert "WC2/MH" in lines(undo(client, off["batch_id"]).json())  # type: ignore[attr-defined]
