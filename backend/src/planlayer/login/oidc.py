"""Login through an OpenID Connect provider (ADR 0030): Entra ID, Keycloak, Authentik, Okta, …

The authorization code flow with PKCE. ``start`` sends the browser to the provider with a fresh
state, nonce and code challenge; the provider sends it back to the callback, where ``finish``
exchanges the code at the token endpoint and reads the person from the ID token and the userinfo
endpoint. The client is public, or confidential with a client secret for a provider that
requires one (``PLANLAYER_OIDC_CLIENT_SECRET``, or the one saved on the Settings page).

The ID token's signature is not verified: the token comes straight from the token endpoint over
TLS (certificate verified), in exchange for a code bound to this login by the PKCE verifier, which
OpenID Connect Core 1.0, 3.1.3.7 (6) allows in place of the signature. Its issuer, audience,
expiry and nonce are checked.
"""

import base64
import hashlib
import json
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import Engine, delete, func, insert, select, update

from planlayer.login.identity import Identity, group_names
from planlayer.login.settings import LoginSettings
from planlayer.store.schema import oidc_login

PENDING = timedelta(minutes=10)
TIMEOUT_S = 10
LEEWAY_S = 60  # clock difference accepted between Planlayer and the provider


class LoginError(Exception):
    """A login that did not complete; the message says why, without secrets."""


@dataclass(frozen=True)
class Pending:
    state: str
    nonce: str
    verifier: str
    redirect_uri: str
    test_by: int | None
    settings: LoginSettings | None  # a test's settings
    result: dict[str, Any] | None


def _context(settings: LoginSettings) -> ssl.SSLContext:
    if settings.ca_pem:
        return ssl.create_default_context(cadata=settings.ca_pem)
    return ssl.create_default_context()


def _request(
    settings: LoginSettings,
    url: str,
    *,
    form: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """A GET, or a form POST; the status and the JSON answer ({} when it isn't JSON)."""
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Accept": "application/json", **(headers or {})}
    )
    context = _context(settings) if url.startswith("https://") else None
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=context) as r:
            status, body = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read()
    except (urllib.error.URLError, TimeoutError, ssl.SSLError) as e:
        reason = getattr(e, "reason", e)
        raise LoginError(f"{urllib.parse.urlsplit(url).netloc} not reachable: {reason}") from e
    try:
        doc = json.loads(body)
    except ValueError:
        doc = {}
    return status, doc if isinstance(doc, dict) else {}


def discovery(settings: LoginSettings) -> dict[str, Any]:
    """The provider's discovery document; it must name the configured issuer."""
    issuer = settings.oidc_issuer.rstrip("/")
    url = f"{issuer}/.well-known/openid-configuration"
    status, doc = _request(settings, url)
    if status != 200:
        raise LoginError(f"discovery: HTTP {status} from {url}")
    if str(doc.get("issuer", "")).rstrip("/") != issuer:
        raise LoginError(f"discovery names the issuer {doc.get('issuer')!r}, not {issuer!r}")
    for key in ("authorization_endpoint", "token_endpoint"):
        if not doc.get(key):
            raise LoginError(f"discovery has no {key}")
    return doc


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def start(
    engine: Engine,
    settings: LoginSettings,
    redirect_uri: str,
    *,
    test_by: int | None = None,
) -> str:
    """The provider's login address for a new pending login. ``test_by`` (an admin's user id)
    marks a test of ``settings``, which the callback then uses instead of the ones in force."""
    doc = discovery(settings)
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    with engine.begin() as conn:
        conn.execute(delete(oidc_login).where(oidc_login.c.created_at < func.now() - PENDING))
        conn.execute(
            insert(oidc_login).values(
                state=state,
                nonce=nonce,
                verifier=verifier,
                redirect_uri=redirect_uri,
                test_by=test_by,
                settings=settings.as_dict() if test_by is not None else None,
            )
        )
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri,
        "scope": settings.oidc_scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
    }
    if settings.oidc_hosted_domain:
        # Google offers only that domain's accounts; the ID token's hd claim is still checked.
        params["hd"] = settings.oidc_hosted_domain
    return f"{doc['authorization_endpoint']}?{urllib.parse.urlencode(params)}"


def pending(engine: Engine, state: str) -> Pending | None:
    """The pending login of this state, if it hasn't expired."""
    with engine.connect() as conn:
        row = conn.execute(
            select(oidc_login).where(
                oidc_login.c.state == state, oidc_login.c.created_at > func.now() - PENDING
            )
        ).first()
    if row is None:
        return None
    return Pending(
        state=row.state,
        nonce=row.nonce,
        verifier=row.verifier,
        redirect_uri=row.redirect_uri,
        test_by=row.test_by,
        settings=LoginSettings.of(row.settings) if row.settings is not None else None,
        result=row.result,
    )


