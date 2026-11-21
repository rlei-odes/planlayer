"""Settings, read from ``PLANLAYER_*`` environment variables (or a ``.env`` file)."""

import re
from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class Settings(BaseSettings):
    """Three connections (T-14): app store, source and output.

    Source and output fall back to the app store URL, for installations that keep everything in
    one database.
    """

    model_config = SettingsConfigDict(env_prefix="PLANLAYER_", env_file=".env", extra="ignore")

    environment: Literal["development", "production"] = "production"

    app_database_url: SecretStr
    source_database_url: SecretStr | None = None
    output_database_url: SecretStr | None = None

    app_schema: str = "planlayer_app"
    output_schema: str = "planlayer_out"
    # Schema of the source views, until each dataset is mapped on the mapping page (E-01).
    source_schema: str = "gold"

    # A demo installation (ADR 0030): the login page lists the users and logs in as any of them
    # without a password. Never on an installation with real data.
    demo: bool = False
    # Lifetime of a login session.
    session_hours: int = 12
    # The address users open Planlayer at, e.g. https://plan.example.com; the OpenID Connect
    # callback is under it. Unset: the address of each request.
    public_url: str | None = None

    # The key pay and the login's client secret are encrypted with (ADR 0029): 32 bytes, base64
    # (planlayer new-pay-key). Needed once the app store holds any; a lost key loses them.
    pay_key: SecretStr | None = None

    # Backups (ADR 0032): pg_dump as a role with BYPASSRLS that only reads the app store, into
    # this directory, keeping the newest backup_keep files. Unset: no backups.
    backup_database_url: SecretStr | None = None
    backup_dir: Path | None = None
    backup_keep: int = Field(default=14, ge=1)

    # Ask GitHub for the latest release, for the admins' notice of a newer version. Off for a
    # server without outbound access.
    update_check: bool = True

    # Built frontend served by the API; unset in development, where Vite serves it.
    frontend_dist: Path | None = None

    @field_validator("app_schema", "output_schema", "source_schema")
    @classmethod
    def _plain_identifier(cls, value: str) -> str:
        # Schema names end up in DDL and search paths; only unquoted lowercase identifiers.
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("must be a lowercase SQL identifier (a-z, 0-9, _), max. 63 chars")
        return value

    @field_validator("public_url")
    @classmethod
    def _plain_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if not value.startswith(("https://", "http://")):
            raise ValueError("must start with https:// or http://")
        return value.rstrip("/")

    @property
    def development(self) -> bool:
        return self.environment == "development"

    @cached_property
    def source_url(self) -> str:
        return (self.source_database_url or self.app_database_url).get_secret_value()

    @cached_property
    def output_url(self) -> str:
        return (self.output_database_url or self.app_database_url).get_secret_value()

    @cached_property
    def app_url(self) -> str:
        return self.app_database_url.get_secret_value()
