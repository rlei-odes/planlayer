"""Planning rounds, entry forms, the lock trigger and the workflow (ADR 0007, ADR 0008).

The ``client`` fixture syncs ``conftest.SOURCE`` and opens round B2026; ``dev`` is the actor.
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, insert, select
from sqlalchemy.exc import DBAPIError

from planlayer import access, rounds, workflow
from planlayer.config import Settings
from planlayer.domain.spreading import Profile
from planlayer.store.db import create_app_engine
from planlayer.store.schema import (
    fact_cost_center,
    line_spread,
    member,
    planning_round,
    round_member,
    version,
    workflow_event,
)
from planlayer.store.uow import unit_of_work

from .conftest import (
    APPROVER,
    BLANK,
    CONTROLLER,
    OWNER,
    TEST_DATABASE_URL,
    URL,
    limited_controller,
    plan,
    rows,
    run_sync,
    versions_of,
)
from .conftest import login as log_in

R = "B2026"


@pytest.fixture
def engine(migrated: Settings, client: TestClient) -> Iterator[Engine]:
    e = create_app_engine(migrated)
    yield e
    e.dispose()


def month(code: str, m: int, value: str | None, expected: list[int | None] = BLANK) -> dict:  # type: ignore[type-arg]
    return {"account_code": code, "month": m, "value": value, "expected": expected}


def year(code: str, value: str | None) -> dict:  # type: ignore[type-arg]
    return {"account_code": code, "month": None, "value": value, "expected": BLANK}


def post(client: TestClient, *edits: dict, cc: str = "CC1") -> object:  # type: ignore[type-arg]
    return client.post(f"/api/versions/{R}/cost-centers/{cc}/edits", json={"edits": list(edits)})


def system(engine: Engine) -> access.User:
    return access.system_user(engine)


def as_user(engine: Engine, login: str) -> access.User:
    with engine.connect() as conn:
        return access.user_by_login(conn, login)


def raw_insert(engine: Engine, account: str = "6000", cc: str = "CC1") -> None:
    """A plan value written past the API, as a bug or a script would (as the controller)."""
    with unit_of_work(engine, system(engine)) as conn:
        ids = dict(conn.execute(select(member.c.code, member.c.member_id)).all())
        vid = conn.execute(select(version.c.version_id).where(version.c.code == R)).scalar_one()
        conn.execute(
            insert(fact_cost_center).values(
                version_id=vid,
                layer_id=1,
                measure_id=1,
                period=date(2026, 5, 1),
                dim_entity=ids["DE"],
                dim_cost_center=ids[cc],
                dim_account=ids[account],
                amount=Decimal(1),
            )
        )


def status(client: TestClient, cc: str = "CC1") -> str:
    units = client.get(f"/api/versions/{R}/cost-centers").json()
    return str(next(u["status"] for u in units if u["code"] == cc))


def act(client: TestClient, action: str, comment: str | None = None, **target: object) -> object:
    target.setdefault("cost_centers", ["CC1"])
    return client.post(
        f"/api/versions/{R}/workflow", json={"action": action, "comment": comment, **target}
    )


# --- Rounds and the lock trigger ------------------------------------------------------------------


def test_a_round_in_setup_is_prepared_by_controllers(engine: Engine, client: TestClient) -> None:
    """Controllers write into a round in setup without moving its workflow status; planners
    can't write until it opens (ADR 0014)."""
    rounds.create_round(engine, system(engine), "F2026", "Forecast 2026", "forecast", 2026)
    url = "/api/versions/F2026/cost-centers/CC1"
    grid = client.get(f"{url}/grid").json()
    assert grid["round_status"] == "setup" and grid["lock_reason"] is None
    assert client.post(f"{url}/edits", json={"edits": [month("6000", 0, "1")]}).status_code == 200
    assert client.get(f"{url}/grid").json()["status"] == "not_started"
    log_in(client, OWNER)
    grid = client.get(f"{url}/grid").json()
    assert grid["lock_reason"].startswith("the round is still being set up")
    assert not rows(grid)["6000"]["total_editable"]
    edit = month("6000", 1, "1")
    assert client.post(f"{url}/edits", json={"edits": [edit]}).status_code == 423


