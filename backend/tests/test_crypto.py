"""Pay and the client secret encrypted at rest (ADR 0029)."""

from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, select, text

from planlayer import access, crypto
from planlayer.api.app import create_app
from planlayer.config import Settings
from planlayer.login import local
from planlayer.login import settings as login_settings
from planlayer.store.db import create_app_engine
from planlayer.store.schema import APP_SCHEMA, hr_pay

from .conftest import PAY_KEY, TEST_DATABASE_URL, run_sync

OTHER_KEY = SecretStr(crypto.new_key())


@pytest.fixture
def key() -> Iterator[None]:
    crypto.configure(SecretStr(PAY_KEY))
    yield
    crypto.configure(SecretStr(PAY_KEY))


# --- The format -----------------------------------------------------------------------------------


def test_a_sealed_value_opens_only_in_its_place_with_its_key(key: None) -> None:
    a, b = (
        crypto.seal("4500.00", "hr_pay.monthly_salary"),
        crypto.seal("4500.00", "hr_pay.monthly_salary"),
    )
    assert a != b  # a fresh nonce each time: equal salaries don't look equal
    assert b"4500" not in a
    assert crypto.unseal(a, "hr_pay.monthly_salary") == "4500.00"
    with pytest.raises(crypto.PayKeyError, match="doesn't open"):
        crypto.unseal(a, "position_pay.salary")  # moved to another column
    with pytest.raises(crypto.PayKeyError, match="doesn't open"):
        crypto.unseal(a[:-1] + bytes([a[-1] ^ 1]), "hr_pay.monthly_salary")  # changed
    crypto.configure(OTHER_KEY)
    with pytest.raises(crypto.PayKeyError, match="another key"):
        crypto.unseal(a, "hr_pay.monthly_salary")
    crypto.configure(None)
    with pytest.raises(crypto.PayKeyError, match="PLANLAYER_PAY_KEY is not set"):
        crypto.unseal(a, "hr_pay.monthly_salary")


def test_amounts_keep_their_exact_value_and_blank_needs_no_key(key: None) -> None:
    amount = crypto.SealedAmount("position_pay.bonus")
    for value in (Decimal("0.00"), Decimal("1234567890123456.78"), Decimal("12")):
        sealed = amount.process_bind_param(value, None)  # type: ignore[arg-type]
        assert amount.process_result_value(sealed, None) == value  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Decimal"):
        amount.process_bind_param(0.1, None)  # type: ignore[arg-type]
    crypto.configure(None)
    assert amount.process_bind_param(None, None) is None  # type: ignore[arg-type]
    assert amount.process_result_value(None, None) is None  # type: ignore[arg-type]


def test_keys_are_checked() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        crypto.parse_key("c2hvcnQ=")
    with pytest.raises(ValueError, match="base64"):
        crypto.parse_key("not base64!")
    assert len(crypto.parse_key(crypto.new_key())) == 32


# --- In the app store -----------------------------------------------------------------------------


def _admin_engine(schema: str) -> Engine:
    """The superuser, past row-level security, with the app's table names."""
    assert TEST_DATABASE_URL
    return create_engine(TEST_DATABASE_URL).execution_options(
        schema_translate_map={APP_SCHEMA: schema}
    )


def _salaries(schema: str) -> dict[str, Decimal]:
    admin = _admin_engine(schema)
    with admin.connect() as conn:
        found = {r.code: r.monthly_salary for r in conn.execute(select(hr_pay))}
    admin.dispose()
    return found


def test_pay_is_sealed_in_the_database(migrated: Settings, source_schema: str) -> None:
    run_sync(migrated, source_schema)
    salaries = _salaries(migrated.app_schema)
    assert salaries and all(isinstance(v, Decimal) for v in salaries.values())
    admin = _admin_engine(migrated.app_schema)
    with admin.connect() as conn:
        raw = conn.execute(
            text(f"SELECT monthly_salary FROM {migrated.app_schema}.hr_pay")
        ).scalars()
        stored = [bytes(v) for v in raw]
    admin.dispose()
    assert stored and all(v[0] == crypto.VERSION for v in stored)
    assert not any(str(s).encode() in v for s, v in zip(salaries.values(), stored, strict=False))


# --- The client secret ----------------------------------------------------------------------------


@pytest.fixture
def admin_client(migrated: Settings) -> Iterator[TestClient]:
    settings = migrated.model_copy(update={"demo": False})
    engine = create_app_engine(settings)
    admin = replace(access.system_user(engine), is_admin=True)
    access.create_user(engine, admin, "root", "Root", is_admin=True)
    local.set_password(engine, "root", "correct horse battery")
    engine.dispose()
    with TestClient(create_app(settings)) as c:
        c.post("/api/auth/login", json={"login": "root", "password": "correct horse battery"})
        yield c


def test_the_client_secret_is_saved_sealed_and_never_sent_back(
    migrated: Settings, admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(login_settings.CLIENT_SECRET_ENV, raising=False)
    r = admin_client.put("/api/settings/login/client-secret", json={"secret": " s3cret "})
    assert r.status_code == 200 and r.json()["client_secret"] == "saved"
    assert "s3cret" not in r.text
    engine = create_app_engine(migrated)
    assert login_settings.client_secret(engine) == "s3cret"
    admin = _admin_engine(migrated.app_schema)
    with admin.connect() as conn:
        raw = conn.execute(
            text(f"SELECT oidc_client_secret FROM {migrated.app_schema}.login_setting")
        ).scalar_one()
    admin.dispose()
    assert b"s3cret" not in bytes(raw)
    # The environment's wins, and the saved one can't be changed meanwhile.
    monkeypatch.setenv(login_settings.CLIENT_SECRET_ENV, "from-env")
    assert login_settings.client_secret(engine) == "from-env"
    assert admin_client.get("/api/settings/login").json()["client_secret"] == "env"
    assert (
        admin_client.put("/api/settings/login/client-secret", json={"secret": "x"}).status_code
        == 422
    )
    monkeypatch.delenv(login_settings.CLIENT_SECRET_ENV)
    # Without the key it can't be read: the page says why.
    crypto.configure(None)
    r = admin_client.put("/api/settings/login/client-secret", json={"secret": "new"})
    assert r.status_code == 503 and "PLANLAYER_PAY_KEY" in r.json()["detail"]
    crypto.configure(SecretStr(PAY_KEY))
    r = admin_client.put("/api/settings/login/client-secret", json={"secret": None})
    assert r.json()["client_secret"] is None and login_settings.client_secret(engine) is None
    engine.dispose()
