"""Login against the company's directory (ADR 0030): Active Directory, OpenLDAP, lldap, …

A bind as the user, with the DN from the template and their own password, then a search for their
entry under the base DN with that bind: its e-mail, name and ``memberOf``. No service account.
"""

import ssl
from dataclasses import dataclass

import ldap3
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn

from planlayer.login.identity import Identity, group_names
from planlayer.login.settings import LoginSettings

TIMEOUT_S = 10


class LoginError(Exception):
    """The directory couldn't be asked, or answered something unexpected; the message says why,
    without the password."""


@dataclass(frozen=True)
class Bound:
    """What a bind and the entry's search found, for a login and for the Settings page's test."""

    bind_dn: str
    identity: Identity | None  # None: wrong login or password
    attributes: list[str]


def _server(settings: LoginSettings) -> ldap3.Server:
    """The server, its certificate checked: against the pasted CA, or the system's."""
    ldaps = settings.ldap_url.startswith("ldaps://")
    tls = ldap3.Tls(validate=ssl.CERT_REQUIRED, ca_certs_data=settings.ca_pem or None)
    # No schema read: attribute names are the directory's to check, and a missing one just
    # comes back empty (directories differ in what their schema declares).
    return ldap3.Server(
        settings.ldap_url,
        use_ssl=ldaps,
        tls=tls if ldaps else None,
        connect_timeout=TIMEOUT_S,
        get_info=ldap3.NONE,
    )


def _values(entry: ldap3.Entry, name: str) -> list[str]:
    for key, values in entry.entry_attributes_as_dict.items():
        if key.lower() == name.lower():
            return [str(v) for v in values if v is not None and str(v).strip()]
    return []


def bind(settings: LoginSettings, login: str, password: str) -> Bound:
    """Bind as this login and read the entry. A wrong login or password gives no identity; a
    directory that can't be reached or found no entry raises LoginError."""
    login = login.strip()
    bind_dn = settings.ldap_bind_dn.replace("{login}", escape_rdn(login))
    if not login or not password:
        # An empty password is an anonymous bind to LDAP, which succeeds: never send one.
        return Bound(bind_dn, None, [])
    try:
        conn = ldap3.Connection(
            _server(settings),
            user=bind_dn,
            password=password,
            receive_timeout=TIMEOUT_S,
            raise_exceptions=False,
            check_names=False,
        )
        if not conn.bind():
            description = conn.result.get("description") if conn.result else None
            if description == "invalidCredentials":
                return Bound(bind_dn, None, [])
            raise LoginError(f"the directory refused the bind: {description or conn.last_error}")
        try:
            attr = settings.ldap_login_attribute
            wanted = [
                a for a in (attr, settings.ldap_email_attribute, settings.ldap_name_attribute) if a
            ]
            conn.search(
                settings.ldap_base_dn,
                f"({escape_filter_chars(attr)}={escape_filter_chars(login)})",
                attributes=[*wanted, "memberOf"],
                size_limit=2,
            )
            entries = list(conn.entries)
        finally:
            conn.unbind()  # type: ignore[no-untyped-call]
    except LDAPException as e:
        raise LoginError(f"{settings.ldap_url}: {type(e).__name__}: {e}") from e
    if not entries:
        raise LoginError(
            f"bound as {bind_dn}, but no entry with {attr}={login} under {settings.ldap_base_dn}"
        )
    if len(entries) > 1:
        raise LoginError(f"more than one entry with {attr}={login} under {settings.ldap_base_dn}")
    entry = entries[0]
    names = sorted(entry.entry_attributes_as_dict)
    logins = _values(entry, attr)
    emails = _values(entry, settings.ldap_email_attribute) if settings.ldap_email_attribute else []
    display = _values(entry, settings.ldap_name_attribute) if settings.ldap_name_attribute else []
    found = logins[0] if logins else login
    identity = Identity(
        key=f"ldap:{entry.entry_dn.lower()}",
        login=found,
        display_name=display[0] if display else found,
        email=emails[0] if emails else None,
        groups=group_names(_values(entry, "memberOf")),
    )
    return Bound(bind_dn, identity, names)
