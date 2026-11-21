"""Login (ADR 0030): the first run's admin, passwords and their limit, the provider's settings,
identities matched to users, OpenID Connect against a fake provider, LDAP's API path."""

import base64
import json
import re
import time
from collections.abc import Iterator
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update

from planlayer import access
from planlayer.api.app import create_app
from planlayer.config import Settings
from planlayer.login import identity, ldap, local, oidc, passwords
from planlayer.login import settings as login_settings
from planlayer.login.identity import Identity
from planlayer.login.settings import LoginSettings
from planlayer.store.db import create_app_engine
from planlayer.store.schema import app_user, login_event, login_setting, oidc_login

PASSWORD = "correct horse battery"
ISSUER = "https://id.example.com/tenant"
CLIENT_ID = "planlayer"


@pytest.fixture
def prod(migrated: Settings) -> Settings:
    """A production-like installation: no demo, not development (Secure cookies are sent over
    the test client's https base URL)."""
    return migrated.model_copy(update={"demo": False, "environment": "production"})


@pytest.fixture
def engine(prod: Settings) -> Iterator[Engine]:
    e = create_app_engine(prod)
    yield e
    e.dispose()


def https_client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings), base_url="https://plan.example.com")


def setup_code(caplog: pytest.LogCaptureFixture) -> str:
    codes = re.findall(r"Setup code for the first admin account: ([A-Z0-9-]+)", caplog.text)
    assert codes, caplog.text
    return codes[-1]


def first_admin(client: TestClient, code: str, **values: str) -> Any:
    body = {
        "code": code,
        "login": "root",
        "display_name": "Root",
        "email": "root@x.example",
        "password": PASSWORD,
        **values,
    }
    return client.post("/api/auth/setup", json=body)


def set_method(engine: Engine, **values: str) -> None:
    with engine.begin() as conn:
        conn.execute(update(login_setting).values(**values))


# --- Settings ----------------------------------------------------------------------------------


def test_settings_problems() -> None:
    s = LoginSettings(method="oidc", oidc_issuer="http://kc.local/realms/x", oidc_client_id="c")
    assert s.problems(development=True) == []
    assert s.problems(development=False) == ["the issuer must be an https:// address"]
    ldap_s = LoginSettings(
        method="ldap", ldap_url="ldaps://dc", ldap_bind_dn="uid=x", ldap_base_dn=""
    )
    assert ldap_s.problems(development=False) == [
        "the bind DN must contain {login} once",
        "the base DN is missing",
    ]
    with pytest.raises(ValueError, match="method"):
        LoginSettings.of({"method": "saml"})
    google = LoginSettings(
        method="oidc", oidc_issuer="https://accounts.google.com", oidc_client_id="c"
    )
    assert google.problems(development=False) == ["Google needs the hosted domain"]
    assert replace(google, oidc_hosted_domain="corp.example").problems(development=False) == []


def test_settings_round_trip_through_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
    s = LoginSettings(
        method="ldap",
        ldap_url="ldaps://dc01.corp.example:636",
        ldap_bind_dn="{login}@corp.example",
        ldap_base_dn="DC=corp,DC=example",
        ldap_login_attribute="sAMAccountName",
        ca_pem=pem,
        admin_group="Planlayer Admins",
        oidc_issuer="ignored, not the method",
    )
    text = s.env()
    assert "OIDC" not in text
    assert 'PLANLAYER_LOGIN_ADMIN_GROUP="Planlayer Admins"' in text
    for line in text.splitlines():
        name, _, value = line.partition("=")
        if value.startswith('"'):
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        monkeypatch.setenv(name, value)
    assert login_settings.from_env() == replace(s, oidc_issuer="")