def test_lock_trigger_refuses_writes_past_the_api(engine: Engine, client: TestClient) -> None:
    raw_insert(engine)  # open round, cost center not started: allowed
    rounds.set_round_status(engine, system(engine), R, "closed", unapproved_ok=True)
    with pytest.raises(DBAPIError, match="the planning round is closed"):
        raw_insert(engine, account="6010")
    rounds.set_round_status(engine, system(engine), R, "open")
    act(client, "submit")
    with pytest.raises(DBAPIError, match="the cost center is submitted"):
        raw_insert(engine, account="6010")
    rounds.set_member_enabled(engine, system(engine), R, "CC2", False)
    with pytest.raises(DBAPIError, match="the cost center is not enabled"):
        raw_insert(engine, cc="CC2")


def test_round_status_transitions(engine: Engine, client: TestClient) -> None:
    closing = {"status": "closed", "unapproved_ok": True}
    assert client.post(f"/api/versions/{R}/status", json=closing).json()["status"] == "closed"
    assert client.post(f"/api/versions/{R}/status", json={"status": "setup"}).status_code == 422
    assert post(client, month("6000", 0, "1")).status_code == 423  # type: ignore[attr-defined]
    assert client.post(f"/api/versions/{R}/status", json={"status": "open"}).status_code == 200


def test_actuals_sync_is_not_locked(engine: Engine, migrated: Settings, source_schema: str) -> None:
    rounds.set_round_status(engine, system(engine), R, "closed", unapproved_ok=True)
    assert run_sync(migrated, source_schema).actual_rows == 16


# --- Workflow -------------------------------------------------------------------------------------


def test_first_save_starts_the_cost_center(engine: Engine, client: TestClient) -> None:
    assert status(client) == "not_started"
    post(client, month("6000", 0, "5"))
    assert status(client) == "in_progress"
    with engine.connect() as conn:
        events = conn.execute(
            select(workflow_event.c.actor, workflow_event.c.from_status, workflow_event.c.to_status)
        ).all()
    assert [tuple(e) for e in events] == [(CONTROLLER, "not_started", "in_progress")]


def test_submit_reject_approve_reopen(client: TestClient) -> None:
    post(client, month("6000", 0, "5"))
    assert act(client, "submit").json() == ["CC1"]  # type: ignore[attr-defined]
    grid = client.get(f"{URL}/grid").json()
    assert grid["status"] == "submitted" and grid["lock_reason"] == "the cost center is submitted"
    assert post(client, month("6000", 1, "5")).status_code == 423  # type: ignore[attr-defined]

    assert act(client, "reject").status_code == 422  # type: ignore[attr-defined]
    assert act(client, "reject", "too high").json() == ["CC1"]  # type: ignore[attr-defined]
    assert status(client) == "in_progress"
    act(client, "submit")
    act(client, "approve")
    assert status(client) == "approved"
    assert act(client, "submit").status_code == 422  # type: ignore[attr-defined]
    assert act(client, "reopen", "restated").json() == ["CC1"]  # type: ignore[attr-defined]
    assert status(client) == "in_progress"


def test_empty_cost_center_can_be_submitted(client: TestClient) -> None:
    assert act(client, "submit", cost_centers=["CC2"]).json() == ["CC2"]  # type: ignore[attr-defined]


def test_bulk_approve_below_a_node_and_no_self_approval(engine: Engine, client: TestClient) -> None:
    act(client, "submit", cost_centers=["CC1", "CC2"])
    # CC1's owner can't approve it.
    with pytest.raises(access.AccessError, match="CC1: you can't approve a cost center you plan"):
        workflow.act(engine, as_user(engine, "anna"), R, "approve", cost_centers=["CC1"])
    assert act(client, "approve", node="DEP", cost_centers=None).json() == ["CC1", "CC2"]  # type: ignore[attr-defined]
    assert act(client, "approve", node="DEP", cost_centers=None).status_code == 422  # type: ignore[attr-defined]


