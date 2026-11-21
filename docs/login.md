# Login

How users log in to Planlayer, and how an admin sets it up. The decisions behind it are in
[ADR 0030](adr/0030-login.md).

## Demo and production

| | Demo | Production |
|---|---|---|
| Setting | `PLANLAYER_DEMO=true` | `PLANLAYER_DEMO` unset |
| Login page | Lists the users; anyone logs in as anyone, without a password | Password form, and the provider's button when OpenID Connect is set up |
| Data | `planlayer seed-demo` fills an empty app store from the Nordwerk test warehouse | The company's own |

A demo is never turned into a production installation: production starts from an empty app store.

## First run

While no admin can log in, the server writes a setup code to its log:

```
WARNING:     planlayer.login: No admin can log in yet. Setup code for the first admin account: ABCD-EFGH-JKLM-NPQR
```

(`docker compose logs app` shows it; `docker compose exec app planlayer setup-code` prints a new
one and makes the earlier one invalid.) The login page then asks for the code and the admin's login,
name, e-mail and password (at least 12 characters). The account is a **local account**: its
password is stored as a scrypt hash in the app store. It stays the way in when the login provider
is down or set up wrong.

If no admin can log in later (a lost password), set a local password from the server:

```
docker compose exec app planlayer set-password LOGIN      # asks for it, or reads PLANLAYER_PASSWORD
```

## The login provider

On *Settings › Login* the admin picks **OpenID Connect** or **LDAP**, fills in the fields, and
runs **Test**: a real login with the unsaved settings, shown step by step (the provider's answer,
the claims or attributes, the groups, the roles they give, and who the person would log in as).
Nothing is saved and no session is made. **Save** is offered after a test passed.

After a test passed, *As environment variables* shows the same settings as `PLANLAYER_LOGIN_*`
lines. Set in the deployment's environment (or env file), they replace the page's settings, which
then become read-only. That keeps the login in the deployment's configuration:

```
PLANLAYER_LOGIN_METHOD=oidc
PLANLAYER_LOGIN_OIDC_LABEL=Microsoft
PLANLAYER_LOGIN_OIDC_ISSUER=https://login.microsoftonline.com/<tenant ID>/v2.0
PLANLAYER_LOGIN_OIDC_CLIENT_ID=<application ID>
PLANLAYER_LOGIN_ADMIN_GROUP=<group object ID>
```

Every field has a variable: `PLANLAYER_LOGIN_` and the field's name in capitals (`OIDC_ISSUER`,
`LDAP_BIND_DN`, `CA_PEM` with `\n` for line breaks, `CONTROLLER_GROUP`, …).

### Users and roles

- At someone's first login through the provider, Planlayer looks for a user with the same login,
  then with the same e-mail address. Without one, it creates a user without roles; cost center
  owners named in the source by e-mail then plan their cost centers right away. From then on the
  user is found by the provider's key for them (`sub`, or the directory entry), so a renamed login
  or e-mail doesn't make a new user.
- An e-mail the provider marks as unverified (`email_verified: false`) is neither matched nor kept.
- **Allowed group:** when set, only its members log in.
- **Role groups** (admin, controller, viewer, HR): when one is set, that role comes from the group
  at every login through the provider. Without one, the role is set on the *Users* page. Local
  accounts always keep the roles the *Users* page gives them.
- Groups are compared without regard to case. A Keycloak group path's leading `/` is ignored, and
  an LDAP group matches by its full DN or by its name (`cn=planlayer-admins,ou=groups,dc=…` or
  `planlayer-admins`).

### The address Planlayer is reached at

The OpenID Connect provider sends the browser back to `<address>/api/auth/oidc/callback`; the
*Settings* page shows the exact URI to register. Planlayer takes the address from each request.
Behind a reverse proxy that changes the `Host` header, set it:

```
PLANLAYER_PUBLIC_URL=https://plan.example.com
```

Session cookies are sent with `Secure` outside development, so production is served over HTTPS.

## OpenID Connect

Planlayer runs the authorization code flow with PKCE from the server; the browser never holds a
token. It is a public client by default. A provider that requires a confidential client gets the
secret from `PLANLAYER_OIDC_CLIENT_SECRET` (sent as HTTP Basic). The issuer must use `https://`
(plain `http://` is accepted in development only).

