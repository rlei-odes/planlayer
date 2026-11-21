"""The login provider's settings (ADR 0030): from the Settings page (``login_setting``), or from
``PLANLAYER_LOGIN_*`` environment variables, which then win as a whole and make the page read-only.
"""

from dataclasses import asdict, dataclass, fields
from typing import Literal, Self, cast

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, func, select, update

from planlayer.access import User
from planlayer.store.schema import login_setting

Method = Literal["none", "oidc", "ldap"]
METHODS: tuple[Method, ...] = ("none", "oidc", "ldap")
ROLES = ("admin", "controller", "viewer", "hr")
GOOGLE_ISSUER = "https://accounts.google.com"


@dataclass(frozen=True)
class LoginSettings:
    method: Method = "none"
    oidc_label: str = ""
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_scopes: str = "openid profile email"
    oidc_login_claim: str = "preferred_username"
    oidc_email_claim: str = "email"
    oidc_name_claim: str = "name"
    oidc_groups_claim: str = "groups"
    oidc_hosted_domain: str = ""  # Google Workspace: the hd claim every ID token must carry
    ldap_url: str = ""
    ldap_bind_dn: str = ""  # with {login}
    ldap_base_dn: str = ""
    ldap_login_attribute: str = "uid"
    ldap_email_attribute: str = "mail"
    ldap_name_attribute: str = "cn"
    ca_pem: str = ""
    allowed_group: str = ""
    admin_group: str = ""
    controller_group: str = ""
    viewer_group: str = ""
    hr_group: str = ""

    @classmethod
    def of(cls, values: dict[str, object]) -> Self:
        """From a stored or posted mapping; unknown keys are ignored, texts are stripped."""
        known = {f.name for f in fields(cls)}
        clean = {k: str(v).strip() for k, v in values.items() if k in known and v is not None}
        if clean.get("method", "none") not in METHODS:
            raise ValueError(f"method must be one of {', '.join(METHODS)}")
        return cls(**clean)  # type: ignore[arg-type]

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    def role_group(self, role: str) -> str:
        return cast(str, getattr(self, f"{role}_group"))

    def problems(self, *, development: bool) -> list[str]:
        """What keeps these settings from working, before any provider is asked."""
        found: list[str] = []
        match self.method:
            case "oidc":
                if not self.oidc_issuer:
                    found.append("the issuer is missing")
                elif not self.oidc_issuer.startswith("https://") and not (
                    development and self.oidc_issuer.startswith("http://")
                ):
                    found.append("the issuer must be an https:// address")
                if not self.oidc_client_id:
                    found.append("the client ID is missing")
                if "openid" not in self.oidc_scopes.split():
                    found.append("the scopes must include openid")
                if not self.oidc_login_claim:
                    found.append("the login claim is missing")
                # Every Google account has this issuer; the hosted domain limits it to one
                # Workspace, as a tenant's own issuer does for Entra ID.
                if self.oidc_issuer.rstrip("/") == GOOGLE_ISSUER and not self.oidc_hosted_domain:
                    found.append("Google needs the hosted domain")
            case "ldap":
                if not self.ldap_url:
                    found.append("the server is missing")
                elif not self.ldap_url.startswith("ldaps://") and not (
                    development and self.ldap_url.startswith("ldap://")
                ):
                    found.append("the server must be an ldaps:// address")
                if self.ldap_bind_dn.count("{login}") != 1:
                    found.append("the bind DN must contain {login} once")
                if not self.ldap_base_dn:
                    found.append("the base DN is missing")
                if not self.ldap_login_attribute:
                    found.append("the login attribute is missing")
        if self.ca_pem and "-----BEGIN CERTIFICATE-----" not in self.ca_pem:
            found.append("the CA certificate is not in PEM form")
        return found

    def env(self) -> str:
        """The settings as ``PLANLAYER_LOGIN_*`` lines, for a deployment's environment."""
        default = LoginSettings()
        lines = [f"PLANLAYER_LOGIN_METHOD={self.method}"]
        for name, value in self.as_dict().items():
            if name == "method" or value == getattr(default, name):
                continue
            if name.startswith("oidc_") and self.method != "oidc":
                continue
            if name.startswith("ldap_") and self.method != "ldap":
                continue
            lines.append(f"PLANLAYER_LOGIN_{name.upper()}={_quoted(value)}")
        return "\n".join(lines) + "\n"


