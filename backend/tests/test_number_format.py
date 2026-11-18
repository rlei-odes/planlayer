"""The number format: the installation's default, set by the admin, and each user's own choices
over it."""

from dataclasses import replace

from fastapi.testclient import TestClient

from planlayer import access
from planlayer.config import Settings
from planlayer.store.db import create_app_engine
from tests.conftest import CONTROLLER, OWNER, login

FULL = {"separators": "comma_dot", "decimals": False, "scale": "full", "units": True}


def test_default_and_own_choices(client: TestClient, migrated: Settings) -> None:
    me = client.get("/api/me").json()
    assert me["number_format"] == me["number_default"] == FULL
    assert me["number_own"] == {"separators": None, "decimals": None, "scale": None, "units": None}

    # Only an admin sets the default.
    swiss = {"separators": "apostrophe_dot", "decimals": True, "scale": "k", "units": False}
    assert client.put("/api/settings/number-format", json=swiss).status_code == 403
    engine = create_app_engine(migrated)
    system = access.system_user(engine)
    access.create_user(
        engine, replace(system, is_admin=True), "adm@x.example", "Adm", is_admin=True
    )
    engine.dispose()
    login(client, "adm@x.example")
    response = client.put("/api/settings/number-format", json=swiss)
    assert response.status_code == 200 and response.json() == swiss

    # A user's own choice wins where it is set; the rest follows the default.
    login(client, CONTROLLER)
    me = client.put("/api/me/number-format", json={"separators": "dot_comma"}).json()
    assert me["number_format"] == {**swiss, "separators": "dot_comma"}
    assert me["number_default"] == swiss
    login(client, OWNER)
    assert client.get("/api/me").json()["number_format"] == swiss

    # Back to the default; an unknown choice is refused.
    login(client, CONTROLLER)
    assert client.put("/api/me/number-format", json={}).json()["number_format"] == swiss
    bad = client.put("/api/me/number-format", json={"scale": "bn"})
    assert bad.status_code == 422
