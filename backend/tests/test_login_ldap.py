"""LDAP against a real directory: the development compose file's lldap (profile ``auth``, users
from deploy/dev/lldap/bootstrap.sh). Skipped unless ``PLANLAYER_TEST_LDAP_URL`` is set, e.g. to
``ldap://localhost:3890``."""

import os

import pytest

from planlayer.login import ldap
from planlayer.login.settings import LoginSettings

URL = os.environ.get("PLANLAYER_TEST_LDAP_URL")
PASSWORD = "planlayer-dev"

pytestmark = pytest.mark.skipif(not URL, reason="PLANLAYER_TEST_LDAP_URL is not set")


@pytest.fixture
def settings() -> LoginSettings:
    assert URL
    return LoginSettings(
        method="ldap",
        ldap_url=URL,
        ldap_bind_dn="uid={login},ou=people,dc=nordwerk,dc=example",
        ldap_base_dn="dc=nordwerk,dc=example",
        ldap_name_attribute="displayName",
    )


def test_bind_reads_the_entry(settings: LoginSettings) -> None:
    found = ldap.bind(settings, "Admin", PASSWORD).identity
    assert found is not None
    assert (found.key, found.login, found.display_name, found.email) == (
        "ldap:uid=admin,ou=people,dc=nordwerk,dc=example",
        "admin",
        "Adam Admin",
        "admin@nordwerk.example",
    )
    assert {"planlayer", "planlayer-admins"} <= found.groups
    assert "cn=planlayer-admins,ou=groups,dc=nordwerk,dc=example" in found.groups


@pytest.mark.parametrize(
    ("login", "password"),
    [("admin", "wrong"), ("admin", ""), ("adm*", PASSWORD), ("nobody", PASSWORD)],
)
def test_wrong_logins_find_nobody(settings: LoginSettings, login: str, password: str) -> None:
    assert ldap.bind(settings, login, password).identity is None


def test_an_unreachable_directory_is_an_error(settings: LoginSettings) -> None:
    down = LoginSettings.of({**settings.as_dict(), "ldap_url": "ldap://localhost:1"})
    with pytest.raises(ldap.LoginError, match="unable to open socket"):
        ldap.bind(down, "admin", PASSWORD)
