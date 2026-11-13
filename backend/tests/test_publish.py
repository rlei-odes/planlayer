"""Publishing (ADR 0011): files, preview, run into the output schema, refusal, republish.

The ``client`` fixture's output schema is the test's own; CC1 has synced actuals, the round
B2026 is open and the controller is logged in.
"""

import csv
import io
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from planlayer import access, publish
from planlayer.config import Settings
from planlayer.store.db import create_app_engine as create_engine_for
from planlayer.store.output import (
    create_output_engine,
    dim_account,
    dim_cost_center,
    fact_plan_cost_center,
)

from .conftest import BLANK, CONTROLLER, OWNER, URL, login, versions_of

P = "/api/rounds/B2026"


@pytest.fixture
def output(migrated: Settings) -> Iterator[Engine]:
    e = create_output_engine(migrated)
    yield e
    e.dispose()


def plan_cc1(client: TestClient, value: str = "1200") -> None:
    edit = {"account_code": "6000", "month": None, "value": value, "expected": BLANK}
    assert client.post(f"{URL}/edits", json={"edits": [edit]}).status_code == 200


def act(client: TestClient, action: str, cc: str = "CC1", comment: str | None = None) -> None:
    body = {"action": action, "cost_centers": [cc], "comment": comment}
    assert client.post("/api/versions/B2026/workflow", json=body).status_code == 200


def preview(client: TestClient, scope: str = "approved") -> dict:  # type: ignore[type-arg]
    return client.get(f"{P}/publish/preview", params={"scope": scope}).json()  # type: ignore[no-any-return]


def run(client: TestClient, p: dict, scope: str = "approved") -> object:  # type: ignore[type-arg]
    return client.post(f"{P}/publish", json={"scope": scope, "fingerprint": p["fingerprint"]})


def test_preview_run_and_repeat(client: TestClient, output: Engine) -> None:
    plan_cc1(client)
    assert preview(client)["rows"] == 0  # nothing approved yet
    act(client, "submit")
    act(client, "approve")
    p = preview(client)
    assert (p["rows"], p["added"], p["changed"], p["removed"]) == (12, 12, 0, 0)
    assert p["cost_centers"] == [
        {"code": "CC1", "added": 12, "changed": 0, "removed": 0, "unchanged": 0}
    ]
    done = run(client, p).json()  # type: ignore[attr-defined]
    assert done["added"] == 12
    with output.connect() as conn:
        rows = conn.execute(select(fact_plan_cost_center)).all()
        dims = conn.execute(select(dim_cost_center.c.code, dim_cost_center.c.level_1_code)).all()
    assert len(rows) == 12 and {r.status for r in rows} == {"approved"}
    assert sum(r.amount for r in rows) == Decimal("1200.00")
    assert ("CC1", "NW") in [tuple(d) for d in dims]
    again = preview(client)
    assert (again["added"], again["changed"], again["unchanged"]) == (0, 0, 12)
    history = client.get(f"{P}/publish/runs").json()
    assert history[0]["added"] == 12 and history[0]["finished_at"] is not None


def test_run_refuses_when_something_changed(client: TestClient) -> None:
    plan_cc1(client)
    p = preview(client, "all")
    grid = client.get(f"{URL}/grid").json()
    edit = {"account_code": "6000", "month": 0, "value": "1", "expected": versions_of(grid, "6000")}
    client.post(f"{URL}/edits", json={"edits": [edit]})
    response = run(client, p, "all")
    assert response.status_code == 409  # type: ignore[attr-defined]
    assert "preview again" in response.json()["detail"]  # type: ignore[attr-defined]
    assert run(client, preview(client, "all"), "all").status_code == 200  # type: ignore[attr-defined]


