"""Transfers of positions between cost centers (ADR 0021).

CC1's HR positions (see test_workforce): P001 Max (EMP/TECH, 100 %), P002 Eva (EMP/TECH, 50 %),
the planner (EMP/OFF, 80 %, leaving end of June) and a freelancer. CC2 has no owner; Bob is
made its deputy here.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update
from sqlalchemy.exc import DBAPIError

from planlayer import access
from planlayer.config import Settings
from planlayer.store.db import create_app_engine
from planlayer.store.schema import workforce_position, workforce_position_log
from planlayer.store.uow import unit_of_work

from .conftest import CONTROLLER, NOBODY, OWNER, VIEWER, login
from .test_workforce import lines, mode

R = "B2026"
CC1 = f"/api/versions/{R}/workforce/CC1"
CC2 = f"/api/versions/{R}/workforce/CC2"


@pytest.fixture
def engine(migrated: Settings, client: TestClient) -> Iterator[Engine]:
    e = create_app_engine(migrated)
    yield e
    e.dispose()


@pytest.fixture
def staffed(client: TestClient) -> TestClient:
    """A positions round, both cost centers staffed from HR, Bob deputy of CC2, as Anna."""
    assert mode(client, "positions") == 200
    client.post(f"{CC1}/hr")
    client.post(f"{CC2}/hr")
    assert client.post("/api/responsibilities/CC2/deputies", json={"login": NOBODY}).status_code
    login(client, OWNER)
    return client


def max_(client: TestClient) -> dict:  # type: ignore[type-arg]
    return next(p for p in client.get(CC1).json()["positions"] if p["code"] == "P001")


def propose(client: TestClient, **body: object) -> object:
    p = max_(client)
    return client.post(
        f"{CC1}/positions/{p['position_id']}/transfer",
        json={"to_cost_center": "CC2", "kind": "full", **body},
    )


def waiting(client: TestClient) -> dict:  # type: ignore[type-arg]
    return next(t for t in client.get("/api/transfers").json() if t["status"] == "proposed")


def test_targets_are_the_rounds_other_cost_centers(staffed: TestClient) -> None:
    targets = staffed.get(f"{CC1}/transfer-targets").json()
    assert [t["code"] for t in targets] == ["CC2"]
    assert targets[0]["editable"]


def test_full_transfer_waits_for_the_receiver(staffed: TestClient, engine: Engine) -> None:
    response = propose(staffed, note="Moves with the new line")
    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    sheet = response.json()  # type: ignore[attr-defined]
    p = next(p for p in sheet["positions"] if p["code"] == "P001")
    assert p["transfer"]["to_code"] == "CC2" and not p["transferable"]
    # Nothing has moved: the values stay with the sending cost center.
    assert lines(sheet, "headcount")["EMP/TECH"] == ["2.00"] * 12
    assert propose(staffed).status_code == 422  # type: ignore[attr-defined]  # one at a time
    t = waiting(staffed)
    assert t["can_cancel"] and not t["can_decide"]
    # The sender can't accept for the receiver.
    decision = f"/api/transfers/{t['transfer_id']}/decision"
    assert staffed.post(decision, json={"accept": True}).status_code == 403

    login(staffed, NOBODY)
    home = staffed.get("/api/home").json()
    assert home["tasks"] == 1
    assert home["transfers"][0]["person_name"] == p["person_name"]  # the receiver sees whom
    incoming = staffed.get(CC2).json()["incoming"]
    assert [i["position_code"] for i in incoming] == ["P001"]
    assert staffed.post(decision, json={"accept": True}).status_code == 200
    cc2 = staffed.get(CC2).json()
    moved = next(p for p in cc2["positions"] if p["code"] == "P001")
    assert moved["transferred_from"] == "CC1" and moved["origin"] == "hr"
    assert lines(cc2, "headcount")["EMP/TECH"] == ["2.00"] * 12  # Ida and Max
    assert staffed.get("/api/home").json()["tasks"] == 0

    login(staffed, OWNER)
    cc1 = staffed.get(CC1).json()
    assert "P001" not in {p["code"] for p in cc1["positions"]}
    assert lines(cc1, "headcount")["EMP/TECH"] == ["1.00"] * 12
    # Anna can no longer read Max; the transfer's record names the accepting user.
    with engine.connect() as conn:
        actors = set(conn.execute(select(workforce_position_log.c.actor)).scalars())
    assert NOBODY in actors
    decided = next(t for t in staffed.get("/api/transfers").json() if t["status"] == "accepted")
    assert decided["decided_by"] == "Bob"


def test_transfer_during_the_year(staffed: TestClient) -> None:
    assert propose(staffed, kind="during_year").status_code == 422  # type: ignore[attr-defined]
    for wrong in ("2026-09-15", "2026-01-01", "2027-03-01"):
        assert propose(staffed, kind="during_year", starts=wrong).status_code == 422  # type: ignore[attr-defined]
    assert propose(staffed, kind="during_year", starts="2026-09-01").status_code == 200  # type: ignore[attr-defined]
    login(staffed, NOBODY)
    t = waiting(staffed)
    assert t["kind"] == "during_year" and t["starts"] == "2026-09-01"
    accepted = staffed.post(f"/api/transfers/{t['transfer_id']}/decision", json={"accept": True})
    assert accepted.status_code == 200, accepted.text
    cc2 = staffed.get(CC2).json()
    arrived = next(p for p in cc2["positions"] if p["code"] == "P001")
    assert arrived["entry_date"] == "2026-09-01" and arrived["transferred_from"] == "CC1"
    assert lines(cc2, "headcount")["EMP/TECH"] == ["1.00"] * 8 + ["2.00"] * 4
    b = cc2["build_up"]
    assert b["moving_in"][8] == 1 and b["entering"][8] == 1 and b["new"][8] == 0

    login(staffed, OWNER)
    cc1 = staffed.get(CC1).json()
    ended = next(p for p in cc1["positions"] if p["code"] == "P001")
    assert ended["leave_date"] == "2026-08-31" and ended["transferred_to"] == "CC2"
    assert not ended["removable"] and not ended["transferable"]
    assert lines(cc1, "headcount")["EMP/TECH"] == ["2.00"] * 8 + ["1.00"] * 4
    assert cc1["build_up"]["moving_out"][8] == 1
    # The ended half stays as it was transferred.
    url = f"{CC1}/positions/{ended['position_id']}"
    assert staffed.delete(url).status_code == 422


def test_decline_and_withdraw(staffed: TestClient) -> None:
    propose(staffed)
    t = waiting(staffed)
    assert staffed.post(f"/api/transfers/{t['transfer_id']}/cancel").status_code == 200
    assert staffed.post(f"/api/transfers/{t['transfer_id']}/cancel").status_code == 422
    assert max_(staffed)["transferable"]
    propose(staffed)
    t = waiting(staffed)
    login(staffed, VIEWER)  # reads both, writes neither
    decision = f"/api/transfers/{t['transfer_id']}/decision"
    assert staffed.post(decision, json={"accept": False}).status_code == 403
    login(staffed, NOBODY)
    declined = staffed.post(decision, json={"accept": False, "reason": "No budget for it"})
    assert declined.status_code == 200
    login(staffed, OWNER)
    shown = next(x for x in staffed.get("/api/transfers").json() if x["status"] == "declined")
    assert shown["reason"] == "No budget for it"
    assert max_(staffed)["transfer"] is None


def test_controller_accepts_and_locks_apply(staffed: TestClient) -> None:
    propose(staffed)
    t = waiting(staffed)
    login(staffed, CONTROLLER)
    staffed.post(f"/api/versions/{R}/workflow", json={"action": "submit", "cost_centers": ["CC1"]})
    decision = f"/api/transfers/{t['transfer_id']}/decision"
    # The sending cost center is submitted: the position can't leave it now.
    assert staffed.post(decision, json={"accept": True}).status_code == 423
    staffed.post(
        f"/api/versions/{R}/workflow",
        json={"action": "reject", "cost_centers": ["CC1"], "comment": "Max moves first"},
    )
    assert staffed.post(decision, json={"accept": True}).status_code == 200


def test_numbers_unique_except_for_an_ended_half(staffed: TestClient, engine: Engine) -> None:
    propose(staffed, kind="during_year", starts="2026-07-01")
    login(staffed, NOBODY)
    staffed.post(
        f"/api/transfers/{waiting(staffed)['transfer_id']}/decision", json={"accept": True}
    )
    # Two rows share P001; reopening the ended one would make two active P001s.
    login(staffed, CONTROLLER)
    with unit_of_work(engine, access.system_user(engine)) as conn, pytest.raises(DBAPIError):
        conn.execute(
            update(workforce_position)
            .where(workforce_position.c.code == "P001")
            .values(transferred_to_id=None)
        )
