// Shared planning model for both grid prototypes: one cost center, accounts × months (T-25).
// Amounts are integer cents (BigInt); null = blank, distinct from 0.
// Spreading mirrors planlayer.domain.spreading (largest remainder); here only for the UX test.

import fixture from './fixture.json'

export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
// Columns: 0 label, 1 prior-year total, 2 plan year total, 3..14 months.
export const COL_LABEL = 0
export const COL_PY = 1
export const COL_FY = 2
export const COL_M1 = 3
export const COLUMN_TITLES = ['Account', 'PY actual', 'Plan FY', ...MONTHS]

export type Cents = bigint | null
export type RowKind = 'group' | 'account' | 'percent'

export type Row = {
  kind: RowKind
  key: string
  label: string
  group: string
  locked: boolean
  priorYear: bigint[]
  // percent rows: share of another account
  base?: string
}

type Account = { code: string; name: string; group: string; groupName: string; priorYear: (string | null)[] }

const LOCKED_GROUP = 'DEPRECIATION' // shown as locked (e.g. planned centrally)

export function toCents(text: string | null): bigint {
  if (text === null) return 0n
  const [whole, frac = ''] = text.split('.')
  const sign = whole.startsWith('-') ? -1n : 1n
  return sign * (BigInt(whole.replace('-', '')) * 100n + BigInt((frac + '00').slice(0, 2)))
}

