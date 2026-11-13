"""Cost forwarding (ADR 0018).

The slice has CC1 (owned by anna) and CC2 (no owner). Forwardings here pass CC1's costs on to
CC2, so a planner of CC1 sets off writes into a cost center she may not write.
"""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import DBAPIError

from planlayer import access
from planlayer.config import Settings
from planlayer.domain import forwarding as rules
from planlayer.store.db import create_app_engine
from planlayer.store.schema import change_log, fact_cost_center, member, version
from planlayer.store.uow import unit_of_work

from .conftest import BLANK, OWNER, login, plan, rows

R = "B2026"
PAGE = f"/api/versions/{R}/forwarding"


@pytest.fixture
def engine(migrated: Settings, client: TestClient) -> Iterator[Engine]:
    e = create_app_engine(migrated)
    yield e
    e.dispose()


# --- The rules ----------------------------------------------------------------------------------


def test_full_forwarding_by_portions_keeps_the_cents() -> None:
    f = rules.Forwarding("full", "S", {"A": Decimal(1), "B": Decimal(2)}, "portion")
    base: list[Decimal | None] = [Decimal("100.00")] + [None] * 11
    out = rules.charges(f, base)
    assert (out["A"][0], out["B"][0]) == (Decimal("33.33"), Decimal("66.67"))
    assert out["A"][1] == 0


def test_percentages_below_100_leave_the_rest_on_the_source() -> None:
    f = rules.Forwarding("full", "S", {"A": Decimal(30), "B": Decimal(50)}, "percent")
    out = rules.charges(f, [Decimal(1000)] * 12)
    assert (out["A"][0], out["B"][0]) == (Decimal("300.00"), Decimal("500.00"))
    with pytest.raises(rules.ForwardingError, match="more than 100"):
        rules.check(rules.Forwarding("full", "S", {"A": Decimal(101)}, "percent"))


def test_rate_and_fixed_forwardings_spread_the_year_in_twelfths() -> None:
    rate = rules.Forwarding("rate", "S", {"A": Decimal("2.5")}, rate=Decimal(100))
    assert sum(rules.charges(rate)["A"]) == Decimal(250)
    assert rules.charges(rate)["A"][0] == Decimal("20.84")
    fixed = rules.Forwarding(
        "fixed", "S", {"A": Decimal(1), "B": Decimal(1)}, "portion", Decimal(1200)
    )
    out = rules.charges(fixed)
    assert out["A"] == [Decimal(50)] * 12 and out["B"] == [Decimal(50)] * 12


def test_full_forwardings_run_after_what_charges_their_source() -> None:
    sources = {"bldg": "FAC", "tools": "TOOL", "it": "IT"}
    receivers = {"bldg": {"TOOL", "M1"}, "tools": {"M1", "M2"}, "it": {"FAC"}}
    sequence = rules.order(sources, {"tools"}, receivers)
    assert sequence.index("bldg") < sequence.index("tools")
    assert rules.downstream(sources, {"tools"}, receivers, "TOOL") == {"tools"}
    assert rules.downstream(sources, {"tools"}, receivers, "FAC") == set()
    with pytest.raises(rules.CycleError):
        rules.order({"a": "X", "b": "Y"}, {"a", "b"}, {"a": {"Y"}, "b": {"X"}})
    # A full forwarding passes on what is left: after the source's other forwardings.
    assert rules.order({"rest": "FAC", "m3": "FAC"}, {"rest"}, {"rest": {"A"}, "m3": {"B"}}) == [
        "m3",
        "rest",
    ]
    with pytest.raises(rules.CycleError):
        rules.order({"a": "X", "b": "X"}, {"a", "b"}, {"a": {"Y"}, "b": {"Z"}})


# --- The page and the sheets ----------------------------------------------------------------------


def forwarding(code: str = "TOOLS", **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": code,
        "name": "Tooling",
        "method": "full",
        "source_code": "CC1",
        "group_code": "B",
        "charge_account": {"code": "9000", "name": "Tooling charged"},
        "credit_account": {"code": "9005", "name": "Tooling passed on"},
        "receivers": [{"cost_center_code": "CC2", "quantity": "60"}],
        "shares": "percent",
    }
    return body | changes


def enter(client: TestClient, cc: str, account: str, month: int | None, value: str) -> Any:
    edit = {"account_code": account, "month": month, "value": value, "expected": BLANK}
    url = f"/api/versions/{R}/cost-centers/{cc}"
    response = client.post(f"{url}/edits", json={"edits": [edit]})
    assert response.status_code == 200, response.text
    return response.json()


