from fastapi.testclient import TestClient

from planlayer.api.app import create_app
from planlayer.config import Settings
from planlayer.store import migrate


def test_health_reports_schema_revision(migrated: Settings) -> None:
    with TestClient(create_app(migrated)) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["schema_revision"] == migrate.head_revision()


def test_health_before_first_migration(db_settings: Settings) -> None:
    with TestClient(create_app(db_settings)) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["schema_revision"] is None


def test_health_unavailable_without_database() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_database_url="postgresql+psycopg://nobody@127.0.0.1:1/none",  # type: ignore[arg-type]
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