def test_overview_rolls_up_and_finds_approvers(engine: Engine, client: TestClient) -> None:
    admin = replace(system(engine), is_admin=True)
    for login in ("cfo", "head"):
        access.create_user(engine, admin, login, login)
    access.add_approver(engine, system(engine), "NW", "cfo")
    access.add_approver(engine, system(engine), "DEP", "head")
    rounds.set_deadline(engine, system(engine), R, date(2026, 1, 31))
    post(client, month("6000", 0, "5"))
    act(client, "submit", cost_centers=["CC2"])
    o = workflow.overview(engine, system(engine), R, today=date(2026, 2, 1))
    assert [(r.kind, r.code, r.depth) for r in o.rows] == [
        ("node", "NW", 0),
        ("node", "DEP", 1),
        ("cost_center", "CC1", 2),
        ("cost_center", "CC2", 2),
    ]
    nw, _, cc1, cc2 = o.rows
    assert nw.counts == {"not_started": 0, "in_progress": 1, "submitted": 1, "approved": 0}
    assert cc1.approvers == [APPROVER, "head"]  # the nearest node above wins
    assert (cc1.status, cc1.overdue, cc2.overdue) == ("in_progress", True, False)
    assert o.not_planned == []


def test_new_and_disabled_cost_centers_are_not_planned(engine: Engine, client: TestClient) -> None:
    with engine.begin() as conn:
        cc2 = conn.execute(select(member.c.member_id).where(member.c.code == "CC2")).scalar_one()
        # As if CC2 had first arrived with a sync after the round was created.
        conn.execute(round_member.delete().where(round_member.c.member_id == cc2))
    assert [u["code"] for u in client.get(f"/api/versions/{R}/cost-centers").json()] == ["CC1"]
    o = workflow.overview(engine, system(engine), R, today=date(2026, 1, 1))
    assert [(n.code, n.reason) for n in o.not_planned] == [("CC2", "new")]
    assert post(client, month("6000", 0, "1"), cc="CC2").status_code == 423  # type: ignore[attr-defined]
    enable = f"/api/versions/{R}/cost-centers/CC2/enabled"
    client.post(enable, json={"enabled": True})
    assert post(client, month("6000", 0, "1"), cc="CC2").status_code == 200  # type: ignore[attr-defined]
    client.post(f"/api/versions/{R}/cost-centers/CC1/enabled", json={"enabled": False})
    o = workflow.overview(engine, system(engine), R, today=date(2026, 1, 1))
    assert [(n.code, n.reason) for n in o.not_planned] == [("CC1", "disabled")]


# --- Sheets: comparisons, added and hidden accounts -----------------------------------------------


def test_comparisons_in_order(engine: Engine, client: TestClient) -> None:
    rounds.create_round(engine, system(engine), "B2025", "Budget 2025", "budget", 2025)
    rounds.set_comparisons(
        engine,
        system(engine),
        R,
        [
            rounds.Comparison("version", "B2025"),
            rounds.Comparison("prior_year_actuals", note="  "),
            rounds.Comparison("actuals", note="January to June only"),
        ],
    )
    grid = client.get(f"{URL}/grid").json()
    assert [c["label"] for c in grid["comparisons"]] == [
        "Budget 2025",
        "Actual 2025",
        "Actual 2026",
    ]
    # Notes reach the planner; actuals columns say how far their actuals go.
    assert [(c["note"], c["actuals_until"], c["partial"]) for c in grid["comparisons"]] == [
        (None, None, False),
        (None, "2025-12-01", False),
        ("January to June only", None, False),
    ]
    assert rows(grid)["6000"]["comparisons"][1] == ["100.00"] * 12
    assert rows(grid)["6000"]["comparisons"][0] == BLANK


def test_added_account_appears_and_not_planned_ones_cannot_be_added(
    engine: Engine, client: TestClient
) -> None:
    cc2 = "/api/versions/B2026/cost-centers/CC2"
    assert [r["code"] for r in client.get(f"{cc2}/grid").json()["rows"]] == ["A", "6000"]
    grid = client.post(f"{cc2}/accounts", json={"account_code": "7000"}).json()
    assert [r["code"] for r in grid["rows"]] == ["A", "6000", "B", "7000"]
    assert plan(grid, "7000") == BLANK
    rounds.set_account_planned(engine, system(engine), R, "6010", False)
    assert client.post(f"{cc2}/accounts", json={"account_code": "6010"}).status_code == 422
    codes = [a["code"] for a in client.get(f"{cc2}/accounts").json()]
    assert codes == ["4900", "6000", "7000"]