def sheet(client: TestClient, cc: str) -> Any:
    return client.get(f"/api/versions/{R}/cost-centers/{cc}/grid").json()


def test_full_forwarding_charges_receivers_and_credits_the_source(client: TestClient) -> None:
    enter(client, "CC1", "6000", 0, "1000")
    enter(client, "CC1", "7000", 1, "500")
    response = client.post(PAGE, json=forwarding())
    assert response.status_code == 200, response.text
    page = response.json()
    (f,) = page["forwardings"]
    assert (f["passed_on"], f["receivers"][0]["year"], f["receivers"][0]["percent"]) == (
        "900.00",
        "900.00",
        "60.00",
    )
    assert page["sources"][0] == {
        "cost_center_code": "CC1",
        "cost_center_name": "Machining",
        "costs": "1500.00",
        "other_credits": None,
        "passed_on": "900.00",
        "remaining": "600.00",
    }
    assert page["proposed_codes"] == ["9010", "9015"]

    receiver = sheet(client, "CC2")
    assert plan(receiver, "9000")[:2] == ["600.00", "300.00"]
    charge = rows(receiver)["9000"]
    assert charge["forwarded"]["side"] == "charge" and charge["forwarded"]["source_code"] == "CC1"
    assert not charge["months_editable"] and not charge["total_editable"]
    source = sheet(client, "CC1")
    credit = rows(source)["9005"]
    assert (credit["credit"], plan(source, "9005")[:2]) == (True, ["-600.00", "-300.00"])
    assert rows(source)["B"]["plan"][1]["value"] == "200.00"  # 500 rent less 300 passed on


def test_a_planners_edit_on_the_source_moves_the_charges(
    engine: Engine, client: TestClient
) -> None:
    client.post(PAGE, json=forwarding())
    login(client, OWNER)
    grid = enter(client, "CC1", "6000", 2, "100")  # anna plans CC1 only
    assert plan(grid, "9005")[2] == "-60.00"
    with unit_of_work(engine, access.system_user(engine)) as conn:
        logged = conn.execute(
            select(change_log.c.actor, change_log.c.dim_cost_center)
            .where(change_log.c.batch_id == grid["batch_id"])
            .order_by(change_log.c.id)
        ).all()
        cc2 = conn.execute(select(member.c.member_id).where(member.c.code == "CC2")).scalar()
    assert ("anna", cc2) not in logged and ("system", cc2) in logged
    # She can't write a forwarding account, nor read the page.
    edit = {"account_code": "9005", "month": 3, "value": "1", "expected": BLANK}
    url = f"/api/versions/{R}/cost-centers/CC1/edits"
    assert client.post(url, json={"edits": [edit]}).status_code == 422
    assert client.get(PAGE).status_code == 403

    # Undo reverts her cell; the forwarding follows.
    undone = client.post(
        f"/api/versions/{R}/cost-centers/CC1/undo", json={"batch_id": grid["batch_id"]}
    )
    assert undone.status_code == 200, undone.text
    assert "9005" not in rows(undone.json())  # nothing to credit any more
    login(client, "ctrl@x.example")
    assert "9000" not in rows(sheet(client, "CC2"))


def test_forwarding_accounts_are_the_controllers(engine: Engine, client: TestClient) -> None:
    enter(client, "CC1", "6000", 0, "1000")
    client.post(PAGE, json=forwarding())
    with unit_of_work(engine, access.system_user(engine)) as conn:
        ids = dict(conn.execute(select(member.c.code, member.c.member_id)).all())
        vid = conn.execute(select(version.c.version_id).where(version.c.code == R)).scalar_one()
    with engine.connect() as conn:
        anna = access.user_by_login(conn, OWNER)
    with (
        pytest.raises(DBAPIError, match="written by cost forwarding"),
        unit_of_work(engine, anna) as conn,
    ):
        conn.execute(
            insert(fact_cost_center).values(
                version_id=vid,
                layer_id=1,
                measure_id=1,
                period=date(2026, 5, 1),
                dim_entity=ids["DE"],
                dim_cost_center=ids["CC1"],
                dim_account=ids["9005"],
                amount=Decimal(1),
            )
        )


def test_approved_receivers_take_new_charges_and_keep_their_status(client: TestClient) -> None:
    enter(client, "CC1", "6000", 0, "1000")
    enter(client, "CC2", "6000", 0, "1")
    wf = f"/api/versions/{R}/workflow"
    for action in ("submit", "approve"):
        response = client.post(wf, json={"action": action, "cost_centers": ["CC2"]})
        assert response.status_code == 200, response.text
    response = client.post(PAGE, json=forwarding())
    assert response.status_code == 200, response.text
    assert response.json()["forwardings"][0]["receivers"][0]["status"] == "approved"
    assert plan(sheet(client, "CC2"), "9000")[0] == "600.00"


