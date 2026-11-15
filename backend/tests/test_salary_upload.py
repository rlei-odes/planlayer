"""HR's salary upload (ADR 0025): the admin's switch for salaries from the source, the template,
the check that writes nothing, and applying it to the round's positions and HR's staff list.

The fixture's HR list (see test_personnel): CC1 has P001 (4000), P002 (2000), P003 (3200,
leaving end of June) and P004 (a freelancer, 9000); P005 left in 2024 and isn't taken; CC2 has
P009 (3500), not taken until a test takes it.
"""

import csv
import io
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal

import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from planlayer import access
from planlayer.config import Settings
from planlayer.personnel.salary_files import FileError, read
from planlayer.store.db import create_app_engine
from planlayer.store.schema import hr_pay, position_pay_log
from planlayer.store.uow import system_rights, unit_of_work

from .conftest import CONTROLLER, OWNER, login
from .test_personnel import HR, PAY, R, account, by_code, start

ADMIN = "admin@x.example"
SALARIES = f"{PAY}/salaries"


@pytest.fixture
def engine(migrated: Settings, client: TestClient) -> Iterator[Engine]:
    e = create_app_engine(migrated)
    admin = replace(access.system_user(e), is_admin=True)
    access.create_user(e, admin, HR, "HR", is_hr=True)
    access.create_user(e, admin, ADMIN, "Admin", is_admin=True)
    yield e
    e.dispose()


def source_pay(client: TestClient, on: bool) -> None:
    login(client, ADMIN)
    response = client.put("/api/datasets/staff/pay-from-source", json={"on": on})
    assert response.status_code == 200, response.text


def as_csv(*rows: tuple[str, ...], header: tuple[str, ...] = ("number", "monthly_salary")) -> bytes:
    out = io.StringIO()
    csv.writer(out).writerows([header, *rows])
    return out.getvalue().encode()