def test_account_not_planned(engine: Engine, client: TestClient) -> None:
    post(client, month("6010", 0, "5"))
    with pytest.raises(rounds.RoundError, match="still has plan values on CC1"):
        rounds.set_account_planned(engine, system(engine), R, "6010", False)
    post(client, month("6010", 0, None, versions_of(client.get(f"{URL}/grid").json(), "6010")))
    rounds.set_account_planned(engine, system(engine), R, "6010", False)
    grid = client.get(f"{URL}/grid").json()
    # Shown read only for its prior-year actuals, and counted in the group's total.
    row = rows(grid)["6010"]
    assert row["not_planned"] == "round" and not row["total_editable"]
    assert row["comparisons"][0][0] == "50.00"
    assert rows(grid)["A"]["comparisons"][0][0] == "150.00"
    assert post(client, month("6010", 0, "5")).status_code == 422  # type: ignore[attr-defined]
    with pytest.raises(DBAPIError, match="the account is not planned"):
        raw_insert(engine, account="6010")
    rounds.set_account_planned(engine, system(engine), R, "6010", True)
    assert rows(client.get(f"{URL}/grid").json())["6010"]["not_planned"] is None


# --- Entry forms ----------------------------------------------------------------------------------


def test_group_form_inherited_account_form_wins(engine: Engine, client: TestClient) -> None:
    rounds.set_entry_form(engine, system(engine), R, "A", "annual_even")
    rounds.set_entry_form(engine, system(engine), R, "6010", "monthly")
    r = rows(client.get(f"{URL}/grid").json())
    assert (r["6000"]["entry_form"], r["6000"]["months_editable"]) == ("annual_even", False)
    assert (r["6010"]["entry_form"], r["6010"]["months_editable"]) == ("monthly", True)
    assert r["7000"]["entry_form"] == "monthly"
    assert post(client, month("6000", 0, "1")).status_code == 422  # type: ignore[attr-defined]
    grid = post(client, year("6000", "100.00")).json()  # type: ignore[attr-defined]
    assert plan(grid, "6000") == ["8.34"] * 4 + ["8.33"] * 8


def test_annual_hides_months(engine: Engine, client: TestClient) -> None:
    rounds.set_entry_form(engine, system(engine), R, None, "annual")
    row = rows(client.get(f"{URL}/grid").json())["7000"]
    assert (row["months_visible"], row["months_editable"], row["total_editable"]) == (
        False,
        False,
        True,
    )


def test_annual_profile(engine: Engine, client: TestClient) -> None:
    rounds.add_profile(
        engine, system(engine), "SHUT", "August shutdown", Profile.relative([1] * 7 + [0] + [1] * 4)
    )
    rounds.set_entry_form(engine, system(engine), R, "6000", "annual_profile", profile_code="SHUT")
    grid = post(client, year("6000", "1100")).json()  # type: ignore[attr-defined]
    assert plan(grid, "6000") == ["100.00"] * 7 + ["0.00"] + ["100.00"] * 4
    assert rows(grid)["6000"]["basis"] == "profile August shutdown"
    with pytest.raises(rounds.RoundError):
        rounds.set_entry_form(engine, system(engine), R, "6000", "annual_profile")


def test_reference_version_and_fallback(engine: Engine, client: TestClient) -> None:
    rounds.create_round(engine, system(engine), "B2025", "Budget 2025", "budget", 2025)
    rounds.set_entry_form(
        engine, system(engine), R, "7000", "annual_reference", reference_version_code="B2025"
    )
    grid = client.get(f"{URL}/grid").json()
    # Budget 2025 has no values for 7000: the line spreads evenly, and says so.
    assert (rows(grid)["7000"]["basis"], rows(grid)["7000"]["fallback"]) == ("Budget 2025", True)
    assert (rows(grid)["7000"]["reference_version"], rows(grid)["6000"]["reference_version"]) == (
        "B2025",
        None,
    )
    assert rows(grid)["6000"]["fallback"] is False
    grid = post(client, year("7000", "12")).json()  # type: ignore[attr-defined]
    assert plan(grid, "7000") == ["1.00"] * 12


