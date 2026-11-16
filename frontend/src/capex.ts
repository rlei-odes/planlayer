// Capex planning's helpers (ADR 0028): months as planners type them, labels, sums.

import { toCents } from './amounts'
import type { CapexItem, CapexStatus } from './api'

const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
const SHORT = new Intl.DateTimeFormat('en', { month: 'short', year: 'numeric', timeZone: 'UTC' })

export const STATUS_TEXT: Record<CapexStatus, string> = {
  open: 'Open',
  confirmed: 'Confirmed',
  declined: 'Declined',
}

/** "2026-03-01" -> "Mar 2026"; blank stays blank. */
export function monthLabel(iso: string | null | undefined): string {
  return iso ? SHORT.format(new Date(iso)) : ''
}

function iso(year: number, month: number): string | undefined {
  if (month < 1 || month > 12 || year < 1900 || year > 2200) return undefined
  return `${year}-${String(month).padStart(2, '0')}-01`
}

/**
 * A month as typed or pasted, to the first of the month: 2026-03, 2026-03-15, 03.2026, 3/2026,
 * 3/26, 15.03.2026, Mar 2026, March 26. Null for blank, undefined when it isn't a month.
 */
export function parseMonth(raw: string): string | null | undefined {
  const s = raw.trim().toLowerCase()
  if (s === '') return null
  const year = (y: string) => (y.length === 2 ? 2000 + Number(y) : Number(y))
  let m = /^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$/.exec(s)
  if (m) return iso(Number(m[1]), Number(m[2]))
  m = /^(?:(\d{1,2})[./])?(\d{1,2})[./-](\d{4}|\d{2})$/.exec(s)
  if (m) return iso(year(m[3]), Number(m[2]))
  m = /^([a-zä]{3,9})\.?[\s-]*(\d{4}|\d{2})$/.exec(s)
  if (m) {
    const at = MONTHS.findIndex((name) => m![1].startsWith(name) || (name === 'mar' && m![1].startsWith('mär')))
    return at < 0 ? undefined : iso(year(m[2]), at + 1)
  }
  return undefined
}

/** Whether an investment's cash out covers its amount, part of it, or none. */
export function scheduleState(i: CapexItem): 'none' | 'part' | 'full' | null {
  if (i.amount === null || toCents(i.amount) === 0n) return null
  const scheduled = toCents(i.scheduled)
  if (scheduled === 0n) return 'none'
  return scheduled >= toCents(i.amount) ? 'full' : 'part'
}

/** Amounts rounded to thousands for overviews: "1,234k". */
export function thousands(value: string): string {
  const k = Number(toCents(value) / 100n) / 1000
  if (k === 0) return '–'
  return `${Math.round(k).toLocaleString('en')}k`
}