def forget(engine: Engine, state: str) -> None:
    with engine.begin() as conn:
        conn.execute(delete(oidc_login).where(oidc_login.c.state == state))


def keep_result(engine: Engine, state: str, result: dict[str, Any]) -> None:
    with engine.begin() as conn:
        conn.execute(update(oidc_login).where(oidc_login.c.state == state).values(result=result))


def _claims(id_token: str) -> dict[str, Any]:
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as e:
        raise LoginError("the ID token is not a JWT") from e
    if not isinstance(claims, dict):
        raise LoginError("the ID token is not a JWT")
    return claims


def check_claims(claims: dict[str, Any], issuer: str, client_id: str, nonce: str) -> None:
    """The ID token's issuer, audience, expiry and nonce (the signature: see the module)."""
    if str(claims.get("iss", "")).rstrip("/") != issuer.rstrip("/"):
        raise LoginError(f"the ID token's issuer is {claims.get('iss')!r}")
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if client_id not in audiences or (len(audiences) > 1 and claims.get("azp") != client_id):
        raise LoginError("the ID token is not for this client")
    exp = claims.get("exp")
    if not isinstance(exp, int | float) or exp < time.time() - LEEWAY_S:
        raise LoginError("the ID token has expired")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise LoginError("the ID token's nonce doesn't match this login")
    if not claims.get("sub"):
        raise LoginError("the ID token has no subject")


def _text(claims: dict[str, Any], name: str) -> str | None:
    value = claims.get(name) if name else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def finish(
    settings: LoginSettings, p: Pending, code: str, secret: str | None
) -> tuple[Identity, list[str]]:
    """Exchange the code of the pending login ``p``, with the client secret if there is one:
    the identity and the claims' names."""
    doc = discovery(settings)
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": p.redirect_uri,
        "code_verifier": p.verifier,
    }
    headers: dict[str, str] = {}
    if secret:
        basic = f"{urllib.parse.quote(settings.oidc_client_id)}:{urllib.parse.quote(secret)}"
        headers["Authorization"] = "Basic " + base64.b64encode(basic.encode()).decode()
    else:
        form["client_id"] = settings.oidc_client_id
    status, tokens = _request(settings, doc["token_endpoint"], form=form, headers=headers)
    if status != 200:
        error = tokens.get("error_description") or tokens.get("error") or ""
        raise LoginError(f"token endpoint: HTTP {status} {str(error)[:300]}".strip())
    id_token = tokens.get("id_token")
    if not isinstance(id_token, str):
        raise LoginError("the token response has no ID token: is openid among the scopes?")
    claims = _claims(id_token)
    check_claims(claims, doc["issuer"], settings.oidc_client_id, p.nonce)
    # The ID token's own hd, before userinfo is merged in: Google sets it for Workspace accounts
    # only, so a personal account with the same e-mail domain doesn't pass.
    if settings.oidc_hosted_domain and (
        str(claims.get("hd", "")).lower() != settings.oidc_hosted_domain.lower()
    ):
        raise LoginError(
            f"the ID token's hosted domain is {claims.get('hd')!r},"
            f" not {settings.oidc_hosted_domain!r}"
        )
    # The userinfo endpoint may carry claims the ID token leaves out (groups, e-mail); it must
    # name the same subject. The ID token's own claims win.
    access_token = tokens.get("access_token")
    if doc.get("userinfo_endpoint") and isinstance(access_token, str):
        status, info = _request(
            settings, doc["userinfo_endpoint"], headers={"Authorization": f"Bearer {access_token}"}
        )
        if status == 200 and info.get("sub") == claims["sub"]:
            claims = {**info, **claims}
    login = _text(claims, settings.oidc_login_claim)
    if login is None:
        raise LoginError(
            f"no {settings.oidc_login_claim!r} claim; the claims are {', '.join(sorted(claims))}"
        )
    raw = claims.get(settings.oidc_groups_claim) if settings.oidc_groups_claim else None
    groups = [str(g) for g in (raw if isinstance(raw, list) else [raw] if raw else [])]
    identity = Identity(
        key=f"oidc:{doc['issuer'].rstrip('/')} {claims['sub']}",
        login=login,
        display_name=_text(claims, settings.oidc_name_claim) or login,
        email=_text(claims, settings.oidc_email_claim),
        email_verified=claims.get("email_verified") is not False,
        groups=group_names(groups),
    )
    return identity, sorted(claims)