def upload(client: TestClient, data: bytes, step: str = "check", name: str = "s.csv") -> dict:  # type: ignore[type-arg]
    response = client.post(
        f"{SALARIES}/{step}",
        params={"filename": name},
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def outcomes(result: dict) -> list[tuple[str | None, str]]:  # type: ignore[type-arg]
    return [(r["number"], r["outcome"]) for r in result["rows"]]


def test_the_admin_switches_salaries_from_the_source(engine: Engine, client: TestClient) -> None:
    login(client, ADMIN)
    staff = next(d for d in client.get("/api/datasets").json() if d["dataset"] == "staff")
    assert staff["pay_fields"] == ["monthly_salary"] and staff["pay_from_source"] is True
    login(client, CONTROLLER)
    start(client)
    login(client, HR)
    info = client.get(SALARIES).json()
    assert info["pay_from_source"] is True and info["reason"]
    # While the source delivers salaries, HR doesn't upload them.
    response = client.post(
        f"{SALARIES}/check", params={"filename": "s.csv"}, content=as_csv(("P001", "1"))
    )
    assert response.status_code == 422
    source_pay(client, False)
    # Off: the mapped preview leaves pay out, and a sync leaves HR's salaries as they are.
    mapping = staff["mapping"]
    preview = client.post("/api/datasets/staff/preview", json={**mapping, "mapped": True}).json()
    assert "monthly_salary" not in preview["columns"] and preview["rows"]
    with unit_of_work(engine, access.system_user(engine)) as conn:
        before = dict(conn.execute(select(hr_pay.c.code, hr_pay.c.monthly_salary)).all())
    assert client.post("/api/sync/run", json={}).status_code == 200
    with unit_of_work(engine, access.system_user(engine)) as conn:
        after = dict(conn.execute(select(hr_pay.c.code, hr_pay.c.monthly_salary)).all())
    assert after == before and after["P001"] == Decimal("4000.00")
    login(client, HR)
    info = client.get(SALARIES).json()
    assert info["pay_from_source"] is False and info["reason"] is None
    assert info["people"] == 6 and info["with_salary"] == 4
    # The admin and HR only.
    login(client, CONTROLLER)
    assert client.put("/api/datasets/staff/pay-from-source", json={"on": True}).status_code == 403
    assert client.get(SALARIES).status_code == 403


def test_template_in_three_formats(engine: Engine, client: TestClient) -> None:
    start(client)
    source_pay(client, False)
    login(client, HR)
    text = client.get(f"{SALARIES}/template.csv").content.decode("utf-8-sig")
    lines = list(csv.reader(io.StringIO(text)))
    assert lines[0] == ["number", "name", "title", "cost_center", "monthly_salary"]
    # Everyone on HR's staff list and in the round, by cost center; no pay in the file.
    assert [r[0] for r in lines[1:]] == ["P001", "P002", "P003", "P004", "P005", "P009"]
    assert lines[1] == ["P001", "Max Muster", "Machinist", "CC1", ""]
    assert all(r[4] == "" for r in lines[1:])
    wb = openpyxl.load_workbook(io.BytesIO(client.get(f"{SALARIES}/template.xlsx").content))
    ws = wb.worksheets[0]
    assert [c.value for c in ws[1]] == ["number", "name", "title", "cost_center", "monthly_salary"]
    assert ws.cell(7, 1).value == "P009" and ws.cell(7, 5).value is None
    table = pq.read_table(io.BytesIO(client.get(f"{SALARIES}/template.parquet").content))
    assert table.column("number").to_pylist()[-1] == "P009"
    assert table.schema.field("monthly_salary").type == pa.decimal128(18, 2)
    login(client, OWNER)
    assert client.get(f"{SALARIES}/template.csv").status_code == 403


def test_check_then_apply(engine: Engine, client: TestClient) -> None:
    start(client)
    source_pay(client, False)
    login(client, HR)
    data = as_csv(
        ("P001", "4100", "CC1"),
        ("P002", "2000", ""),  # as it is
        ("P003", "", "CC1"),  # blank: left as it is
        ("P004", "abc", ""),
        ("P005", "4600", "CC1"),  # left in 2024: not in the round
        ("P009", "3600", "CC9"),  # on CC2 in HR's list
        ("X999", "1000", ""),
        ("P002", "", ""),  # the number twice
        header=("number", "monthly_salary", "cost_center"),
    )
    result = upload(client, data)
    assert result["applied"] is False
    assert outcomes(result) == [
        ("P001", "changed"),
        ("P002", "failed"),
        ("P003", "blank"),
        ("P004", "failed"),
        ("P005", "not_in_round"),
        ("P009", "not_in_round"),
        ("X999", "unknown"),
        ("P002", "failed"),
    ]
    rows = {r["line"]: r for r in result["rows"]}
    assert rows[2]["old"] == "4000.00" and rows[2]["new"] == "4100.00"
    assert rows[2]["name"] == "Max Muster" and rows[2]["file_cost_center"] is None
    assert "line 9" in rows[3]["message"] and "line 3" in rows[9]["message"]
    assert rows[9]["name"] == "Eva Teil"  # a doubled number still names the person
    assert "not an amount" in rows[5]["message"]
    assert rows[7]["cost_center"] == "CC2" and rows[7]["file_cost_center"] == "CC9"
    assert result["positions"] == 1 and result["staff_list"] == 3
    # The check wrote nothing.
    assert by_code(client.get(f"{PAY}/CC1").json())["P001"]["hr_salary"] == "4000.00"

    result = upload(client, data, "apply")
    assert result["applied"] is True and result["batch_id"]
    page = client.get(f"{PAY}/CC1").json()
    assert by_code(page)["P001"]["hr_salary"] == "4100.00"
    assert by_code(page)["P003"]["hr_salary"] == "3200.00"
    # The totals follow: P001 +100 a month on the employees' account.
    assert account(page, "6000") == ["9300.00"] * 6 + ["6100.00"] * 6
    with unit_of_work(engine, access.system_user(engine)) as conn, system_rights(conn):
        log = conn.execute(
            select(position_pay_log.c.batch_id).where(
                position_pay_log.c.batch_id == result["batch_id"]
            )
        ).all()
        salaries = dict(conn.execute(select(hr_pay.c.code, hr_pay.c.monthly_salary)).all())
    assert len(log) == 1
    assert salaries["P001"] == Decimal("4100.00") and salaries["P005"] == Decimal("4600.00")
    # People taken later get HR's uploaded salary.
    login(client, CONTROLLER)
    assert client.post(f"/api/versions/{R}/workforce/CC2/hr").status_code == 200
    login(client, HR)
    assert by_code(client.get(f"{PAY}/CC2").json())["P009"]["hr_salary"] == "3600.00"
    # Uploaded again, nothing changes.
    again = upload(client, data)
    assert again["positions"] == 0 and again["staff_list"] == 0


def test_a_submitted_cost_center_isnt_changed(engine: Engine, client: TestClient) -> None:
    start(client)
    source_pay(client, False)
    login(client, OWNER)
    act = {"action": "submit", "cost_centers": ["CC1"]}
    assert client.post(f"/api/versions/{R}/workflow", json=act).status_code == 200
    login(client, HR)
    result = upload(client, as_csv(("P001", "4100"), ("P002", "2000")), "apply")
    rows = result["rows"]
    assert rows[0]["outcome"] == "failed" and "CC1 is submitted" in rows[0]["message"]
    assert rows[1]["outcome"] == "unchanged"
    assert result["positions"] == 0 and result["staff_list"] == 0
    assert by_code(client.get(f"{PAY}/CC1").json())["P001"]["hr_salary"] == "4000.00"


def test_excel_and_parquet_uploads(engine: Engine, client: TestClient) -> None:
    start(client)
    source_pay(client, False)
    login(client, HR)
    wb = openpyxl.load_workbook(io.BytesIO(client.get(f"{SALARIES}/template.xlsx").content))
    ws = wb.worksheets[0]
    ws.cell(2, 5).value = 4250.5  # P001, a number
    ws.cell(3, 5).value = "2.100,00"  # P002, typed as text
    buf = io.BytesIO()
    wb.save(buf)
    result = upload(client, buf.getvalue(), name="salaries.xlsx")
    changed = {r["number"]: r["new"] for r in result["rows"] if r["outcome"] == "changed"}
    assert changed == {"P001": "4250.50", "P002": "2100.00"}

    table = pa.table(
        {
            "number": ["P001", "P002"],
            "monthly_salary": pa.array([Decimal("4300.00"), None], pa.decimal128(18, 2)),
            "remark": ["a", "b"],
        }
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    result = upload(client, buf.getvalue(), "apply", name="salaries.parquet")
    assert result["ignored"] == ["remark"]
    assert by_code(client.get(f"{PAY}/CC1").json())["P001"]["hr_salary"] == "4300.00"


def test_files_that_cant_be_read() -> None:
    with pytest.raises(FileError, match="no column monthly_salary"):
        read(b"number;salary\nP001;100\n", "s.csv")
    with pytest.raises(FileError, match="CSV, Excel"):
        read(b"x", "s.xls")
    with pytest.raises(FileError, match="can't be read as Excel"):
        read(b"not a workbook", "s.xlsx")
    f = read(
        (
            "Number;Monthly salary;Cost-center\nP001;4.500,50;CC1\nP002;-1;\nP003;12'345;\n"
            "P005;12.345;\n;;\nP004;4500.123;\n"
        ).encode("cp1252"),
        "s.csv",
    )
    assert [(r.line, r.salary, r.error is None) for r in f.rows] == [
        (2, Decimal("4500.50"), True),
        (3, None, False),
        (4, Decimal("12345.00"), True),
        (5, None, False),
        (7, None, False),
    ]
    assert f.rows[0].cost_center == "CC1"