def test_rate_forwarding_leaves_the_coverage_gap(client: TestClient) -> None:
    enter(client, "CC1", "7000", None, "15000")
    body = forwarding(
        "BLDG",
        method="rate",
        shares=None,
        rate="1200",
        unit="m³",
        receivers=[{"cost_center_code": "CC2", "quantity": "10"}],
    )
    page = client.post(PAGE, json=body).json()
    assert page["forwardings"][0]["rate"] == "1200"
    assert page["sources"][0]["remaining"] == "3000.00"
    assert plan(sheet(client, "CC2"), "9000") == ["1000.00"] * 12


def test_refusals(client: TestClient) -> None:
    assert (
        client.post(
            PAGE, json=forwarding(receivers=[{"cost_center_code": "CC1", "quantity": "1"}])
        ).status_code
        == 422
    )
    assert (
        client.post(
            PAGE, json=forwarding(receivers=[{"cost_center_code": "CC2", "quantity": "101"}])
        ).status_code
        == 422
    )
    two = forwarding(credit_account={"code": "9000", "name": "x"})
    assert client.post(PAGE, json=two).status_code == 422
    enter(client, "CC1", "6000", 0, "1")
    assert (
        client.post(PAGE, json=forwarding(charge_account={"code": "6000", "name": "x"})).status_code
        == 422
    )
    assert client.post(PAGE, json=forwarding()).status_code == 200
    back = forwarding(
        "BACK",
        source_code="CC2",
        receivers=[{"cost_center_code": "CC1", "quantity": "10"}],
        charge_account={"code": "9010", "name": "Back"},
        credit_account={"code": "9015", "name": "Back passed on"},
    )
    response = client.post(PAGE, json=back)
    assert response.status_code == 422 and "circle" in response.text


def test_change_and_delete(client: TestClient) -> None:
    enter(client, "CC1", "6000", 0, "1000")
    client.post(PAGE, json=forwarding())
    changed = forwarding(shares="portion", charge_account={"code": "9020", "name": "Moved"})
    response = client.put(f"{PAGE}/TOOLS", json=changed)
    assert response.status_code == 200, response.text
    grid = sheet(client, "CC2")
    assert "9000" not in rows(grid) and plan(grid, "9020")[0] == "1000.00"
    assert client.delete(f"{PAGE}/TOOLS").status_code == 200
    assert "9020" not in rows(sheet(client, "CC2"))
    assert "9005" not in rows(sheet(client, "CC1"))


def test_a_new_round_carries_and_recomputes_forwardings(client: TestClient) -> None:
    enter(client, "CC1", "6000", None, "1200")
    client.post(PAGE, json=forwarding())
    new = {"code": "B2027", "name": "Budget 2027", "kind": "budget", "fiscal_year": 2027,
           "copy_from": R, "carry": {"values": True}}  # fmt: skip
    assert client.post("/api/rounds", json=new).status_code == 200
    page = client.get("/api/versions/B2027/forwarding").json()
    assert page["forwardings"][0]["passed_on"] == "720.00"
    grid = client.get("/api/versions/B2027/cost-centers/CC2/grid").json()
    assert plan(grid, "9000") == ["60.00"] * 12


def test_full_forwarding_passes_on_what_the_rates_leave(client: TestClient) -> None:
    enter(client, "CC1", "7000", None, "15000")
    rate = forwarding(
        "BLDG",
        method="rate",
        shares=None,
        rate="1200",
        unit="m³",
        receivers=[{"cost_center_code": "CC2", "quantity": "10"}],
    )
    assert client.post(PAGE, json=rate).status_code == 200
    rest = forwarding(
        "REST",
        receivers=[{"cost_center_code": "CC2", "quantity": "100"}],
        charge_account={"code": "9010", "name": "Rest charged"},
        credit_account={"code": "9015", "name": "Rest passed on"},
    )
    page = client.post(PAGE, json=rest).json()
    assert {f["code"]: f["passed_on"] for f in page["forwardings"]} == {
        "BLDG": "12000.00",
        "REST": "3000.00",
    }
    assert page["sources"][0]["remaining"] == "0.00"
    grid = sheet(client, "CC1")
    assert rows(grid)["B"]["plan"][0]["value"] == "0.00"  # 1250 rent less 1000 and 250 passed on