def _quoted(value: str) -> str:
    """Bare when safe, else double-quoted; line breaks (a CA certificate) as ``\\n``, which
    ``from_env`` turns back into line breaks."""
    if all(c.isalnum() or c in "-_.:/@{}=," for c in value):
        return value
    escaped = value.strip().replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


class _Env(BaseSettings):
    """``PLANLAYER_LOGIN_*``; the method decides whether the environment sets the login."""

    model_config = SettingsConfigDict(
        env_prefix="PLANLAYER_LOGIN_", env_file=".env", extra="ignore"
    )

    method: Method | None = None
    oidc_label: str | None = None
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_scopes: str | None = None
    oidc_login_claim: str | None = None
    oidc_email_claim: str | None = None
    oidc_name_claim: str | None = None
    oidc_groups_claim: str | None = None
    oidc_hosted_domain: str | None = None
    ldap_url: str | None = None
    ldap_bind_dn: str | None = None
    ldap_base_dn: str | None = None
    ldap_login_attribute: str | None = None
    ldap_email_attribute: str | None = None
    ldap_name_attribute: str | None = None
    ca_pem: str | None = None
    allowed_group: str | None = None
    admin_group: str | None = None
    controller_group: str | None = None
    viewer_group: str | None = None
    hr_group: str | None = None


def from_env() -> LoginSettings | None:
    """The settings the environment sets, or None when ``PLANLAYER_LOGIN_METHOD`` is unset."""
    env = _Env()
    if env.method is None:
        return None
    values = {k: v for k, v in env.model_dump().items() if v is not None}
    if "ca_pem" in values:
        values["ca_pem"] = values["ca_pem"].replace("\\n", "\n")
    return LoginSettings.of(values)


def suggested(base: LoginSettings) -> LoginSettings | None:
    """``base`` filled in from ``PLANLAYER_LOGIN_*`` variables set without the method, as a draft
    for the Settings page's first setup; the method follows from them (an issuer: OpenID Connect,
    a server: LDAP). None once a login method is saved, whose settings the page then shows, and
    when no such variable changes anything."""
    env = _Env()
    if env.method is not None or base.method != "none":
        return None
    values = {k: v for k, v in env.model_dump().items() if v is not None and k != "method"}
    if "ca_pem" in values:
        values["ca_pem"] = values["ca_pem"].replace("\\n", "\n")
    if base.method == "none":
        if values.get("oidc_issuer"):
            values["method"] = "oidc"
        elif values.get("ldap_url"):
            values["method"] = "ldap"
    draft = LoginSettings.of({**base.as_dict(), **values})
    return None if draft == base else draft


def stored(engine: Engine) -> LoginSettings:
    names = [f.name for f in fields(LoginSettings)]
    with engine.connect() as conn:
        row = conn.execute(select(*(login_setting.c[n] for n in names))).one()
    return LoginSettings.of(dict(row._mapping))


def load(engine: Engine) -> tuple[LoginSettings, bool]:
    """The settings in force, and whether the environment sets them."""
    env = from_env()
    return (env, True) if env is not None else (stored(engine), False)


def save(engine: Engine, actor: User, settings: LoginSettings, *, development: bool) -> None:
    actor.require_admin()
    if from_env() is not None:
        raise ValueError("the login is set by PLANLAYER_LOGIN_* environment variables")
    if problems := settings.problems(development=development):
        raise ValueError("; ".join(problems))
    with engine.begin() as conn:
        conn.execute(update(login_setting).values(**settings.as_dict(), updated_at=func.now()))
