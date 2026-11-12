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

/** A profile's months summed to its four quarters, for a round planned by quarter. */
export function quarterly(values: number[]): number[] {
  return [0, 1, 2, 3].map((q) => values.slice(3 * q, 3 * q + 3).reduce((a, b) => a + b, 0))
}

const MONTH_YEAR = new Intl.DateTimeFormat('en', { month: 'long', year: 'numeric', timeZone: 'UTC' })

export function monthName(iso: string): string {
  return MONTH_YEAR.format(new Date(iso))
}