def test_copy_setup_from_round(engine: Engine, client: TestClient) -> None:
    rounds.set_entry_form(engine, system(engine), R, "A", "annual_even")
    rounds.set_account_planned(engine, system(engine), R, "7000", False)
    rounds.set_member_enabled(engine, system(engine), R, "CC2", False)
    rounds.create_round(engine, system(engine), "F2026", "Forecast", "forecast", 2026, copy_from=R)
    rounds.set_round_status(engine, system(engine), "F2026", "open")
    grid = client.get("/api/versions/F2026/cost-centers/CC1/grid").json()
    assert rows(grid)["6000"]["entry_form"] == "annual_even"
    assert rows(grid)["7000"]["not_planned"] == "round"  # carried over, shown for its actuals
    units = client.get("/api/versions/F2026/cost-centers").json()
    assert [u["code"] for u in units] == ["CC1"]
    with engine.connect() as conn:
        copied = conn.execute(select(planning_round.c.copied_from)).scalars().all()
    assert sorted(c for c in copied if c is not None) == [2]


# --- A line's own spread ------------------------------------------------------------------------


def spread(client: TestClient, account: str, body: dict | None, cc: str = "CC1") -> object:  # type: ignore[type-arg]
    return client.put(
        f"/api/versions/{R}/cost-centers/{cc}/lines/{account}/spread", json={"spread": body}
    )


def test_line_spread(engine: Engine, client: TestClient) -> None:
    rounds.add_profile(
        engine, system(engine), "SHUT", "August shutdown", Profile.relative([1] * 7 + [0] + [1] * 4)
    )
    log_in(client, OWNER)
    response = spread(client, "6000", {"basis": "profile", "profile_code": "SHUT"})
    row = rows(response.json())["6000"]  # type: ignore[attr-defined]
    assert row["chosen_spread"] == {"basis": "profile", "profile_code": "SHUT"}
    assert (row["spread"], row["basis"], row["profile"]) == (
        "profile",
        "profile August shutdown",
        "SHUT",
    )
    grid = post(client, year("6000", "1100")).json()  # type: ignore[attr-defined]
    assert plan(grid, "6000") == ["100.00"] * 7 + ["0.00"] + ["100.00"] * 4

    # Only this cost center's line; back to the entry form's basis.
    log_in(client, CONTROLLER)
    other = rows(client.get(f"/api/versions/{R}/cost-centers/CC2/grid").json())["6000"]
    assert (other["spread"], other["chosen_spread"]) == ("reference", None)
    row = rows(spread(client, "6000", None).json())["6000"]  # type: ignore[attr-defined]
    assert (row["spread"], row["chosen_spread"]) == ("reference", None)
    assert plan(client.get(f"{URL}/grid").json(), "6000")[7] == "0.00"  # stored months stay

    assert spread(client, "6000", {"basis": "even", "profile_code": "SHUT"}).status_code == 422  # type: ignore[attr-defined]
    assert spread(client, "6000", {"basis": "profile", "profile_code": "NO"}).status_code == 404  # type: ignore[attr-defined]
    rounds.set_entry_form(engine, system(engine), R, "7000", "annual")
    assert spread(client, "7000", {"basis": "even"}).status_code == 422  # type: ignore[attr-defined]

    spread(client, "6010", {"basis": "profile", "profile_code": "SHUT"})
    refused = client.delete("/api/profiles/SHUT")
    assert refused.status_code == 422 and R in refused.json()["detail"]

    act(client, "submit")
    assert spread(client, "6010", None).status_code == 423  # type: ignore[attr-defined]


def test_line_spread_is_enforced_in_the_database(engine: Engine, client: TestClient) -> None:
    def write(user: access.User, cc: str) -> None:
        with unit_of_work(engine, user) as conn:
            ids = dict(conn.execute(select(member.c.code, member.c.member_id)).all())
            vid = conn.execute(select(version.c.version_id).where(version.c.code == R)).scalar_one()
            conn.execute(
                insert(line_spread).values(
                    version_id=vid,
                    module="cost_center",
                    cost_center_id=ids[cc],
                    account_id=ids["6000"],
                    basis="even",
                )
            )

    with pytest.raises(DBAPIError, match="row-level security"):
        write(as_user(engine, OWNER), "CC2")
    act(client, "submit")
    with pytest.raises(DBAPIError, match="the cost center is submitted"):
        write(system(engine), "CC1")
    write(system(engine), "CC2")


# --- Round-wide work sees every cost center -------------------------------------------------------


