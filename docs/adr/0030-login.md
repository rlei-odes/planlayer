# 0030: Login

- **Status:** Accepted
- **Closes:** the login part of E-09 (T-22)
- **Date:** 2026-11-20

## Context

Users, roles and sessions exist (ADR 0009); the only way in was the development login, which
lists every user without a password. An installation needs a real login, set up by an admin who
may not have a shell on the server, and a demo that anyone can try without one.

## Decision

### Two kinds of installation

- **Demo** (`PLANLAYER_DEMO=true`): the login page lists the active users and logs in as any of
  them without a password, on a banner saying so. `planlayer seed-demo` fills an empty app store
  from the Nordwerk test data. A demo is never turned into a production installation; production
  starts from an empty app store.
- **Production** (`PLANLAYER_DEMO` unset): users log in with a password (a local account or the
  directory over LDAPS) or through an OpenID Connect provider.

### First run

When no active admin has a password and no provider is set up, the app creates a setup code,
stores its hash and writes the code to the server log; each start of the server, and
`planlayer setup-code`, issues a new one in its place. The login page then asks for the code and
an admin account (login, name, e-mail, password). Without the code, whoever opened a newly
started server first would become its admin. The account is a **local account**: its password
hash (scrypt) is in `app_user`. It stays as the way in when the provider is down or set up wrong;
`planlayer set-password LOGIN` sets a local password from the command line.

### The provider, on the Settings page

The admin chooses the method (none, OpenID Connect or LDAP) and its fields on the Settings page,
stored in `login_setting`. A **test** runs a real login with the unsaved settings and reports each
step (discovery, the provider's answer, the claims or attributes found, the groups, the roles they
give) without creating a session, so a wrong setting is seen before anyone is locked out.

No secret is stored there:

- OpenID Connect runs the authorization code flow with PKCE as a public client. A provider that
  insists on a confidential client gets its secret from `PLANLAYER_OIDC_CLIENT_SECRET`.
- LDAP binds as the user logging in, with their own password, and reads their entry with that
  bind; no service account.

### OpenID Connect

- The backend sends the browser to the provider with a fresh state, nonce and PKCE challenge,
  kept in `oidc_login` for ten minutes, and exchanges the code at the token endpoint on the
  callback (`/api/auth/oidc/callback`, under `PLANLAYER_PUBLIC_URL` or the request's own address).
  The browser never holds a token.
- The ID token's issuer, audience, expiry and nonce are checked; its signature is not. The token
  comes straight from the token endpoint over TLS with a verified certificate, in exchange for a
  code bound to this login by the PKCE verifier, which OpenID Connect Core 1.0, 3.1.3.7 (6) allows
  in place of the signature. This keeps a JWT library out. The issuer must be `https`, except in
  development.
- The issuer is a single tenant's (for Entra ID, `https://login.microsoftonline.com/<tenant>/v2.0`).
  A multi-tenant issuer would let any tenant's users claim any e-mail address. Google has only
  one issuer for all its accounts; there the hosted domain setting stands in for the tenant: the
  ID token's `hd` claim must name the Workspace's domain, and the settings are refused without it.
- Claims are configurable: login (default `preferred_username`), e-mail (`email`), name (`name`),
  groups (`groups`). The userinfo endpoint's claims are added when it names the same subject.

### LDAP

- `ldaps://`, or `ldap://` in development only. A CA certificate (PEM) can be pasted for a
  directory signed by the company's own CA.
- The bind DN comes from a template (`uid={login},ou=people,dc=example,dc=com`, or
  `{login}@corp.example.com` for Active Directory), the login escaped for a DN. An empty password
  is refused before the bind, since LDAP treats it as an anonymous bind that succeeds.
- After the bind, the entry is searched under the base DN by the login attribute (escaped for a
  filter); its e-mail, name and `memberOf` are read.

### From identity to user

- An identity is the provider and its stable key (the issuer and `sub`, or the entry's DN). A user
  who logged in once keeps it in `app_user.identity` and is found by it from then on, so a renamed
  login or e-mail doesn't make a new user.
- At a first login the identity is matched to an active user by login, then by e-mail
  (case-insensitive); an e-mail the provider marks as unverified (`email_verified: false`) isn't
  matched. Matching two different users refuses the login. Without a match, a user without roles
  is created: owners matched from the source by e-mail then plan their cost centers without an
  admin creating them first.
- **Allowed group:** when set, only its members get in.
- **Role groups:** per role (admin, controller, viewer, HR) an optional group. When one is set,
  that role is taken from the group at every login through the provider, and the Users page shows
  it as set by the directory. Local accounts keep the roles set on the Users page, so a group
  mistake can't remove the local admin.
- Groups are compared case-insensitively; a Keycloak group path's leading `/` is dropped, and an
  LDAP group matches by its full DN or its first RDN value (`cn=planlayer-admins,…` or
  `planlayer-admins`).

### Logins are logged and limited

Every attempt is written to `login_event` (time, login tried, method, outcome, address, user
agent) and to the server log. After five failed password logins for one login, or twenty from one
address, within fifteen minutes, further attempts are refused with 429 until the window passes.

## Consequences

- An installation is set up from the browser: the setup code from the log, the admin account,
  then the provider on the Settings page.
- Microsoft Entra ID, Keycloak, Authentik, Okta and other OpenID Connect providers use the same
  code; Active Directory, OpenLDAP and lldap use the LDAP path. The development compose file
  runs Keycloak and lldap in the `auth` profile to test against.
- New tables: `login_setting`, `oidc_login`, `login_event`; `app_user` gains `password_hash` and
  `identity`. `PLANLAYER_DEV_LOGIN` becomes `PLANLAYER_DEMO`.
- Open in E-09: the login log on the Users page; a password reset for local accounts in the UI;
  Entra ID's group overage (more than 200 groups sends a link instead of the list).
