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


def test_demo_is_off_by_default() -> None:
    assert make().demo is False


@pytest.mark.parametrize("url", ["plan.example.com", "ftp://plan.example.com"])
def test_public_url_must_be_http(url: str) -> None:
    with pytest.raises(ValidationError, match="https://"):
        make(public_url=url)


def test_public_url_without_trailing_slash() -> None:
    assert make(public_url="https://plan.example.com/").public_url == "https://plan.example.com"