def test_round_wide_work_sees_every_cost_center(engine: Engine, client: TestClient) -> None:
    post(client, month("6010", 0, "5"))
    limited = limited_controller(engine)
    # Values on CC1, which the limited controller's own rights don't show, still count.
    with pytest.raises(rounds.RoundError, match="still has plan values on CC1"):
        rounds.set_account_planned(engine, limited, R, "6010", False)
    # A new round takes every cost center's values from the one it is set up like.
    rounds.create_round(
        engine, limited, "B2027", "Budget 2027", "budget", 2027, copy_from=R,
        carry=rounds.CarryOver(values=True),
    )  # fmt: skip
    assert TEST_DATABASE_URL
    schemas = engine.get_execution_options()["schema_translate_map"]
    admin = create_engine(TEST_DATABASE_URL).execution_options(schema_translate_map=schemas)
    with admin.connect() as conn:  # the superuser, past row-level security
        copied = conn.execute(
            select(func.count())
            .select_from(fact_cost_center)
            .join(version, version.c.version_id == fact_cost_center.c.version_id)
            .where(version.c.code == "B2027")
        ).scalar_one()
    admin.dispose()
    assert copied > 0


def test_a_planner_recalls_a_submission(client: TestClient) -> None:
    log_in(client, OWNER)
    post(client, month("6000", 0, "5"))
    act(client, "submit")
    assert client.get(f"{URL}/grid").json()["can_recall"] is True
    log_in(client, CONTROLLER)
    assert client.get(f"{URL}/grid").json()["can_recall"] is False  # a controller rejects
    assert act(client, "recall").status_code == 403  # type: ignore[attr-defined]
    log_in(client, OWNER)
    assert act(client, "recall", "oops").json() == ["CC1"]  # type: ignore[attr-defined]
    grid = client.get(f"{URL}/grid").json()
    assert (grid["status"], grid["lock_reason"], grid["can_recall"]) == ("in_progress", None, False)
    assert grid["returned"] is None  # a recall sends nothing back: no comment kept
    assert act(client, "recall").status_code == 422  # type: ignore[attr-defined]


def test_a_rejection_shows_until_the_next_submit(client: TestClient) -> None:
    post(client, month("6000", 0, "5"))
    act(client, "submit")
    act(client, "reject", "too high")
    log_in(client, OWNER)
    returned = client.get(f"{URL}/grid").json()["returned"]
    assert (returned["how"], returned["comment"]) == ("rejected", "too high")
    [work] = client.get("/api/home").json()["rounds"]
    assert work["cost_centers"][0]["returned"]["comment"] == "too high"
    post(client, month("6000", 1, "6"))  # editing keeps it in view
    assert client.get(f"{URL}/grid").json()["returned"] is not None
    act(client, "submit")
    assert client.get(f"{URL}/grid").json()["returned"] is None
    log_in(client, CONTROLLER)
    act(client, "approve")
    act(client, "reopen", "restated")
    assert client.get(f"{URL}/grid").json()["returned"]["how"] == "reopened"


def test_closing_with_cost_centers_not_approved_asks_first(client: TestClient) -> None:
    refused = client.post(f"/api/versions/{R}/status", json={"status": "closed"})
    assert refused.status_code == 409 and "CC1, CC2" in refused.text
    act(client, "submit", cost_centers=["CC1", "CC2"])
    act(client, "approve", cost_centers=["CC1", "CC2"])
    closed = client.post(f"/api/versions/{R}/status", json={"status": "closed"})
    assert closed.status_code == 200, closed.text


def test_back_to_setup_before_anyone_starts(engine: Engine, client: TestClient) -> None:
    setup = {"status": "setup"}
    assert client.post(f"/api/versions/{R}/status", json=setup).json()["status"] == "setup"
    assert client.post(f"/api/versions/{R}/status", json={"status": "open"}).status_code == 200
    log_in(client, OWNER)
    post(client, month("6000", 0, "5"))
    log_in(client, CONTROLLER)
    refused = client.post(f"/api/versions/{R}/status", json=setup)
    assert refused.status_code == 422 and "CC1" in refused.text
    # Round-wide: a controller whose own rights reach no cost center is refused all the same.
    with pytest.raises(rounds.RoundError, match="already being planned"):
        rounds.set_round_status(engine, limited_controller(engine), R, "setup")
