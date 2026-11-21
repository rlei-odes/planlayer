# Google Workspace

How to let users log in to Planlayer with their Google Workspace accounts. Google is an OpenID
Connect provider, so this is the general login setup of [login.md](login.md) with Google's
values.

Two things differ from other providers:

- **One issuer for every Google account.** `https://accounts.google.com` signs in Workspace and
  personal accounts alike. Planlayer's *Hosted domain* limits the login to one Workspace: Google
  sets the `hd` claim only for accounts of a Workspace (or Cloud Identity) organisation, and
  Planlayer refuses an ID token whose `hd` is not that domain. A personal Google account
  registered with a company e-mail address carries no `hd`, so it is refused too. The settings
  are not saved with Google's issuer and no hosted domain.
- **No groups.** Google's ID token carries no group memberships. *Allowed group* and the role
  groups stay empty; roles are given on the *Users* page. Who may log in at all is decided by the
  hosted domain and, on Google's side, by the app's audience being *Internal*.

## 1. Create the OAuth client

In the Google Cloud console (console.cloud.google.com), signed in as an administrator of the
Workspace:

1. Create a project, e.g. `Planlayer`, inside the Workspace's organisation (not under *No
   organisation*; only then can the app be *Internal*).
2. *Google Auth Platform › Branding*: app name `Planlayer`, a support e-mail address.
3. *Google Auth Platform › Audience*: user type **Internal**. Google then offers the login only to
   the organisation's users, before Planlayer is asked.
4. *Google Auth Platform › Clients › Create client*:
   - Application type: **Web application**
   - Name: `Planlayer`
   - Authorized redirect URIs: as shown on Planlayer's *Settings › Login*, e.g.
     `https://plan.example.com/api/auth/oidc/callback`. For the local test installation below:
     `http://localhost:8003/api/auth/oidc/callback` (Google accepts plain `http` for
     `localhost`).
5. Note the **Client ID** and copy the **Client secret** at once (Google shows it only when the
   client is created; a lost one is replaced by adding a new secret). It goes in
   Planlayer's *Client secret* field (step 2).

The scopes Planlayer asks for (`openid profile email`) need no entry under *Data access* and no
verification by Google.

## 2. Connect Planlayer

On *Settings › Login* (as an admin), choose *OpenID Connect*:

| Field | Value |
|---|---|
| Button | `Google` |
| Issuer | `https://accounts.google.com` |
| Client ID | the client's ID (`….apps.googleusercontent.com`) |
| Hosted domain | the Workspace's domain, e.g. `corp.example` |
| Claims › Login | `email` (Google sends no `preferred_username`) |
| Groups | all empty |

The client secret goes in the *Client secret* field (saved encrypted with `PLANLAYER_PAY_KEY`, never
shown again), or in `PLANLAYER_OIDC_CLIENT_SECRET` in the server's environment, which wins. Then
*Test*: you sign in at Google and come back to the steps: the claims received (`hd` among them),
your login (your e-mail address), the user you would log in as. *Save* after the test passed.

The hosted domain is also sent to Google with the login (`hd=`), so Google offers only that
domain's accounts. One domain is checked. In a Workspace with several domains, *Test* with an
account of each shows the `hd` they carry.

## 3. Roles

New users are created at their first login without roles. Users the source names as cost center
owners by e-mail plan their cost centers right away; everyone else gets their roles from an admin
on the *Users* page. Local accounts, including the first admin, keep working next to Google.

## A test installation next to the development one

The development compose file has a production-like installation for this, in the `google`
profile: the app image as shipped, its own database `planlayer_google` (created once, as a DBA
would with `deploy/sql/install.sql`), no demo login, on http://localhost:8003. Its redirect URI
is `http://localhost:8003/api/auth/oidc/callback`.

1. Copy the template and fill it in, replacing each `<…>` whole, brackets included:

   ```
   cp deploy/dev/google.env.example deploy/dev/google.env
   ```

   `google.env` is git-ignored. Only the client secret is required; the other values are there
   to copy into the *Settings* page, or to set the login by the environment instead (uncomment
   `PLANLAYER_LOGIN_METHOD=oidc`).
2. Start it:

   ```
   docker compose --profile google up -d --build app-google
   ```

3. `docker compose logs app-google` shows the setup code; `docker compose exec app-google
   planlayer setup-code` prints a new one (the earlier one stops working). Open
   http://localhost:8003, enter it and create the first admin (a local account).
4. *Settings › Login* as in *2. Connect Planlayer*; *Test*, *Save*; log out and *Sign in with
   Google*.

To start over, drop the database and its roles; `google-db` creates them again on the next start:

```
docker compose stop app-google
docker compose exec db psql -U postgres -c "DROP DATABASE planlayer_google" \
  -c "DROP ROLE planlayer_google" -c "DROP ROLE planlayer_google_bi"
```