def test_settings_from_the_environment_win(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    admin = replace(access.system_user(engine), is_admin=True)
    monkeypatch.setenv("PLANLAYER_LOGIN_METHOD", "oidc")
    monkeypatch.setenv("PLANLAYER_LOGIN_OIDC_ISSUER", ISSUER)
    s, from_env = login_settings.load(engine)
    assert from_env and s.oidc_issuer == ISSUER and s.oidc_login_claim == "preferred_username"
    with pytest.raises(ValueError, match="environment"):
        login_settings.save(engine, admin, LoginSettings(), development=False)


def test_variables_without_the_method_fill_in_a_draft(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = login_settings.stored(engine)
    assert login_settings.suggested(saved) is None
    monkeypatch.setenv("PLANLAYER_LOGIN_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("PLANLAYER_LOGIN_ADMIN_GROUP", "admins")
    draft = login_settings.suggested(saved)
    assert draft == replace(saved, method="oidc", oidc_issuer=ISSUER, admin_group="admins")
    assert login_settings.load(engine) == (saved, False)  # a draft only, nothing in force
    # Once a method is saved, the page shows what is saved, not the variables.
    set_method(engine, method="ldap", ldap_url="ldaps://dc", ldap_bind_dn="uid={login}")
    assert login_settings.suggested(login_settings.stored(engine)) is None


# --- First run ---------------------------------------------------------------------------------


def test_first_run_creates_a_local_admin(
    prod: Settings, engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("WARNING", "planlayer.login")
    with https_client(prod) as client:
        assert client.get("/api/auth/options").json() == {
            "demo": False,
            "setup": True,
            "oidc": None,
            "ldap": False,
        }
        code = setup_code(caplog)
        assert first_admin(client, "AAAA-BBBB-CCCC-DDDD").status_code == 422
        assert first_admin(client, code, password="short").status_code == 422
        response = first_admin(client, code.lower().replace("-", ""))
        assert response.status_code == 200, response.text
        assert response.json()["is_admin"] is True
        assert "secure" in response.headers["set-cookie"].lower()
        assert client.get("/api/me").json()["login"] == "root"
        assert client.get("/api/auth/options").json()["setup"] is False
        # The code is used up, and an admin can log in now.
        assert first_admin(client, code, login="second").status_code == 422
    with engine.connect() as conn:
        outcomes = conn.execute(
            select(login_event.c.method, login_event.c.outcome).order_by(login_event.c.event_id)
        ).all()
    assert outcomes == [("setup", "failed")] * 2 + [("setup", "success"), ("setup", "failed")]


def test_each_start_issues_a_new_setup_code(
    prod: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("WARNING", "planlayer.login")
    with https_client(prod) as client:
        first = setup_code(caplog)
        client.get("/api/auth/options")  # the page asking doesn't replace it
        assert setup_code(caplog) == first
    with https_client(prod) as client:
        second = setup_code(caplog)
        assert second != first
        assert first_admin(client, first).status_code == 422
        assert first_admin(client, second).status_code == 200


def test_setup_code_command(prod: Settings, engine: Engine) -> None:
    s = LoginSettings()
    code = local.ensure_code(engine, s, renew=True)
    assert code is not None and local.ensure_code(engine, s) is None  # kept, not reissued
    again = local.ensure_code(engine, s, renew=True)
    assert again is not None and again != code
    with https_client(prod) as client:
        assert first_admin(client, code).status_code == 422
    with https_client(prod) as client:
        new = local.ensure_code(engine, s, renew=True)
        assert new is not None
        assert first_admin(client, new).status_code == 200
    assert local.ensure_code(engine, s, renew=True) is None  # an admin can log in now


def test_first_run_while_a_provider_gives_no_admin(engine: Engine) -> None:
    s = LoginSettings(method="oidc", oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID)
    assert local.needed(engine, s)
    assert not local.needed(engine, replace(s, admin_group="admins"))


# --- Passwords ---------------------------------------------------------------------------------


def test_password_hashes() -> None:
    stored = passwords.hash_password(PASSWORD)
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    assert passwords.verify(PASSWORD, stored)
    assert not passwords.verify(PASSWORD + "!", stored)
    assert passwords.hash_password(PASSWORD) != stored  # salted


def test_password_login_and_its_limit(prod: Settings, engine: Engine) -> None:
    admin = replace(access.system_user(engine), is_admin=True)
    access.create_user(engine, admin, "eva", "Eva", is_controller=True)
    local.set_password(engine, "Eva", PASSWORD)
    eva = next(u for u in access.users(engine) if u.login == "eva")
    assert (eva.local, eva.provider) == (True, None)
    with https_client(prod) as client:
        ok = client.post("/api/auth/login", json={"login": "EVA", "password": PASSWORD})
        assert ok.status_code == 200 and ok.json()["is_controller"] is True
        for _ in range(5):
            bad = client.post("/api/auth/login", json={"login": "eva", "password": "wrong"})
            assert bad.status_code == 401
        blocked = client.post("/api/auth/login", json={"login": "eva", "password": PASSWORD})
        assert blocked.status_code == 429
        unknown = client.post("/api/auth/login", json={"login": "nobody", "password": PASSWORD})
        assert unknown.status_code == 401
        assert client.post("/api/auth/demo-login", json={"login": "eva"}).status_code == 404


def test_a_disabled_user_cant_log_in(prod: Settings, engine: Engine) -> None:
    admin = replace(access.system_user(engine), is_admin=True)
    access.create_user(engine, admin, "eva", "Eva")
    local.set_password(engine, "eva", PASSWORD)
    access.update_user(engine, admin, "eva", disabled=True)
    with https_client(prod) as client:
        r = client.post("/api/auth/login", json={"login": "eva", "password": PASSWORD})
        assert r.status_code == 401
    with pytest.raises(access.UserError):
        local.set_password(engine, "eva", PASSWORD)


# --- Identities --------------------------------------------------------------------------------


def person(**values: Any) -> Identity:
    base: dict[str, Any] = {
        "key": "oidc:https://id.example.com 123",
        "login": "Anna@Corp.Example",
        "display_name": "Anna",
        "email": "anna@x.example",
    }
    return Identity(**(base | values))


def roles(engine: Engine, login: str) -> tuple[bool, bool, bool, bool]:
    with engine.connect() as conn:
        r = conn.execute(
            select(
                app_user.c.is_admin,
                app_user.c.is_controller,
                app_user.c.is_viewer,
                app_user.c.is_hr,
            ).where(app_user.c.login == login)
        ).one()
    return tuple(r)  # type: ignore[return-value]


def test_a_first_login_creates_a_user_without_roles(engine: Engine) -> None:
    s = LoginSettings(method="oidc")
    assert identity.preview(engine, s, person()) == "a new user anna@corp.example"
    assert identity.resolve(engine, s, person()) == "anna@corp.example"
    assert roles(engine, "anna@corp.example") == (False, False, False, False)
    # Found by the identity from then on, though the provider renames the login.
    assert identity.resolve(engine, s, person(login="anna.k@corp.example")) == "anna@corp.example"
    listed = {u.login: u for u in access.users(engine)}
    assert (listed["anna@corp.example"].local, listed["anna@corp.example"].provider) == (
        False,
        "oidc",
    )


def test_matched_by_login_then_by_verified_email(engine: Engine) -> None:
    admin = replace(access.system_user(engine), is_admin=True)
    access.create_user(engine, admin, "anna", "Anna", "Anna@X.example", is_controller=True)
    s = LoginSettings(method="oidc")
    # An unverified e-mail isn't matched: a new user.
    unverified = person(key="oidc:x 1", login="a1", email_verified=False)
    assert identity.resolve(engine, s, unverified) == "a1"
    with engine.connect() as conn:
        kept = conn.execute(select(app_user.c.email).where(app_user.c.login == "a1")).scalar()
    assert kept is None
    assert identity.resolve(engine, s, person(key="oidc:x 2", login="a2")) == "anna"
    assert roles(engine, "anna") == (False, True, False, False)  # roles stay
    # Linked to the first identity: another one with the same e-mail is refused.
    with pytest.raises(identity.Refused, match="another identity"):
        identity.resolve(engine, s, person(key="oidc:x 3", login="a3"))


def test_two_matches_are_refused(engine: Engine) -> None:
    admin = replace(access.system_user(engine), is_admin=True)
    access.create_user(engine, admin, "anna", "Anna")
    access.create_user(engine, admin, "anna2", "Anna", "anna@x.example")
    with pytest.raises(identity.Refused, match="two different users"):
        identity.resolve(engine, LoginSettings(method="oidc"), person(login="anna"))


def test_groups_admit_and_set_roles(engine: Engine) -> None:
    s = LoginSettings(
        method="ldap",
        allowed_group="planlayer",
        admin_group="cn=pl-admins,ou=groups,dc=x",
        controller_group="/finance",
    )
    outsider = person(groups=identity.group_names(["staff"]))
    with pytest.raises(identity.Refused, match="allowed group"):
        identity.resolve(engine, s, outsider)
    groups = identity.group_names(
        ["CN=Planlayer,OU=Groups,DC=x", "cn=pl-admins,ou=groups,dc=x", "/Finance"]
    )
    login = identity.resolve(engine, s, person(groups=groups))
    assert roles(engine, login) == (True, True, False, False)
    # At the next login without the groups, the roles they set are gone; viewer and HR,
    # without a group, are the Users page's.
    access.update_user(
        engine, replace(access.system_user(engine), is_admin=True), login, is_hr=True
    )
    identity.resolve(engine, s, person(groups=identity.group_names(["planlayer"])))
    assert roles(engine, login) == (False, False, False, True)


def test_a_disabled_user_is_refused(engine: Engine) -> None:
    admin = replace(access.system_user(engine), is_admin=True)
    access.create_user(engine, admin, "anna@corp.example", "Anna")
    access.update_user(engine, admin, "anna@corp.example", disabled=True)
    with pytest.raises(identity.Refused, match="disabled"):
        identity.resolve(engine, LoginSettings(method="oidc"), person())


# --- OpenID Connect, against a fake provider ----------------------------------------------------


def jwt(claims: dict[str, Any]) -> str:
    def part(d: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part(claims)}.c2ln"


class Provider:
    """Discovery, token and userinfo endpoints, answering as a provider would."""

    def __init__(self) -> None:
        self.nonce = ""
        self.claims: dict[str, Any] = {}
        self.token_forms: list[dict[str, str]] = []

    def __call__(
        self,
        _: LoginSettings,
        url: str,
        *,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        if url.endswith("/.well-known/openid-configuration"):
            return 200, {
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "userinfo_endpoint": f"{ISSUER}/userinfo",
            }
        if url.endswith("/token"):
            assert form is not None
            self.token_forms.append(form)
            if form["code"] != "good-code":
                return 400, {"error": "invalid_grant"}
            claims = {
                "iss": ISSUER,
                "aud": CLIENT_ID,
                "exp": time.time() + 300,
                "nonce": self.nonce,
                "sub": "u-1",
                "preferred_username": "anna@corp.example",
                "name": "Anna Keller",
            } | self.claims
            return 200, {"id_token": jwt(claims), "access_token": "at"}
        if url.endswith("/userinfo"):
            return 200, {"sub": "u-1", "email": "anna@x.example", "groups": ["/planlayer"]}
        return 404, {}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> Provider:
    p = Provider()
    monkeypatch.setattr(oidc, "_request", p)
    set_method(
        engine, method="oidc", oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID, oidc_label="Microsoft"
    )
    return p


def nonce_of(engine: Engine, state: str) -> str:
    with engine.connect() as conn:
        nonce: str = conn.execute(
            select(oidc_login.c.nonce).where(oidc_login.c.state == state)
        ).scalar_one()
    return nonce


def go_to_provider(client: TestClient, provider: Provider, engine: Engine) -> str:
    r = client.get("/api/auth/oidc/login", follow_redirects=False)
    assert r.status_code == 303, r.text
    url = urlsplit(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{ISSUER}/authorize"
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q["client_id"] == CLIENT_ID and q["code_challenge_method"] == "S256"
    assert q["redirect_uri"] == "https://plan.example.com/api/auth/oidc/callback"
    provider.nonce = nonce_of(engine, q["state"])
    return q["state"]


def test_oidc_login(prod: Settings, engine: Engine, provider: Provider) -> None:
    with https_client(prod) as client:
        assert client.get("/api/auth/options").json()["oidc"] == "Microsoft"
        state = go_to_provider(client, provider, engine)
        r = client.get(
            f"/api/auth/oidc/callback?state={state}&code=good-code", follow_redirects=False
        )
        assert r.status_code == 303 and r.headers["location"] == "/"
        me = client.get("/api/me").json()
        assert (me["login"], me["display_name"], me["email"]) == (
            "anna@corp.example",
            "Anna Keller",
            "anna@x.example",
        )
        # PKCE: the verifier goes with the code; a public client names itself.
        form = provider.token_forms[-1]
        assert form["client_id"] == CLIENT_ID and len(form["code_verifier"]) > 40
        # The state is used up.
        again = client.get(
            f"/api/auth/oidc/callback?state={state}&code=good-code", follow_redirects=False
        )
        assert "login_error" in again.headers["location"]


@pytest.mark.parametrize(
    ("claims", "error"),
    [
        ({"aud": "someone-else"}, "not for this client"),
        ({"iss": "https://evil.example"}, "issuer"),
        ({"exp": time.time() - 3600}, "expired"),
        ({"nonce": "replayed"}, "nonce"),
    ],
)
def test_oidc_id_token_checks(
    prod: Settings,
    engine: Engine,
    provider: Provider,
    claims: dict[str, Any],
    error: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider.claims = claims
    with https_client(prod) as client:
        state = go_to_provider(client, provider, engine)
        r = client.get(
            f"/api/auth/oidc/callback?state={state}&code=good-code", follow_redirects=False
        )
        assert "login_error" in r.headers["location"]
        assert client.get("/api/me").status_code == 401
    assert error in caplog.text


@pytest.mark.parametrize(
    ("claims", "logs_in"),
    [
        ({"hd": "Corp.Example"}, True),
        ({"hd": "other.example"}, False),
        ({}, False),  # a personal account: Google sends no hd
    ],
)
def test_oidc_hosted_domain(
    prod: Settings,
    engine: Engine,
    provider: Provider,
    claims: dict[str, Any],
    logs_in: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with engine.begin() as conn:
        conn.execute(update(login_setting).values(oidc_hosted_domain="corp.example"))
    provider.claims = claims
    with https_client(prod) as client:
        r = client.get("/api/auth/oidc/login", follow_redirects=False)
        q = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
        assert q["hd"] == "corp.example"
        provider.nonce = nonce_of(engine, q["state"])
        client.get(f"/api/auth/oidc/callback?state={q['state']}&code=good-code")
        assert (client.get("/api/me").status_code == 200) is logs_in
    assert logs_in or "hosted domain" in caplog.text


def test_oidc_test_with_unsaved_settings(
    prod: Settings, engine: Engine, provider: Provider
) -> None:
    admin = replace(access.system_user(engine), is_admin=True)
    access.create_user(engine, admin, "root", "Root", is_admin=True)
    local.set_password(engine, "root", PASSWORD)
    set_method(engine, method="none")
    with https_client(prod) as client:
        client.post("/api/auth/login", json={"login": "root", "password": PASSWORD})
        page = client.get("/api/settings/login").json()
        assert page["settings"]["method"] == "none" and page["from_env"] is False
        draft = page["settings"] | {
            "method": "oidc",
            "oidc_issuer": ISSUER,
            "oidc_client_id": CLIENT_ID,
            "admin_group": "planlayer",
        }
        r = client.post("/api/settings/login/test-oidc", json={"settings": draft})
        assert r.status_code == 200, r.text
        start = urlsplit(r.json()["url"])
        state = parse_qs(start.query)["state"][0]
        provider.nonce = nonce_of(engine, state)
        back = client.get(
            f"/api/auth/oidc/callback?state={state}&code=good-code", follow_redirects=False
        )
        assert back.status_code == 200 and "planlayerLoginTest" in back.text
        assert f"/?login_test={state}#settings" in back.text  # without a popup: back to Settings
        assert r.json()["state"] == state
        result = client.get(f"/api/settings/login/test-oidc/{state}").json()
        assert result["ok"] is True, result
        steps = {s["label"]: s["detail"] for s in result["steps"]}
        assert steps["Logs in as"] == "a new user anna@corp.example"
        assert steps["Roles"] == "admin yes"
        assert result["groups"] == ["planlayer"]
        assert "PLANLAYER_LOGIN_METHOD=oidc" in result["env"]
        assert f"PLANLAYER_LOGIN_OIDC_ISSUER={ISSUER}" in result["env"]
        # Nothing saved, no session made: still the admin, still no method.
        assert client.get("/api/me").json()["login"] == "root"
        assert client.get("/api/settings/login").json()["settings"]["method"] == "none"
        saved = client.put("/api/settings/login", json=draft)
        assert saved.status_code == 200 and saved.json()["settings"]["method"] == "oidc"
        insecure = draft | {"oidc_issuer": "http://id.example.com"}
        assert client.put("/api/settings/login", json=insecure).status_code == 422


def test_login_settings_are_for_admins(client: TestClient) -> None:
    """The slice's client is logged in as the controller."""
    assert client.get("/api/settings/login").status_code == 403


# --- LDAP: the API around the directory (the directory itself: test_login_ldap.py) --------------


def test_ldap_password_login(
    prod: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_method(
        engine,
        method="ldap",
        ldap_url="ldaps://dc.example",
        ldap_bind_dn="uid={login},ou=people,dc=x",
        ldap_base_dn="dc=x",
    )

    def bind(_: LoginSettings, login: str, password: str) -> ldap.Bound:
        if login == "down":
            raise ldap.LoginError("dc.example not reachable")
        if password != PASSWORD:
            return ldap.Bound("uid=x", None, [])
        found = Identity(key=f"ldap:uid={login},dc=x", login=login, display_name="Luca")
        return ldap.Bound(f"uid={login}", found, ["uid", "cn"])

    monkeypatch.setattr(ldap, "bind", bind)
    with https_client(prod) as client:
        assert client.get("/api/auth/options").json()["ldap"] is True
        r = client.post("/api/auth/login", json={"login": "luca", "password": PASSWORD})
        assert r.status_code == 200 and r.json()["display_name"] == "Luca"
        assert (
            client.post("/api/auth/login", json={"login": "luca", "password": "x"}).status_code
            == 401
        )
        assert (
            client.post("/api/auth/login", json={"login": "down", "password": "x"}).status_code
            == 503
        )


def test_connections_for_admins(client: TestClient, engine: Engine) -> None:
    """The slice's client is the controller; the app store is the test schema."""
    assert client.get("/api/settings/connections").status_code == 403
    admin = replace(access.system_user(engine), is_admin=True)
    access.create_user(engine, admin, "root", "Root", is_admin=True)
    client.post("/api/auth/demo-login", json={"login": "root"})
    found = {c["role"]: c for c in client.get("/api/settings/connections").json()}
    assert set(found) == {"app", "source", "output"}
    assert all(c["ok"] and c["server"].startswith("PostgreSQL ") for c in found.values())
    assert found["app"]["user"] == "planlayer_test" and found["output"]["shared"] is True
    assert "planlayer_test@" not in str(found)  # parts, never the URL with its password
