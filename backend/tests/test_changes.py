"""The round's change log for controllers, and row-level security on the change logs.

The ``client`` fixture syncs ``conftest.SOURCE`` (CC1 owned by anna, CC2 without an owner); B2026
is open and the controller is logged in.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select

from planlayer import access, changes
from planlayer.config import Settings
from planlayer.store.db import create_app_engine
from planlayer.store.schema import change_log
from planlayer.store.uow import unit_of_work

from .conftest import BLANK, CONTROLLER, OWNER, URL, limited_controller, login, versions_of

LOG = "/api/versions/B2026/changes"


@pytest.fixture
def engine(migrated: Settings, client: TestClient) -> Iterator[Engine]:
    e = create_app_engine(migrated)
    yield e
    e.dispose()


def year(account: str, value: str | None, expected: list[int | None] = BLANK) -> dict:  # type: ignore[type-arg]
    return {"account_code": account, "month": None, "value": value, "expected": expected}


def edit(client: TestClient, cc: str, *edits: dict) -> str:  # type: ignore[type-arg]
    response = client.post(
        f"/api/versions/B2026/cost-centers/{cc}/edits", json={"edits": list(edits)}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["batch_id"])


def test_one_entry_per_save(client: TestClient) -> None:
    login(client, OWNER)
    first = edit(client, "CC1", year("6000", "1200"))
    grid = client.get(f"{URL}/grid").json()
    second = edit(client, "CC1", year("6000", "2400", versions_of(grid, "6000")))
    login(client, CONTROLLER)
    edit(client, "CC2", year("6010", "120"))
    page = client.get(LOG).json()
    assert page["counts"] == {"all": 3, "entered": 3, "calculated": 0}
    assert [b["actor"]["login"] for b in page["batches"]] == [CONTROLLER, OWNER, OWNER]
    newest = page["batches"][1]
    assert newest["batch_id"] == second and newest["cells"] == 12
    assert newest["cost_centers"] == [{"code": "CC1", "name": newest["cost_centers"][0]["name"]}]
    assert (newest["sheets"], newest["sources"], newest["calculated"]) == (
        ["costs"],
        ["plan"],
        False,
    )
    assert {u["login"] for u in page["users"]} == {CONTROLLER, OWNER}
    assert [c["code"] for c in page["cost_centers"]] == ["CC1", "CC2"]
    # The lines: the year before and after, and the months changed.
    detail = client.get(f"{LOG}/{second}").json()
    [line] = detail["lines"]
    assert (line["code"], line["before"], line["after"]) == ("6000", "1200.00", "2400.00")
    assert len(line["months"]) == 12 and line["months"][0] == "2026-01-01"
    [line] = client.get(f"{LOG}/{first}").json()["lines"]
    assert (line["before"], line["after"]) == (None, "1200.00")  # blank before, not zero
    # Filters: by user, by cost center.
    mine = client.get(LOG, params={"actor": OWNER}).json()
    assert [b["batch_id"] for b in mine["batches"]] == [second, first]
    assert len(client.get(LOG, params={"cost_center": "CC2"}).json()["batches"]) == 1
    assert client.get(LOG, params={"kind": "calculated"}).json()["batches"] == []


def test_pages(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(changes, "PAGE", 2)
    grid = client.get(f"{URL}/grid").json()
    for value in ("1", "2", "3"):
        edit(client, "CC1", year("6000", value, versions_of(grid, "6000")))
        grid = client.get(f"{URL}/grid").json()
    page = client.get(LOG).json()
    assert len(page["batches"]) == 2 and page["cursor"]
    rest = client.get(LOG, params={"cursor": page["cursor"]}).json()
    assert len(rest["batches"]) == 1 and rest["cursor"] is None
    seen = [b["batch_id"] for b in page["batches"] + rest["batches"]]
    assert len(set(seen)) == 3


def test_controllers_only(client: TestClient) -> None:
    login(client, OWNER)
    assert client.get(LOG).status_code == 403


def test_the_change_log_follows_the_readers_cost_centers(
    client: TestClient, engine: Engine
) -> None:
    edit(client, "CC1", year("6000", "1200"))
    # Without a user, row-level security shows no change, as it shows no plan value.
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(change_log)).scalar() == 0
    with unit_of_work(engine, access.system_user(engine)) as conn:
        assert conn.execute(select(func.count()).select_from(change_log)).scalar() == 12
    # A controller whose rights reach no cost center sees none of its changes.
    assert changes.history(engine, limited_controller(engine), "B2026").batches == []


def test_undo_reads_its_batch_whatever_the_log_shows(client: TestClient) -> None:
    login(client, OWNER)
    batch = edit(client, "CC1", year("6000", "1200"))
    undone = client.post(f"{URL}/undo", json={"batch_id": batch})
    assert undone.status_code == 200, undone.text
    login(client, CONTROLLER)
    page = client.get(LOG).json()
    assert page["counts"]["all"] == 2
    [line] = client.get(f"{LOG}/{page['batches'][0]['batch_id']}").json()["lines"]
    assert (line["before"], line["after"]) == ("1200.00", None)
    assert page["batches"][0]["cleared"] == 12
