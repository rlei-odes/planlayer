// Fiscal months for the setup page (T-18, ADR 0015): periods are first-of-month ISO dates.

export type Grain = 'month' | 'quarter'

/** The first day of each of the fiscal year's twelve months, as ISO dates. */
export function fiscalPeriods(year: number, startMonth: number): string[] {
  return Array.from({ length: 12 }, (_, i) => {
    const index = year * 12 + startMonth - 1 + i
    const month = String((index % 12) + 1).padStart(2, '0')
    return `${Math.floor(index / 12)}-${month}-01`
  })
}

/** How many of the year's months a forecast's actuals through closes. */
export function closedCount(periods: string[], through: string | null | undefined): number {
  return through ? periods.filter((p) => p <= through).length : 0
}

/** The months a forecast can close: any but the last, or quarter ends when planned by quarter. */
export function closable(periods: string[], grain: Grain): string[] {
  return periods.slice(0, -1).filter((_, i) => grain === 'month' || i % 3 === 2)
}

/** Actuals through that fits the grain: by quarter, the latest quarter end at or before it. */
export function fitThrough(periods: string[], grain: Grain, through: string | null): string | null {
  if (!through) return null
  return closable(periods, grain).filter((p) => p <= through).at(-1) ?? null
}

/** How a sheet's period columns are laid out: by quarter or by month, and the months closed. */
export type Layout = { quarters: boolean; closed: number }

export const monthProp = (m: number) => `m${m}`
export const quarterProp = (q: number) => `q${q}`

/** The months a period column covers: one month, or a quarter's three; null for other columns. */
export function monthsOf(prop: string): number[] | null {
  const n = Number(prop.slice(1))
  if (/^m\d+$/.test(prop)) return [n]
  if (/^q\d+$/.test(prop)) return [3 * n, 3 * n + 1, 3 * n + 2]
  return null
}

/** A forecast's closed months hold actuals (ADR 0015). */
export function isClosed(prop: string, layout: Layout): boolean {
  const months = monthsOf(prop)
  return months !== null && months[0] < layout.closed
}

const MONTH_SHORT = new Intl.DateTimeFormat('en', { month: 'short', timeZone: 'UTC' })
const MONTH_YEAR_SHORT = new Intl.DateTimeFormat('en', { month: 'short', year: 'numeric', timeZone: 'UTC' })

/** "Jan–Jun 2026": a run of the year's months. */
export function span(periods: string[], from: number, to: number): string {
  const first = MONTH_SHORT.format(new Date(periods[from]))
  const last = MONTH_YEAR_SHORT.format(new Date(periods[to]))
  return from === to ? last : `${first}–${last}`
}

/** The period columns' labels: months or quarters, closed ones marked "Act". */
export function periodColumns(periods: string[], layout: Layout): { prop: string; label: string; closed: boolean }[] {
  const columns = layout.quarters
    ? [0, 1, 2, 3].map((q) => ({ prop: quarterProp(q), label: `Q${q + 1}` }))
    : periods.map((p, m) => ({ prop: monthProp(m), label: MONTH_SHORT.format(new Date(p)) }))
  return columns.map((c) => {
    const closed = isClosed(c.prop, layout)
    return { ...c, label: closed ? `${c.label} · Act` : c.label, closed }
  })
}

/** A row's values per period column: months as they are, or summed to quarters. */
export function byColumn<T>(values: T[], layout: Layout, total: (xs: T[]) => T): Record<string, T> {
  const out: Record<string, T> = {}
  if (layout.quarters) for (let q = 0; q < 4; q++) out[quarterProp(q)] = total(values.slice(3 * q, 3 * q + 3))
  else values.forEach((v, m) => (out[monthProp(m)] = v))
  return out
}

/** A profile's months summed to its four quarters, for a round planned by quarter. */
export function quarterly(values: number[]): number[] {
  return [0, 1, 2, 3].map((q) => values.slice(3 * q, 3 * q + 3).reduce((a, b) => a + b, 0))
}

const MONTH_YEAR = new Intl.DateTimeFormat('en', { month: 'long', year: 'numeric', timeZone: 'UTC' })

export function monthName(iso: string): string {
  return MONTH_YEAR.format(new Date(iso))
}
