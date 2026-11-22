# Changelog

## [0.5.2](https://github.com/rlei-odes/planlayer/compare/v0.5.1...v0.5.2) (2026-11-22)


### Bug Fixes

* **backend:** HR saves personnel rules in a round carrying values on cost centers it doesn't plan ([b9f2f13](https://github.com/rlei-odes/planlayer/commit/b9f2f132e1adb29396124ac3babc31a9b8f414bb))
* **frontend:** a new budget round's code and name follow its fiscal year until edited ([215df02](https://github.com/rlei-odes/planlayer/commit/215df02ed52615748d19a80b56f07bcb46471e3d))
* **frontend:** a planned switch on each cost center level in round setup turns all cost centers below it on or off ([4ead311](https://github.com/rlei-odes/planlayer/commit/4ead31114588a742cfb64f8bbbe2b572cfd3c29f))
* **frontend:** a refused round setup save shows its reason in the save bar, where it is on screen ([c3ed35d](https://github.com/rlei-odes/planlayer/commit/c3ed35d35bc072f5729693d17604f79699201e9f))
* **frontend:** capacity switches in round setup work per cost center level and only for planned cost centers ([eeca8e9](https://github.com/rlei-odes/planlayer/commit/eeca8e9a6df751b05a5a78318afad59b2af71932))
* **frontend:** capacity's "Add work center" offers a work center not on the sheet yet, else a new one ([f46f45c](https://github.com/rlei-odes/planlayer/commit/f46f45c5532945f50f46af4eae89aa1c39dcf566))
* **frontend:** new positions and workforce lines start with the cost center's usual staff type, else Employee ([7e3fccd](https://github.com/rlei-odes/planlayer/commit/7e3fccdaf1208fa67e4c327d0a026ddfceb03254))
* **frontend:** workforce capacity in round setup needs the round's hours per FTE, and the error says where to set them ([5818be3](https://github.com/rlei-odes/planlayer/commit/5818be324f0eabe9cb899e3600b4c93185273ede))
* workforce hours no longer replace a line's work center hours, and positions whose hours have no line are marked ([0f979ee](https://github.com/rlei-odes/planlayer/commit/0f979ee7e44e456869d44f5bc76535b0a3db1fae))

## [0.5.1](https://github.com/rlei-odes/planlayer/compare/v0.5.0...v0.5.1) (2026-11-22)


### Bug Fixes

* **backend:** demo logins work over plain HTTP from other machines - production deployments stay as they are ([7ed80af](https://github.com/rlei-odes/planlayer/commit/7ed80af75c593c0e15ce15f691a8311d476b73b6))
* **frontend:** long account names on the cost center sheet show in full on hover ([72f0be1](https://github.com/rlei-odes/planlayer/commit/72f0be1e219fe49dfbd8f58f9113c95f1b3aca03))
* **frontend:** master data's Members tab is now Records and comes first; Home's "Notes I started" is now "My notes" ([e413df0](https://github.com/rlei-odes/planlayer/commit/e413df04aafa1a0f31b4b729c8bb58a19fdec4bb))
* **frontend:** the app opens on Home after every login ([88bcee7](https://github.com/rlei-odes/planlayer/commit/88bcee745d25bd2f44eac05d7b8981c07f31347a))
* **frontend:** the capex list's cash-out pills are neutral, so the status beside them stands out ([633e6c4](https://github.com/rlei-odes/planlayer/commit/633e6c4b127b5d54f83f09588b28f03f6065daa9))
* **frontend:** the source check on Master data shows when it ran, without blanking the status ([b9f659d](https://github.com/rlei-odes/planlayer/commit/b9f659d85cc70ed5a1e37e0ac0f021c0cb3d907c))
* **frontend:** the update notice and the unsaved-changes bar are readable in dark mode ([7b1652b](https://github.com/rlei-odes/planlayer/commit/7b1652b4bcb150e5258a5c51e7740b112dd36d70))

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
