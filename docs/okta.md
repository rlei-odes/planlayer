# Okta

How to let users log in to Planlayer with their Okta accounts. Okta is an OpenID Connect
provider, so this is the general login setup of [login.md](login.md) with Okta's values.

Okta has two kinds of authorization server, and either works:

| | Org authorization server | Custom authorization server |
|---|---|---|
| Issuer | `https://<org>.okta.com` | `https://<org>.okta.com/oauth2/default` |
| Available | in every Okta org | needs API Access Management (included in free developer orgs) |
| Groups claim | set on the app (*Sign On* tab), sent with the `groups` scope | a claim on the server |

This guide uses the org authorization server, which every customer has. The issuer is the org's
own address, without `-admin`: the admin console runs at `https://<org>-admin.okta.com`, the
issuer is `https://<org>.okta.com` (or the org's custom domain, when it has one).

## 1. Create the app integration

In the Okta admin console:

1. *Applications › Applications › Create App Integration*
   - Sign-in method: **OIDC - OpenID Connect**
   - Application type: **Web Application**
2. On the next page:
   - App integration name: `Planlayer`
   - Grant type: *Authorization Code* (the default; nothing else is needed)
   - Sign-in redirect URIs: as shown on Planlayer's *Settings › Login*, e.g.
     `https://plan.example.com/api/auth/oidc/callback`. For the local test installation below:
     `http://localhost:8002/api/auth/oidc/callback` (Okta accepts plain `http` for `localhost`).
   - Sign-out redirect URIs: leave empty.
   - Controlled access: **Limit access to selected groups**, and pick the groups whose members
     may log in (e.g. *Planlayer*). Okta then refuses everyone else before Planlayer is asked.
3. *Save*. On the app's *General* tab, note the **Client ID** and copy the **Client secret**. It
   becomes `PLANLAYER_OIDC_CLIENT_SECRET`.
4. Optional, on the same tab under *Client Credentials › Edit*: tick *Require PKCE as additional
   verification*. Planlayer always sends PKCE.

Users and groups added later are assigned on the app's *Assignments* tab.

## 2. Roles by group

Okta sends group names, so the role fields take names such as `Planlayer Admins`.

1. *Directory › Groups › Add group*, once per Planlayer role you want to give by group, e.g.
   *Planlayer Admins*, *Planlayer Controllers*, *Planlayer Viewers*, *Planlayer HR*. Add the people.
   Assign these groups to the app as well (*Assignments › Assign › Assign to Groups*), or make
   them members of a group that is.
2. *Applications › Planlayer › Sign On › OpenID Connect ID Token › Edit*:
   - Groups claim type: **Filter**
   - Groups claim filter: `groups`, *Starts with*, `Planlayer`

   The filter keeps the claim to the groups Planlayer needs; Okta sends at most 100 groups in it.
   *Matches regex* `.*` sends all of the user's groups.
3. In Planlayer: *Scopes* `openid profile email groups` (the org authorization server sends the
   groups claim only when the `groups` scope is asked for), *Claims › Groups* `groups` (the
   default); the role fields take the group names.

*Test* on the *Settings* page lists what the token carried, and the role fields offer those groups
to pick from. Groups are compared without regard to case.

With a custom authorization server instead, add the claim there: *Security › API › Authorization
Servers › default › Claims › Add Claim*, name `groups`, include in *ID Token* (*Always*), value
type *Groups*, the same filter. The `groups` scope is then not needed.

## 3. Connect Planlayer

On *Settings › Login* (as an admin), choose *OpenID Connect*:

| Field | Value |
|---|---|
| Button | `Okta` (or the company's name for its login) |
| Issuer | `https://<org>.okta.com` |
| Client ID | the app's Client ID |
| Scopes | `openid profile email groups` |
| Claims › Groups | `groups` |
| Allowed group | optional, e.g. `Planlayer`; Okta's assignment already limits who logs in |
| Admin, Controller, … | the group names |

The client secret is not entered on the page: set `PLANLAYER_OIDC_CLIENT_SECRET` in the server's
environment. Then *Test*: you sign in at Okta and come back to the steps: the claims received,
your login (`preferred_username`, your Okta username, usually your e-mail address), e-mail,
groups, the Planlayer roles they give, and the user you would log in as. *Save* after the test
passed.

## A test installation next to the development one

The development compose file has a production-like installation for this, in the `okta`
profile: the app image as shipped, its own database `planlayer_okta` (created once, as a DBA
would with `deploy/sql/install.sql`), no demo login, on http://localhost:8002. Its redirect URI is
`http://localhost:8002/api/auth/oidc/callback`.

A free developer org for trying it out is available at developer.okta.com.

1. Copy the template and fill it in, replacing each `<…>` whole, brackets included:

   ```
   cp deploy/dev/okta.env.example deploy/dev/okta.env
   ```

   `okta.env` is git-ignored. Only the client secret is required; the other values are there to
   copy into the *Settings* page, or to set the login by the environment instead (uncomment
   `PLANLAYER_LOGIN_METHOD=oidc`).
2. Start it:

   ```
   docker compose --profile okta up -d --build app-okta
   ```

3. `docker compose logs app-okta` shows the setup code; `docker compose exec app-okta planlayer
   setup-code` prints a new one (the earlier one stops working). Open http://localhost:8002, enter
   it and create the first admin (a local account).
4. *Settings › Login* as in *3. Connect Planlayer*; *Test*, *Save*; log out and *Sign in with Okta*.

To start over, drop the database and its roles; `okta-db` creates them again on the next start:

```
docker compose stop app-okta
docker compose exec db psql -U postgres -c "DROP DATABASE planlayer_okta" \
  -c "DROP ROLE planlayer_okta" -c "DROP ROLE planlayer_okta_bi"
```
