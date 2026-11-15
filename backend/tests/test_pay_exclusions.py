"""A position's costs by part, and HR excluding a position from rules of the round (ADR 0025).

CC1 (see test_personnel): P001 (4000 a month), P002 (2000), P003 (3200, leaving end of June)
and P004 (a freelancer, 9000, typed). The rules here: a 13th salary on 6000 and an employer
contribution of 20 % on 6010.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, update
from sqlalchemy.exc import DBAPIError

from planlayer import access, rounds
from planlayer.store.schema import position_pay
from planlayer.store.uow import unit_of_work

from .conftest import APPROVER, CONTROLLER, NOBODY, OWNER, VIEWER, login
from .test_personnel import DEPUTY, HR, PAY, R, account, by_code, engine, start

__all__ = ["engine"]

RULES = [
    {"kind": "extra", "name": "13th salary", "account": "6000", "salaries": "1", "steps": []},
    {"kind": "contribution", "name": "Pension", "account": "6010", "percent": "20", "steps": []},
]


def parts(position: dict) -> dict[object, str]:  # type: ignore[type-arg]
    return {x["rule_id"] or x["source"]: x["year"] for x in position["parts"]}


def test_breakdown_and_exclusions(engine: Engine, client: TestClient) -> None:
    page = start(client, rules=RULES)
    extra, pension = (r["id"] for r in page["rules"])
    assert [r["name"] for r in page["rules"]] == ["13th salary", "Pension"]
    p001 = by_code(page)["P001"]
    assert p001["salary_account"] == "6000" and p001["excluded_rules"] == []
    # 48,000 pay, a 13th salary of 4,000, 20 % on the 52,000 gross.
    assert parts(p001) == {"pay": "48000.00", extra: "4000.00", pension: "10400.00"}
    assert p001["year_cost"] == "62400.00"
    # The freelancer's amount is all there is.
    assert parts(by_code(page)["P004"]) == {"pay": "108000.00"}

    url = f"{PAY}/CC1/positions/{p001['position_id']}"
    login(client, HR)
    # The preview recalculates without writing.
    preview = client.post(f"{url}/preview", json={"excluded_rules": [extra]}).json()
    assert parts(preview) == {"pay": "48000.00", pension: "9600.00"}
    assert by_code(client.get(f"{PAY}/CC1").json())["P001"]["excluded_rules"] == []
    page = client.put(url, json={"excluded_rules": [extra]}).json()
    assert by_code(page)["P001"]["excluded_rules"] == [extra]
    # The 13th salary on CC1: of P002 and P003 only.
    assert account(page, "6000")[0] == f"{9200 + 5200 / 12:.2f}"

    # Only HR excludes, in the API and in the store.
    login(client, OWNER)
    assert client.put(url, json={"excluded_rules": []}).status_code == 403
    assert client.post(f"{url}/preview", json={"excluded_rules": []}).status_code == 403
    # The owner's own changes keep the exclusion.
    page = client.put(url, json={"salary": "4100"}).json()
    assert by_code(page)["P001"]["excluded_rules"] == [extra]
    with engine.connect() as c:
        owner = access.user_by_login(c, OWNER)
    with pytest.raises(DBAPIError, match="only HR excludes"), unit_of_work(engine, owner) as conn:
        conn.execute(
            update(position_pay)
            .where(position_pay.c.position_id == p001["position_id"])
            .values(excluded_rules=[])
        )

    # Saving the rules keeps them, and the exclusion with them; a rule from elsewhere is refused.
    login(client, HR)
    rules_url = f"{PAY}/rules"
    stored = client.get(rules_url).json()["rules"]
    stored["rules"][0]["name"] = "Christmas bonus"
    saved = client.put(rules_url, json=stored).json()["rules"]["rules"]
    assert [r["id"] for r in saved] == [extra, pension]
    assert by_code(client.get(f"{PAY}/CC1").json())["P001"]["excluded_rules"] == [extra]
    stale = {**stored, "rules": [{**stored["rules"][0], "id": 999999}]}
    assert client.put(rules_url, json=stale).status_code == 422
    assert client.put(url, json={"excluded_rules": [999999]}).status_code == 422


def test_exclusions_carry_into_a_new_round(engine: Engine, client: TestClient) -> None:
    page = start(client, rules=RULES)
    extra = page["rules"][0]["id"]
    p001 = by_code(page)["P001"]
    login(client, HR)
    url = f"{PAY}/CC1/positions/{p001['position_id']}"
    assert client.put(url, json={"excluded_rules": [extra]}).status_code == 200
    system = access.system_user(engine)
    rounds.create_round(engine, system, "B2027", "Budget 2027", "budget", 2027, copy_from=R)
    rounds.set_round_status(engine, system, "B2027", "open")
    page = client.get("/api/versions/B2027/personnel/CC1").json()
    carried = page["rules"][0]
    assert carried["name"] == "13th salary" and carried["id"] != extra
    assert by_code(page)["P001"]["excluded_rules"] == [carried["id"]]
    assert carried["id"] not in parts(by_code(page)["P001"])


def test_preview_only_for_readers_of_the_pay(engine: Engine, client: TestClient) -> None:
    page = start(client, rules=RULES)
    p001 = by_code(page)["P001"]["position_id"]
    assert client.post(f"/api/versions/{R}/workforce/CC2/hr").status_code == 200
    login(client, HR)
    p009 = by_code(client.get(f"{PAY}/CC2").json())["P009"]["position_id"]
    url = f"{PAY}/CC1/positions/{p001}/preview"
    body = {"salary": "5000"}
    # Controllers, viewers, approvers, deputies and users without rights read no pay: the
    # cost center isn't found for them.
    for user in (CONTROLLER, VIEWER, APPROVER, DEPUTY, NOBODY):
        login(client, user)
        assert client.post(url, json=body).status_code == 404, user
    # The owner reads CC1's pay, but not another cost center's position through CC1.
    login(client, OWNER)
    assert client.post(url, json=body).status_code == 200
    assert client.post(f"{PAY}/CC1/positions/{p009}/preview", json=body).status_code == 404
    assert client.post(f"{PAY}/CC2/positions/{p001}/preview", json=body).status_code == 404
    # Before HR releases the round, only HR reads pay.
    login(client, HR)
    assert client.put(f"{PAY}/access/release", json={"released": False}).status_code == 200
    login(client, OWNER)
    assert client.post(url, json=body).status_code == 404
    # Without a session, nothing.
    client.post("/api/auth/logout", json={})
    assert client.post(url, json=body).status_code == 401