def test_approved_only_removes_reopened_cost_centers(client: TestClient) -> None:
    plan_cc1(client)
    act(client, "submit")
    act(client, "approve")
    run(client, preview(client))
    act(client, "reopen", comment="restated")
    p = preview(client)
    assert (p["removed"], p["rows"]) == (12, 0)
    assert p["changes"][0]["kind"] == "removed" and p["changes"][0]["new"] is None
    whole = preview(client, "all")
    assert (whole["changed"], whole["unchanged"]) == (12, 0)  # the status changed


def test_files(client: TestClient) -> None:
    plan_cc1(client, "12")
    text = client.get(f"{P}/export.csv", params={"scope": "all"})
    assert text.headers["content-disposition"] == 'attachment; filename="B2026-all.csv"'
    rows = list(csv.DictReader(io.StringIO(text.text)))
    assert len(rows) == 12 and rows[0]["amount"] == "1.00" and rows[0]["status"] == "in_progress"
    table = pq.read_table(io.BytesIO(client.get(f"{P}/export.parquet?scope=all").content))
    assert table.num_rows == 12 and str(table.schema.field("amount").type) == "decimal128(18, 2)"
    assert client.get(f"{P}/export.csv").text.strip().count("\n") == 0  # approved only: header


def test_republish_all(client: TestClient, migrated: Settings, output: Engine) -> None:
    from planlayer.store.db import create_app_engine

    plan_cc1(client)
    run(client, preview(client, "all"), "all")
    with output.begin() as conn:
        conn.execute(fact_plan_cost_center.delete())
    engine = create_app_engine(migrated)
    codes = publish.republish_all(
        engine, output, migrated.output_schema, access.system_user(engine)
    )
    engine.dispose()
    assert codes == ["B2026"]
    with output.connect() as conn:
        assert len(conn.execute(select(fact_plan_cost_center)).all()) == 12


def test_only_controllers(client: TestClient) -> None:
    login(client, OWNER)
    assert client.get(f"{P}/publish/preview").status_code == 403
    assert client.get(f"{P}/export.csv").status_code == 403


def test_published_signs_follow_the_convention(
    client: TestClient, migrated: Settings, output: Engine
) -> None:
    """ADR 0019: amount stays natural-positive; amount_signed follows the convention by role."""
    plan_cc1(client)
    p = preview(client, "all")
    assert run(client, p, "all").status_code == 200  # type: ignore[attr-defined]
    with output.connect() as conn:
        t = fact_plan_cost_center
        amount, sign = conn.execute(
            select(t.c.amount, t.c.amount_signed).where(t.c.account_code == "6000")
        ).first()  # type: ignore[misc]
        roles = dict(conn.execute(select(dim_account.c.code, dim_account.c.role)).all())
    assert (amount, sign) == (Decimal("100.00"), Decimal("100.00"))  # ledger: costs debit +
    assert (roles["6000"], roles["4900"], roles["A"]) == ("cost", "credit", None)

    # Only an admin sets the convention; it applies from the next run, which the preview shows.
    assert client.put("/api/settings/signs", json={"convention": "result"}).status_code == 403
    engine = create_engine_for(migrated)
    system = access.system_user(engine)
    access.create_user(
        engine, replace(system, is_admin=True), "adm@x.example", "Adm", is_admin=True
    )
    engine.dispose()
    login(client, "adm@x.example")
    settings = client.get("/api/settings/signs").json()
    assert (settings["convention"], settings["credit_accounts"]) == ("ledger", 1)
    assert [c["code"] for c in settings["credits"]] == ["4900"]
    response = client.put("/api/settings/signs", json={"convention": "result"})
    assert response.status_code == 200 and response.json()["convention"] == "result"
    login(client, CONTROLLER)
    p = preview(client, "all")
    assert (p["changed"], p["unchanged"]) == (12, 0)
    assert run(client, p, "all").status_code == 200  # type: ignore[attr-defined]
    with output.connect() as conn:
        sign = conn.execute(select(t.c.amount_signed).where(t.c.account_code == "6000")).scalar()
    assert sign == Decimal("-100.00")  # result: costs reduce the result
