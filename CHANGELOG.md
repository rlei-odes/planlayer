# Changelog

## [0.5.0](https://github.com/rlei-odes/planlayer/releases/tag/v0.5.0) (2026-11-21)

The first release of Planlayer: budgeting and forecasting on your own Postgres.

### Features

- Cost center planning in an Excel-like grid: paste, fill-drag, undo, spreading, comparison columns with actuals
- Planning rounds: budgets by month or year, forecasts by quarter, set up with a live preview; carried over from earlier rounds
- Submit, approve and lock per cost center, enforced by the database; a status page per hierarchy node
- Modules: capacity, cost forwarding, workforce and personnel costs (pay encrypted at rest), capex
- Notes with mentions and tasks; item lists
- Sync from Postgres views with a preview; publishing to `planlayer_out` and as CSV or Parquet
- Login with OpenID Connect (Entra ID, Okta, Google Workspace, Keycloak) or LDAP
- One signed container image for amd64 and arm64, built-in backups, migrations with a backup first