/** Parse what a user types or pastes: 1234.56, 1,234.56, 1.234,56, 1'234.56, -12, (12), 21%. */
export function parseAmount(raw: string): Cents | undefined {
  let s = raw.trim().replace(/\s| /g, '').replace(/[€$]|CHF|EUR/g, '')
  if (s === '') return null
  let sign = 1n
  if (/^\(.*\)$/.test(s)) { sign = -1n; s = s.slice(1, -1) }
  if (s.startsWith('-')) { sign = -sign; s = s.slice(1) }
  s = s.replace(/'/g, '')
  const lastDot = s.lastIndexOf('.')
  const lastComma = s.lastIndexOf(',')
  if (lastDot >= 0 && lastComma >= 0) {
    // Both: the later one is the decimal separator.
    s = lastComma > lastDot ? s.replace(/\./g, '').replace(',', '.') : s.replace(/,/g, '')
  } else if (lastComma >= 0) {
    // Only commas: decimal if one comma with 1-2 digits after it, else thousands.
    const parts = s.split(',')
    s = parts.length === 2 && parts[1].length <= 2 ? `${parts[0]}.${parts[1]}` : parts.join('')
  } else if ((s.match(/\./g) ?? []).length > 1) {
    s = s.replace(/\./g, '') // 1.234.567
  }
  if (!/^\d+(\.\d{1,2})?$/.test(s)) return undefined
  return sign * toCents(s)
}

export function formatCents(c: Cents): string {
  if (c === null) return ''
  const neg = c < 0n
  const abs = neg ? -c : c
  const whole = (abs / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${neg ? '-' : ''}${whole}.${(abs % 100n).toString().padStart(2, '0')}`
}

/** Largest-remainder spread in cents; ties go to the earlier cell. Zero weights stay 0. */
export function spread(total: bigint, weights: bigint[]): bigint[] {
  let w = weights.map((x) => (x < 0n ? 0n : x))
  let sum = w.reduce((a, b) => a + b, 0n)
  if (sum === 0n) { w = weights.map(() => 1n); sum = BigInt(w.length) } // fallback: equal
  const sign = total < 0n ? -1n : 1n
  const n = total < 0n ? -total : total
  const parts = w.map((x) => (n * x) / sum)
  const rema = w.map((x) => (n * x) % sum)
  let left = n - parts.reduce((a, b) => a + b, 0n)
  const order = rema.map((r, i) => [r, i] as const).sort((a, b) => (a[0] === b[0] ? a[1] - b[1] : a[0] > b[0] ? -1 : 1))
  for (const [, i] of order) { if (left === 0n) break; parts[i] += 1n; left -= 1n }
  return parts.map((p) => sign * p)
}

export class PlanModel {
  rows: Row[] = []
  values = new Map<string, Cents>() // `${account}:${month}` -> cents
  rates = new Map<string, number>() // percent rows: rate in %
  version = 0

  constructor(accounts: Account[] = (fixture as { accounts: Account[] }).accounts) {
    let current = ''
    for (const a of accounts) {
      if (a.group !== current) {
        current = a.group
        this.rows.push({ kind: 'group', key: `G:${a.group}`, label: a.groupName, group: a.group,
          locked: a.group === LOCKED_GROUP, priorYear: [] })
      }
      this.rows.push({ kind: 'account', key: a.code, label: `${a.code} ${a.name}`, group: a.group,
        locked: a.group === LOCKED_GROUP, priorYear: a.priorYear.map(toCents) })
    }
    // A percentage line: social security as a share of the first personnel account.
    const firstPersonnel = this.rows.find((r) => r.kind === 'account' && r.group === 'PERSONNEL')
    if (firstPersonnel) {
      const at = this.rows.indexOf(firstPersonnel) + 1
      this.rows.splice(at, 0, { kind: 'percent', key: 'P:social', label: `Social security (% of ${firstPersonnel.key})`,
        group: 'PERSONNEL', locked: false, priorYear: [], base: firstPersonnel.key })
      this.rates.set('P:social', 21)
    }
    for (const r of this.rows) if (r.kind === 'group') r.priorYear = this.groupPriorYear(r.group)
  }

  accountsOf(group: string): Row[] {
    return this.rows.filter((r) => r.kind === 'account' && r.group === group)
  }

  groupPriorYear(group: string): bigint[] {
    return MONTHS.map((_, m) => this.accountsOf(group).reduce((s, r) => s + r.priorYear[m], 0n))
  }

  month(account: string, m: number): Cents {
    return this.values.get(`${account}:${m}`) ?? null
  }

  /** Value of any row in a month (null if blank everywhere it depends on). */
  rowMonth(row: Row, m: number): Cents {
    if (row.kind === 'account') return this.month(row.key, m)
    if (row.kind === 'percent') {
      const base = this.month(row.base!, m)
      if (base === null) return null
      return (base * BigInt(Math.round((this.rates.get(row.key) ?? 0) * 100))) / 10000n
    }
    let any = false
    let sum = 0n
    for (const a of this.accountsOf(row.group)) {
      const v = this.month(a.key, m)
      if (v !== null) { any = true; sum += v }
    }
    return any ? sum : null
  }

  rowYear(row: Row): Cents {
    let any = false
    let sum = 0n
    for (let m = 0; m < 12; m++) {
      const v = this.rowMonth(row, m)
      if (v !== null) { any = true; sum += v }
    }
    return any ? sum : null
  }

  editable(row: Row, col: number): boolean {
    return !row.locked && col >= COL_FY
  }

  /** Display text for a cell. */
  text(row: Row, col: number): string {
    if (col === COL_LABEL) return row.label
    if (col === COL_PY) return row.kind === 'percent' ? '' : formatCents(row.priorYear.reduce((a, b) => a + b, 0n))
    if (row.kind === 'percent') {
      const v = col === COL_FY ? this.rowYear(row) : this.rowMonth(row, col - COL_M1)
      return `${this.rates.get(row.key)}% · ${formatCents(v)}`
    }
    return formatCents(col === COL_FY ? this.rowYear(row) : this.rowMonth(row, col - COL_M1))
  }

  /**
   * Apply one typed or pasted value. Returns an error text or null.
   * - account month: stored as typed (blank clears, 0 is a value)
   * - account FY: spread over months by prior-year actuals
   * - group month: spread over the group's accounts by their prior-year value in that month
   * - group FY: spread over accounts × months by prior-year actuals
   * - percent row: sets the rate for the whole year
   */
  set(row: Row, col: number, raw: string): string | null {
    if (!this.editable(row, col)) return `${row.label} is locked`
    if (row.kind === 'percent') {
      // Accepts "22", "22%", "22,5 %" and the displayed "21% · 1,234.00" (rate before the %).
      const rate = Number(raw.split('%')[0].trim().replace(',', '.'))
      if (!Number.isFinite(rate)) return `not a percentage: ${raw}`
      this.rates.set(row.key, rate)
      this.version++
      return null
    }
    const value = parseAmount(raw)
    if (value === undefined) return `not a number: ${raw}`
    const m = col - COL_M1
    if (row.kind === 'account') {
      if (col === COL_FY) {
        if (value === null) MONTHS.forEach((_, i) => this.values.delete(`${row.key}:${i}`))
        else spread(value, row.priorYear).forEach((v, i) => this.values.set(`${row.key}:${i}`, v))
      } else if (value === null) this.values.delete(`${row.key}:${m}`)
      else this.values.set(`${row.key}:${m}`, value)
    } else {
      const accounts = this.accountsOf(row.group)
      if (value === null) return 'clear the accounts one by one'
      if (col === COL_FY) {
        const cells = accounts.flatMap((a) => MONTHS.map((_, i) => ({ k: `${a.key}:${i}`, w: a.priorYear[i] })))
        spread(value, cells.map((c) => c.w)).forEach((v, i) => this.values.set(cells[i].k, v))
      } else {
        spread(value, accounts.map((a) => a.priorYear[m])).forEach((v, i) => this.values.set(`${accounts[i].key}:${m}`, v))
      }
    }
    this.version++
    return null
  }

  /** Paste a block at (row, col): cells that can't take a value are reported, not silently dropped. */
  paste(startRow: number, startCol: number, block: readonly (readonly string[])[]): string[] {
    const errors: string[] = []
    block.forEach((line, i) => line.forEach((raw, j) => {
      const row = this.rows[startRow + i]
      const col = startCol + j
      if (!row || col > COL_M1 + 11) return
      const err = this.set(row, col, raw)
      if (err) errors.push(`${row.label} / ${COLUMN_TITLES[col]}: ${err}`)
    }))
    return errors
  }
}

/** Stress model: many rows, for the 50k-cell scroll test. */
export function stressModel(rows: number): PlanModel {
  const accounts: Account[] = []
  for (let i = 0; i < rows; i++) {
    accounts.push({ code: String(100000 + i), name: `Line ${i + 1}`, group: `G${Math.floor(i / 50)}`,
      groupName: `Group ${Math.floor(i / 50) + 1}`, priorYear: MONTHS.map((_, m) => String(1000 + ((i * 7 + m * 13) % 900)) + '.00') })
  }
  const model = new PlanModel(accounts)
  model.rows.filter((r) => r.kind === 'account').forEach((r, i) => MONTHS.forEach((_, m) => model.values.set(`${r.key}:${m}`, BigInt(100000 + i * 10 + m))))
  return model
}
