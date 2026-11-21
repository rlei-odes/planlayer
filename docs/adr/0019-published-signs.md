# 0019: Published signs and account roles

- **Status:** Accepted
- **Closes:** the export side of the sign rules (E-12, T-7)
- **Date:** 2026-11-13

## Context

Plan values are stored natural-positive (T-7): costs, revenue and credits are all positive, and the account says what a value means. That is right for entry, but a BI tool reading `planlayer_out` can't net a cost center's costs without knowing which accounts are credits: activity credits (ADR 0013) and forwarding credits (ADR 0018) are typed `cost` like any other account. Warehouses expect signs in one of two ways: as the ERP books (debit positive) or as P&L reports read (profit positive).

## Decision

### Each account has a role

- **credit:** an account credited by capacity planning (an activity type's account) or by a forwarding of any round (its credit account), whatever its type;
- **revenue:** an account typed `revenue`;
- **cost:** every other account, those without a type included.

The role is published on leaf accounts in `dim_account.role` and in `v_plan_cost_center`.

### One convention per installation, for `amount_signed`

| Convention | cost | revenue | credit |
|---|---|---|---|
| `ledger` (default): debit positive, as the ERP books | + | − | − |
| `result`: profit positive, as P&L reports read | − | + | + |

- `fact_plan_cost_center.amount` stays natural-positive; `amount_signed` carries the convention. The file downloads have both.
- The admin chooses the convention on the *Settings* page, which shows each role's sign, the credit accounts, and the accounts without a type.
- A new convention applies from the next publish run. The preview compares `amount_signed` too, so it lists the rows a changed convention rewrites.
- Planned hours (`fact_plan_capacity`) carry no sign.

## Reasons

- **Both columns:** existing reports keep reading `amount` unchanged; new ones read `amount_signed` and sum directly.
- **Roles over per-account rules:** the two conventions follow from three roles, which Planlayer knows; nobody maintains a sign per account. Overrides per account group or account stay possible later (E-12) without changing the published columns.
- **Credits by use, not by type:** capacity and forwarding decide which accounts carry credits; a source's account type can't say so.

## Consequences

- `installation.published_signs`; `amount_signed` and `dim_account.role` in the output, added to existing output tables when a run finds them missing.
- Not in this decision: converting signs of the source at sync (actuals are read natural); per-group or per-account overrides; signing hours. They stay under E-12.
