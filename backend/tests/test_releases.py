"""The notice of a newer release for admins."""

from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from planlayer import access, releases
from planlayer.config import Settings
from planlayer.store.db import create_app_engine

from .conftest import login


@pytest.fixture
def engine(migrated: Settings, client: TestClient) -> Iterator[Engine]:
    e = create_app_engine(migrated)
    yield e
    e.dispose()


RELEASE = releases.Release(version="9.1.0", notes_url="https://github.com/x/releases/tag/v9.1.0")


@pytest.mark.parametrize(
    ("latest", "current", "shown"),
    [
        ("0.6.0", "0.5.0", True),
        ("0.5.1", "0.5.0", True),
        ("0.5.0", "0.5.0", False),
        ("0.5.0", "0.5.0.dev0", False),  # a development build of the release itself
        ("0.5.0", "0.6.0", False),
        ("0.10.0", "0.9.3", True),  # numbers, not text
    ],
)
def test_newer(latest: str, current: str, shown: bool) -> None:
    release = releases.Release(version=latest, notes_url="")
    assert (releases.newer(release, current) is not None) is shown


def test_only_admins_see_a_newer_release(
    engine: Engine, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(releases, "latest", lambda: RELEASE)
    assert client.get("/api/settings/release").status_code == 403  # the controller
    admin = replace(access.system_user(engine), is_admin=True)
    access.create_user(engine, admin, "root@x.example", "Root", is_admin=True)
    login(client, "root@x.example")
    found = client.get("/api/settings/release").json()
    assert (found["latest"], found["notes_url"]) == ("9.1.0", RELEASE.notes_url)
    assert found["upgrade_url"].endswith("install.md#upgrades")


def test_a_failed_check_shows_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def down() -> releases.Release:
        raise TimeoutError("no route")

    monkeypatch.setattr(releases, "fetch", down)
    monkeypatch.setattr(releases, "_cache", None)
    assert releases.latest() is None
    # Not asked again before the retry time.
    monkeypatch.setattr(releases, "fetch", lambda: RELEASE)
    assert releases.latest() is None
