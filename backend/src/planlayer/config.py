"""Settings, read from ``PLANLAYER_*`` environment variables (or a ``.env`` file)."""

import re
from functools import cached_property
from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
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

    # Fake login for early iterations (T-22). Refused in production.
    dev_login: bool = False

    # Built frontend served by the API; unset in development, where Vite serves it.
    frontend_dist: Path | None = None

    @field_validator("app_schema", "output_schema", "source_schema")
    @classmethod
    def _plain_identifier(cls, value: str) -> str:
        # Schema names end up in DDL and search paths; only unquoted lowercase identifiers.
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("must be a lowercase SQL identifier (a-z, 0-9, _), max. 63 chars")
        return value

    @model_validator(mode="after")
    def _no_dev_login_in_production(self) -> Self:
        if self.dev_login and self.environment == "production":
            raise ValueError("PLANLAYER_DEV_LOGIN is not allowed in production")
        return self

    @cached_property
    def source_url(self) -> str:
        return (self.source_database_url or self.app_database_url).get_secret_value()

    @cached_property
    def output_url(self) -> str:
        return (self.output_database_url or self.app_database_url).get_secret_value()

    @cached_property
    def app_url(self) -> str:
        return self.app_database_url.get_secret_value()
