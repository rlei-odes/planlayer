# 0029: Pay encrypted at rest

- **Status:** Proposed
- **Closes:** the encryption part of W-18
- **Date:** 2026-11-19

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

- The pay columns above become `bytea` and hold ciphertext. The app encrypts before writing and
  decrypts after reading, in Python, with AES-256-GCM (`cryptography`). The database never sees
  a key or a plain salary.
- Each value is encrypted with a fresh random 96-bit nonce, so equal salaries give different
  ciphertexts.
- The associated data binds a value to its place: table, column and the row's key
  (`hr_pay.code`; `position_pay` version, position and module). A ciphertext copied into another
  row or column fails to decrypt.
- A stored value is `version byte ‖ key id ‖ nonce ‖ ciphertext and tag`. Blank pay stays `NULL`;
  zero is encrypted like any other amount (zero and blank are different values).
- Amounts are encrypted as their exact decimal text, so `Decimal` survives unchanged (T-12).

### What moves out of SQL

- The `>= 0` checks on pay move to Python, next to the existing size check (`sheet.fits`).
- The lock trigger compares the stored `hr_salary` bytes. The app writes an unchanged value back
  as the bytes it read, never re-encrypted, so the trigger still sees "unchanged".
- Copying pay between rounds, or any statement that moves pay from one row to another, decrypts
  and re-encrypts in Python, since the associated data names the row. No pay is copied with
  `INSERT … SELECT`.
- The audit trigger is unchanged: it logs the ciphertext, and reading the log decrypts it.

### The key

- One key per installation, 256 bits, from the environment variable `PLANLAYER_PAY_KEY`
  (base64), like the other credentials. The guided installation (E-11) generates it and shows it
  once, for HR to keep in a password manager.
- The key id is the first 4 bytes of its SHA-256. `PLANLAYER_PAY_KEY_PREVIOUS` holds the old key
  during a rotation; `planlayer rotate-pay-key` re-encrypts every value with the current key in
  one transaction, and the previous key can then be removed.
- Without the key, or with a key whose id no stored value carries, pay pages and personnel cost
  calculations fail with an error naming the missing key. The rest of the app works.
- A lost key means lost pay. The salaries come back from the source's next sync or HR's upload;
  pay typed in Planlayer must be entered again. The installation docs say so.

### Migration

- A migration adds the `bytea` columns. `planlayer encrypt-pay` (needs the key) encrypts the
  existing values and empties the numeric columns; a later migration drops them. Until then the
  app reads ciphertext where present and the numeric column otherwise.

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
- Backups need the key to be useful for pay: the backup docs (D-30) say where it is kept.
- New: the setting `PLANLAYER_PAY_KEY` (and `_PREVIOUS`), the commands `encrypt-pay` and
  `rotate-pay-key`, and a dependency on `cryptography`.
