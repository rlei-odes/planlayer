# 0009: Roles, planning scopes and login sessions

- **Status:** Accepted
- **Closes:** D-21
- **Date:** 2026-11-08

## Context

Planners see and edit only their own area (scope). Owners come with the synced cost centers and plan them by default; the controller can add further grants (T-17). Approvers are assigned to hierarchy nodes (ADR 0008). Enforcement is forced row-level security over a per-transaction context (ADR 0005). Login comes from OIDC and LDAPS later, with a development login first (T-22).

## Decision

### Users and roles

Users live in Planlayer (`app_user`) with a lowercase login, an optional e-mail and three role flags, which combine:

| Role | May |
|---|---|
| **admin** | manage users and roles; later connections and syncs |
| **controller** | set up rounds, run the workflow (open, close, reopen), edit responsibilities; read and write every cost center; approve every cost center |
| **viewer** | read every cost center (e.g. CFO, auditor) |

A built-in **system** user, which can't log in, is the actor of syncs and other jobs; it acts as controller.

### Planning and approval rights come from data

- **Owner:** the synced owner value is matched case-insensitively to an active user's e-mail or login. The controller can override the match on the responsibilities page: another user, or nobody.
- **Deputies:** the controller grants planning rights to a user on a cost center, or on a node, which covers every cost center below it.
- **Approvers:** assigned to nodes (ADR 0008). An approver reads every cost center below their node, and approves those whose nearest assigned node it is.
- Owners whose value matches no active user are listed on the responsibilities page.

| Action | Who |
|---|---|
| read a cost center | its owner and deputies, approvers above it, controllers, viewers |
| edit, add accounts, submit | its owner and deputies, controllers |
| approve, reject | its nearest approvers and controllers, never someone who plans it |
| reopen | controllers |

Users without any of these rights see nothing.

### Enforcement

- The view `cost_center_access` lists for every user the cost centers they may read and whether they may write them. Forced RLS policies on the fact table filter reads and refuse writes through it, with the user id from the transaction's context.
- Every request, including reads, runs in a unit of work that sets the user. Without a user, no plan values or actuals are visible.
- The API checks the same rights for clear errors and filters lists: a cost center outside the user's scope is reported as not found.

### Sessions

A login creates a random session token, sent as an HttpOnly, SameSite=Lax cookie (Secure in production). The app store keeps only its SHA-256 hash and an expiry (`PLANLAYER_SESSION_HOURS`, default 12). Disabling a user ends their sessions. The development login (`PLANLAYER_DEV_LOGIN`, refused in production) logs in as any active user without a password; OIDC and LDAPS will create the same sessions.

## Reasons

- **Rights from data:** owners already sit in the source, and approvers on nodes; deriving rights from them keeps the user list short and follows reorganisations without extra maintenance.
- **Overrides and deputies:** the source's owner is sometimes wrong, outdated or on leave. The controller fixes that in Planlayer, without waiting for the source.
- **A separate admin:** user administration is IT work; planning administration is the controller's. Some installations give both to one person.
- **The controller reads and writes everything:** corrections happen without asking the owner, and the change log records them. Locks still apply: an approved cost center must be reopened first.
- **Not found instead of forbidden:** a planner can't probe which cost centers exist.
- **Hashed session tokens:** a copy of the app store doesn't contain working sessions.

## Consequences

- New tables: `app_user`, `user_session`, `owner_override`, `planner_grant`; `approver_assignment` refers to users. Views: `cost_center_owner`, `cost_center_access`.
- The responsibilities page (controller) and the users page (admin) edit these. The planning and status pages are offered only to users who read at least one cost center; a user with only the admin role sees the admin pages. `planlayer seed-dev-users` creates the users of the test data (TD-09).
- Database tests connect as an unprivileged role, because a superuser bypasses row-level security.
- Sales planning (later) scopes by region with the same mechanism: a view of readable and writable members per user.
- OIDC and LDAPS (E-09) map an identity to a user by login or e-mail, and may set roles from directory groups.