| Field | Default | |
|---|---|---|
| Button | | The provider's name on the login page: "Sign in with …" |
| Issuer | | Discovery is read from `<issuer>/.well-known/openid-configuration` |
| Client ID | | |
| Scopes | `openid profile email` | |
| Claims: login | `preferred_username` | |
| Claims: e-mail | `email` | |
| Claims: name | `name` | |
| Claims: groups | `groups` | Entra ID: `roles` for app roles by name |
| CA certificate | | PEM, for a provider signed by the company's own CA |

### Microsoft Entra ID

The app registration, roles by app role name or by group, and a local test installation:
[entra-id.md](entra-id.md). In short: issuer `https://login.microsoftonline.com/<tenant ID>/v2.0`
(the tenant's own, never `common`), a *Web* redirect URI with a client secret in
`PLANLAYER_OIDC_CLIENT_SECRET`, and *Claims › Groups* `roles` to match app roles by name.

### Okta

The app integration, roles by group name, and a local test installation: [okta.md](okta.md). In
short: a *Web Application* integration with a client secret in `PLANLAYER_OIDC_CLIENT_SECRET`,
issuer `https://<org>.okta.com` (without `-admin`), a groups claim filter on the app's *Sign On*
tab, and scopes `openid profile email groups`.

### Keycloak

A client with *Client authentication* off (public), *Standard flow* on, the redirect URI from the
*Settings* page, and *PKCE method* S256. For role groups, a *Group Membership* mapper on the
client's dedicated scope: claim `groups`, *Full group path* off, added to the ID token and
userinfo. Issuer: `https://<keycloak>/realms/<realm>`.

## LDAP

Planlayer binds as the user, with the DN from the template and their password, and reads their
entry under the base DN with that bind: login, e-mail, name and `memberOf`. No service account is
needed. An empty password is refused before it reaches the directory (LDAP treats it as an
anonymous bind). The server must use `ldaps://` (plain `ldap://` in development only); its
certificate is checked against the system's CAs, or the CA certificate pasted on the page.

| Field | OpenLDAP, lldap | Active Directory |
|---|---|---|
| Server | `ldaps://ldap.example.com:636` | `ldaps://dc01.corp.example.com:636` |
| Bind DN | `uid={login},ou=people,dc=example,dc=com` | `{login}@corp.example.com` |
| Base DN | `dc=example,dc=com` | `DC=corp,DC=example,DC=com` |
| Login attribute | `uid` | `sAMAccountName` |
| E-mail attribute | `mail` | `mail` |
| Name attribute | `cn` (lldap: `displayName`) | `displayName` |

The password form tries a local account of that login first, then the directory.

## The login log

Every attempt (demo, password, OpenID Connect, first run) is written to `login_event` with the
time, the login tried, the outcome, the address and the user agent, and to the server log. After
five failed password logins for one login, or twenty from one address, within fifteen minutes,
further attempts are refused (HTTP 429) until the window has passed.

## Developing against real providers

The development compose file runs Keycloak and lldap in the `auth` profile:

```
docker compose --profile auth up -d keycloak lldap
docker compose exec lldap sh /bootstrap/bootstrap.sh       # the test users, once
```

Both know the users `anna.keller`, `jonas.brandt`, `controller`, `admin` and `outsider`, password
`planlayer-dev`, in the groups `planlayer` (all but `outsider`), `planlayer-admins` and
`planlayer-controllers`.

| | Settings |
|---|---|
| Keycloak | issuer `http://kc.localhost:8080/realms/planlayer`, client ID `planlayer` (admin console: `admin` / `admin`) |
| lldap | server `ldap://localhost:3890` (`ldap://lldap:3890` from the app container), bind DN `uid={login},ou=people,dc=nordwerk,dc=example`, base DN `dc=nordwerk,dc=example`, name attribute `displayName` (web UI on port 17170: `lldap-admin` / `lldap-admin-dev`) |

`kc.localhost` resolves to the loopback address in browsers and on the host, and is the Keycloak
container's alias inside the compose network, so the issuer is the same for both. The directory
tests run against lldap with `PLANLAYER_TEST_LDAP_URL=ldap://localhost:3890 uv run pytest tests/test_login_ldap.py`.
