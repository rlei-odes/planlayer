# 0029: Pay encrypted at rest

- **Status:** Accepted
- **Closes:** the encryption part of W-18
- **Date:** 2026-11-21

## Context

Row-level security keeps pay to HR and the readers HR names (ADR 0022, ADR 0023). It does not
protect pay from anyone who reads the database past it: a superuser or DBA, anyone with a backup
or dump, a replica, or a reporting tool connected with an owner's role. They see every salary in
plain numbers.

Pay is stored in these columns:

- `hr_pay.monthly_salary`: HR's salary per person, from the source's `staff` dataset or HR's upload
  (ADR 0025);
- `position_pay.hr_salary`, `position_pay.salary`, `position_pay.bonus`: the salary and bonus of a
  position in a round;
- `position_pay_log.old` and `.new`: whole `position_pay` rows as JSON, written by the audit
  trigger.

Personnel costs are calculated in Python (`domain/personnel`, `personnel/costs.py`); the database
only stores pay. In SQL, pay appears in check constraints (`>= 0`) and in the lock trigger on
`position_pay`, which compares the old and new `hr_salary`. Totals on the personnel accounts of
`fact_cost_center` and in `planlayer_out` are part of the plan and stay readable; small groups
already keep them from revealing a salary (ADR 0022).

Planlayer is open source, so any scheme is public. Only a secret per installation, a key, can keep
pay unreadable.

## Decision

### Encrypted in the app

- The pay columns above are `bytea` and hold sealed values. The app seals before writing and
  opens after reading, in Python, with AES-256-GCM (`cryptography`). The database never sees a
  key or a plain salary.
- The columns have SQLAlchemy types (`SealedAmount`, `SealedText` in `planlayer.crypto`) that
  seal what a statement writes and open what it reads, so queries name pay like any column.
  Bulk inserts, which pass arrays past the column types, seal through the same types.
- Each value gets a fresh random 96-bit nonce, so equal salaries look different.
- The associated data is the value's column (`position_pay.salary`): a value copied into another
  column fails to open. It does not name the row, which the column types can't see; someone who
  can write to the database could swap two sealed salaries between rows, but nobody without the
  key can read one.
- A stored value is `version byte ‖ key id ‖ nonce ‖ ciphertext and tag`. Blank pay stays `NULL`
  and needs no key; zero is sealed like any other amount (zero and blank are different values).
- Amounts are sealed as their exact decimal text, so `Decimal` survives unchanged (T-12).

### What moves out of SQL

- The `>= 0` checks on pay are in Python, next to the size check (`sheet.fits`).
- The lock trigger compares the stored `hr_salary` bytes. The app writes HR's salary only when HR
  changes it, never an unchanged value sealed again, so the trigger still sees "unchanged".
- Pay moves between rows only through Python (a new round's positions, a transfer, HR's salaries
  taken into positions): read, opened, sealed for its new column. No pay is copied with
  `INSERT … SELECT` from another column.
- The audit trigger is unchanged: it logs the sealed bytes, as `\x…` text in the JSON.

### The client secret

The OpenID Connect client secret (ADR 0030) is sealed with the same key in
`login_setting.oidc_client_secret`, so an admin can enter it on the Settings page instead of
setting `PLANLAYER_OIDC_CLIENT_SECRET`. The page never shows it again. The environment variable,
when set, is used instead.

### The key

- One key per installation, 256 bits, from the environment variable `PLANLAYER_PAY_KEY`
  (base64), like the other credentials. `planlayer new-pay-key` prints a new one; it is kept
  where only those who may read pay reach it, such as HR's password manager.
- The key id is the first 4 bytes of its SHA-256, so a value sealed with another key is named as
  such rather than failing as damaged.
- Without the key, pay pages, personnel cost calculations and a saved client secret fail with
  HTTP 503 naming the missing key. The rest of the app works, as does an installation that holds
  no pay.
- A lost key means lost pay. The salaries come back from the source's next sync or HR's upload;
  pay typed in Planlayer must be entered again. The installation docs say so.

### Migration

One migration seals the existing pay in place, the pay log's salaries included, and needs the
key when there is any pay; without it, the upgrade refuses and says how to make one. It reads
the pay tables past their forced row-level security (as their owner, for the migration's
transaction) and with their triggers off, so every row is sealed and no lock or audit entry
fires.

## Reasons

- **Against readers of the database:** a DBA, a backup or a dump holds ciphertext and no key. The
  key lives with the app's environment, which those readers don't reach.
- **The key never reaches the database server:** not in statements, the server log or
  `pg_stat_statements`. Encrypting in SQL would put it in all three.
- **It fits where the calculation already is:** pay is calculated in Python, so the database loses
  nothing it computes today; only two kinds of checks move.
- **No arithmetic trick holds up:** a salary scaled or split into factors keeps its ratios to the
  others, and anyone who knows one salary, their own, recovers the rest.

## Consequences

- Someone with both the database and the app's environment can still read pay. Encryption narrows
  the readers to the app's operators; the log of pay rights (ADR 0023) remains the control for
  them.
- Readable as before: the personnel account totals, names and titles in `hr_staff`, positions and
  their FTE. Pay can still be estimated from totals of cost centers just above the small-group
  minimum.
- Pay can't be sorted, filtered or summed in SQL. Every feature that needs it does so in Python
  after decrypting, which the pay pages do today.
- The source's `staff` dataset still holds salaries in the customer's warehouse, outside
  Planlayer's control.
- The salary upload and the source preview handle plain pay in memory only; neither writes it
  anywhere but the encrypted columns.
- Backups hold pay sealed and need the key to be useful for it: the install docs say to keep the
  key apart from them.
- New: the setting `PLANLAYER_PAY_KEY`, the command `new-pay-key`, and a dependency on
  `cryptography`.
- Key rotation is not built: the key id in every value leaves room for a previous key and a
  command that seals everything again with the new one.
