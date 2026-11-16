"""The setup page's API (E-03): rounds, a round's whole setup, seasonality profiles."""

from fastapi.testclient import TestClient

from .conftest import BLANK, OWNER, URL, login, rows

S = "/api/rounds/B2026/setup"


def setup(client: TestClient) -> dict:  # type: ignore[type-arg]
    return client.get(S).json()  # type: ignore[no-any-return]


def test_read_defaults(client: TestClient) -> None:
    v = setup(client)
    assert v["round"]["code"] == "B2026" and v["copied_from"] is None
    assert v["setup"] == {
        "default_form": "monthly",
        "entry_forms": [],
        "comparisons": [{"kind": "prior_year_actuals", "version_code": None, "note": None}],
        "not_planned_accounts": [],
        "deadline": None,
        "capacity_work_centers": [],
        "capacity_workforce": [],
        "fte_hours": None,
        "planned_cost_centers": ["CC1", "CC2"],
        "account_sets": [],
        "grain": "month",
        "actuals_through": None,
        "workforce_mode": None,
        "personnel_costs": False,
        "capex": None,
    }
    assert v["actuals_available"] is True and v["enabled_cost_centers"] == 2
    assert v["sample_cost_center"] == "Machining"


def test_save_whole_setup(client: TestClient) -> None:
    profile = {
        "code": "Q4",
        "name": "Q4 peak",
        "entered_as": "relative",
        "values": [1] * 9 + [3] * 3,
    }
    assert client.post("/api/profiles", json=profile).status_code == 200
    body = {
        "default_form": "annual",
        "entry_forms": [
            {"account_code": "A", "form": "annual_profile", "profile_code": "Q4"},
            {"account_code": "6010", "form": "monthly"},
        ],
        "comparisons": [{"kind": "actuals"}, {"kind": "prior_year_actuals"}],
        "not_planned_accounts": ["7000"],
        "deadline": "2026-12-15",
    }
    assert client.put(S, json=body).status_code == 200
    saved = setup(client)["setup"]
    assert saved["entry_forms"] == [
        {
            "account_code": "6010",
            "form": "monthly",
            "profile_code": None,
            "reference_version_code": None,
        },
        {
            "account_code": "A",
            "form": "annual_profile",
            "profile_code": "Q4",
            "reference_version_code": None,
        },
    ]
    assert saved["not_planned_accounts"] == ["7000"] and saved["deadline"] == "2026-12-15"
    grid = client.get(f"{URL}/grid").json()
    r = rows(grid)
    assert (r["6000"]["entry_form"], r["6010"]["entry_form"]) == ("annual_profile", "monthly")
    # Not planned, but shown read only for its prior-year actuals (ADR 0014).
    assert r["7000"]["not_planned"] == "round"
    assert grid["comparisons"][0]["label"] == "Actual 2026"


def test_invalid_setup_changes_nothing(client: TestClient) -> None:
    edit = {"account_code": "7000", "month": 0, "value": "1", "expected": BLANK}
    client.post(f"{URL}/edits", json={"edits": [edit]})
    before = setup(client)["setup"]
    bad = {**before, "default_form": "annual", "not_planned_accounts": ["7000"]}
    response = client.put(S, json=bad)
    assert (
        response.status_code == 422 and "still has plan values on CC1" in response.json()["detail"]
    )
    no_profile = {**before, "entry_forms": [{"account_code": "A", "form": "annual_profile"}]}
    assert client.put(S, json=no_profile).status_code == 422
    assert setup(client)["setup"] == before


def test_create_round_and_copy(client: TestClient) -> None:
    client.put(S, json={**setup(client)["setup"], "default_form": "annual_even"})
    new = {"code": "F2026", "name": "Forecast 2026", "kind": "forecast", "fiscal_year": 2026,
           "copy_from": "B2026"}  # fmt: skip
    created = client.post("/api/rounds", json=new).json()
    assert (created["status"], created["fiscal_year"]) == ("setup", 2026)
    copied = client.get("/api/rounds/F2026/setup").json()
    assert copied["copied_from"] == "B2026" and copied["setup"]["default_form"] == "annual_even"
    assert client.post("/api/rounds", json=new).status_code == 422  # exists


def test_profiles_are_validated(client: TestClient) -> None:
    bad = {"code": "P", "name": "P", "entered_as": "percent", "values": ["8.325"] * 12}
    response = client.post("/api/profiles", json=bad)
    assert response.status_code == 422 and "not 100" in response.json()["detail"]
    good = {**bad, "values": ["10"] * 10 + ["0", "0"]}
    assert client.post("/api/profiles", json=good).status_code == 200
    assert client.post("/api/profiles", json=good).status_code == 422  # exists
    assert client.get("/api/profiles").json()[0]["values"][-1] == "0"


def test_edit_and_delete_profiles(client: TestClient) -> None:
    p = {"code": "WD", "name": "Working days", "entered_as": "relative", "values": ["1"] * 12}
    client.post("/api/profiles", json=p)
    edited = {**p, "name": "Working days 2026", "values": ["20"] * 11 + ["15.5"]}
    assert client.put("/api/profiles/WD", json=edited).status_code == 200
    assert client.get("/api/profiles").json() == [edited]
    assert client.put("/api/profiles/WD", json={**edited, "values": ["-1"] * 12}).status_code == 422
    assert client.put("/api/profiles/WD", json={**edited, "code": "X"}).status_code == 422
    assert client.put("/api/profiles/NOPE", json={**edited, "code": "NOPE"}).status_code == 404

    rule = {"account_code": "A", "form": "annual_profile", "profile_code": "WD"}
    used = {**setup(client)["setup"], "entry_forms": [rule]}
    client.put(S, json=used)
    refused = client.delete("/api/profiles/WD")
    assert refused.status_code == 422 and "B2026" in refused.json()["detail"]
    client.put(S, json={**used, "entry_forms": []})
    assert client.delete("/api/profiles/WD").status_code == 200
    assert client.get("/api/profiles").json() == []
    assert client.delete("/api/profiles/WD").status_code == 404


def test_profile_presets(client: TestClient) -> None:
    wd = client.get("/api/profile-presets/working_days", params={"fiscal_year": 2026})
    assert wd.json()[:2] == ["22", "20"]
    cd = client.get("/api/profile-presets/calendar_days", params={"fiscal_year": 2028})
    assert cd.json()[1] == "29"
    assert client.get("/api/profile-presets/moon", params={"fiscal_year": 2026}).status_code == 422


def test_only_controllers(client: TestClient) -> None:
    login(client, OWNER)
    assert client.get(S).status_code == 403
    new = {"code": "X", "name": "X", "kind": "budget", "fiscal_year": 2027}
    assert client.post("/api/rounds", json=new).status_code == 403
    assert client.delete("/api/profiles/ANY").status_code == 403
    assert client.get("/api/profile-presets/working_days?fiscal_year=2026").status_code == 403
