# Microsoft Entra ID

How to let users log in to Planlayer with their Microsoft work accounts. Entra ID is an OpenID
Connect provider, so this is the general login setup of [login.md](login.md) with Entra's
values.

## 1. Register the app

In the Entra admin center (entra.microsoft.com):

1. *Applications › App registrations › New registration*
   - Name: `Planlayer`
   - Supported account types: *Accounts in this organizational directory only* (single tenant)
   - Redirect URI: platform **Web**, value as shown on Planlayer's *Settings › Login*, e.g.
     `https://plan.example.com/api/auth/oidc/callback`. For the local test installation below:
     `http://localhost:8001/api/auth/oidc/callback` (Entra accepts plain `http` for `localhost`).
2. On the app's *Overview*, note the **Application (client) ID** and the **Directory (tenant) ID**.
3. *Certificates & secrets › Client secrets › New client secret*. Copy its **Value** at once (it is
   shown only once; the *Secret ID* is something else). It becomes `PLANLAYER_OIDC_CLIENT_SECRET`.
   Note the expiry date: Planlayer's Microsoft login stops working when the secret expires.
4. *Token configuration › Add optional claim › ID › email*, and allow the Microsoft Graph email
   permission when asked.
5. *API permissions › Grant admin consent for <tenant>*. Without it, Microsoft asks the first
   user who logs in to consent (an admin can consent for the whole organisation there).

## 2. Roles: by name or by group

Planlayer gives roles (admin, controller, viewer, HR) from what the ID token says about the user.
Entra can say it in two ways.

### By name, with app roles

Entra puts the names of the user's **app roles** in the `roles` claim. Group memberships it only
sends as object IDs (names only for groups synced from an on-premises Active Directory), so app
roles are how a role is matched by a name.

1. *App registrations › Planlayer › App roles › Create app role*, once per Planlayer role you
   want to give this way:

   | Display name | Allowed member types | Value |
   |---|---|---|
   | Planlayer Admin | Users/Groups | `Planlayer.Admin` |
   | Planlayer Controller | Users/Groups | `Planlayer.Controller` |
   | Planlayer Viewer | Users/Groups | `Planlayer.Viewer` |
   | Planlayer HR | Users/Groups | `Planlayer.HR` |

2. *Enterprise applications › Planlayer › Users and groups › Add user/group*: pick a group (e.g.
   *Planlayer Admins*) or a user, and the app role it gets. Assigning **groups** needs Entra ID P1
   or P2; on the free tier, assign users.
3. Optional: *Enterprise applications › Planlayer › Properties › Assignment required: Yes*. Then
   only users and groups assigned in step 2 can log in at all, which Entra enforces before
   Planlayer is asked.
4. In Planlayer: *Claims › Groups* `roles`; the role fields take the values
   (`Planlayer.Admin`, `Planlayer.Controller`, …).

App roles also avoid Entra's group limit: a user in more than 200 groups gets no `groups` claim,
but their app roles still arrive.

### By group, with object IDs

1. *App registrations › Planlayer › Token configuration › Add groups claim › Security groups*;
   under *ID*, *Group ID*.
2. In Planlayer: *Claims › Groups* `groups` (the default); the role fields take each group's
   **Object ID** (*Groups › the group › Overview*).

In both ways, *Test* on the *Settings* page lists what the token carried, and the role fields
offer those values to pick from.

## 3. Connect Planlayer

On *Settings › Login* (as an admin), choose *OpenID Connect*:

| Field | Value |
|---|---|
| Button | `Microsoft` |
| Issuer | `https://login.microsoftonline.com/<Directory (tenant) ID>/v2.0` |
| Client ID | the Application (client) ID |
| Claims › Groups | `roles` (app roles) or `groups` (object IDs) |
| Admin, Controller, … | the app role values or the groups' object IDs |

The client secret is not entered on the page: set `PLANLAYER_OIDC_CLIENT_SECRET` in the server's
environment. Then *Test*: you sign in at Microsoft and come back to the steps: the claims
received, your login (`preferred_username`, your UPN), e-mail, groups or roles, the Planlayer roles
they give, and the user you would log in as. *Save* after the test passed.

The issuer must be the tenant's own. `common` or `organizations` would let users of any tenant
log in and claim any e-mail address.

Users without a mailbox may have no `email` claim. They are then matched and created by their UPN
alone, and an owner the source names by e-mail isn't matched to them until the e-mail is set on
the *Users* page.

## 4. A tile in My Apps

Users can open Planlayer from Microsoft's My Apps portal (myapps.microsoft.com) with one click:
*App registrations › Planlayer › Branding & properties › Home page URL*
`https://plan.example.com/api/auth/oidc/login`. The tile opens Planlayer's login address;
Planlayer starts a login at Microsoft, which finds the user's session and sends them straight
back, logged in. With *Assignment required*, only assigned users see the tile.

## A test installation next to the development one

The development compose file has a production-like installation for this, in the `entra`
profile: the app image as shipped, its own database `planlayer_entra` (created once, as a DBA
would with `deploy/sql/install.sql`), no demo login, on http://localhost:8001. Its redirect URI is
`http://localhost:8001/api/auth/oidc/callback`.

1. Copy the template and fill it in, replacing each `<…>` whole, brackets included:

   ```
   cp deploy/dev/entra.env.example deploy/dev/entra.env
   ```

   `entra.env` is git-ignored. Only the client secret is required; the other values are there to
   copy into the *Settings* page, or to set the login by the environment instead (uncomment
   `PLANLAYER_LOGIN_METHOD=oidc`).
2. Start it:

   ```
   docker compose --profile entra up -d --build app-entra
   ```

3. `docker compose logs app-entra` shows the setup code; `docker compose exec app-entra planlayer
   setup-code` prints a new one (the earlier one stops working). Open http://localhost:8001, enter
   it and create the first admin (a local account).
4. *Settings › Login* as in *3. Connect Planlayer*; *Test*, *Save*; log out and *Sign in with Microsoft*.

To start over, drop the database and its roles; `entra-db` creates them again on the next start:

```
docker compose stop app-entra
docker compose exec db psql -U postgres -c "DROP DATABASE planlayer_entra" \
  -c "DROP ROLE planlayer_entra" -c "DROP ROLE planlayer_entra_bi"
```
