import pytest
from pydantic import ValidationError

from planlayer.config import Settings

APP_URL = "postgresql+psycopg://planlayer@db/planlayer"


def make(**values: object) -> Settings:
    return Settings(_env_file=None, app_database_url=APP_URL, **values)  # type: ignore[call-arg, arg-type]


def test_source_and_output_fall_back_to_app_store() -> None:
    settings = make()
    assert settings.source_url == APP_URL
    assert settings.output_url == APP_URL


def test_separate_output_database() -> None:
    dwh = "postgresql+psycopg://planlayer@db/dwh"
    settings = make(source_database_url=dwh, output_database_url=dwh)
    assert settings.app_url == APP_URL
    assert settings.source_url == dwh
    assert settings.output_url == dwh


def test_urls_are_hidden_in_repr() -> None:
    assert APP_URL not in repr(make())


@pytest.mark.parametrize("name", ["Planlayer", "plan-layer", "1plan", "x; DROP SCHEMA y", "a" * 64])
def test_schema_names_must_be_plain_identifiers(name: str) -> None:
    with pytest.raises(ValidationError):
        make(app_schema=name)


def test_dev_login_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="not allowed in production"):
        make(dev_login=True, environment="production")


def test_dev_login_allowed_in_development() -> None:
    assert make(dev_login=True, environment="development").dev_login
