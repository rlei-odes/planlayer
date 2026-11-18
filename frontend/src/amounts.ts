// Amounts are decimal strings from the API (T-12); exact arithmetic here uses BigInt cents.
// null is a blank cell, distinct from "0.00".

import type * as S from './schema'

export type Amount = string | null

export function toCents(value: string): bigint {
  const negative = value.startsWith('-')
  const [whole, frac = ''] = value.replace('-', '').split('.')
  const cents = BigInt(whole || '0') * 100n + BigInt((frac + '00').slice(0, 2))
  return negative ? -cents : cents
}

export function fromCents(cents: bigint): string {
  const negative = cents < 0n
  const abs = negative ? -cents : cents
  return `${negative ? '-' : ''}${abs / 100n}.${(abs % 100n).toString().padStart(2, '0')}`
}

/** Sum of amounts; null if every input is blank. */
export function sum(values: Amount[]): Amount {
  const present = values.filter((v): v is string => v !== null)
  return present.length === 0 ? null : fromCents(present.reduce((s, v) => s + toCents(v), 0n))
}

// How amounts are shown and typed: the user's number format (from /api/me), set once the user
// is known and whenever they change it. Display only: values stay exact strings (T-12).
export type NumberFormat = S.NumberFormatIO
export type Separators = NumberFormat['separators']
export type Scale = NumberFormat['scale']

export const SEPARATORS: Record<Separators, { group: string; decimal: string; example: string }> = {
  comma_dot: { group: ',', decimal: '.', example: '1,234.56' },
  dot_comma: { group: '.', decimal: ',', example: '1.234,56' },
  apostrophe_dot: { group: "'", decimal: '.', example: "1'234.56" },
  space_comma: { group: '\u00a0', decimal: ',', example: '1 234,56' },
}

let current: NumberFormat = { separators: 'comma_dot', decimals: true, scale: 'full' }

export function setNumberFormat(f: NumberFormat) {
  current = f
}

export function numberFormat(): NumberFormat {
  return current
}

function grouped(whole: string, f: NumberFormat): string {
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, SEPARATORS[f.separators].group)
}

/** abs / 10^digits, rounded half up to `frac` decimals, as digits and decimals. */
function rounded(abs: bigint, digits: number, frac: number): [string, string] {
  const unit = 10n ** BigInt(digits - frac)
  const n = (abs + unit / 2n) / unit
  const s = n.toString().padStart(frac + 1, '0')
  return [s.slice(0, s.length - frac), s.slice(s.length - frac)]
}

/** The exact amount, "1234567.5" -> "1,234,567.50" in the user's separators; blank stays blank.
 * What the cell editor shows, and what is copied. */
export function format(value: Amount, f: NumberFormat = current): string {
  if (value === null) return ''
  const cents = toCents(value)
  const abs = cents < 0n ? -cents : cents
  return `${cents < 0n ? '-' : ''}${grouped((abs / 100n).toString(), f)}${SEPARATORS[f.separators].decimal}${(abs % 100n).toString().padStart(2, '0')}`
}

/**
 * An amount as the user chose to see it: full, with or without cents, or in thousands or
 * millions. Thousands and millions show three significant digits and drop trailing zeros
 * (10.5k, 123k, 1.23M), but never round away digits of the whole part (1,235k). Without `unit`,
 * the k or M is left to a column's heading.
 */
export function display(value: Amount, f: NumberFormat = current, unit = true): string {
  if (value === null) return ''
  const scale = f.scale
  const cents = toCents(value)
  const abs = cents < 0n ? -cents : cents
  let whole: string
  let frac: string
  if (scale === 'full') {
    ;[whole, frac] = f.decimals ? [(abs / 100n).toString(), (abs % 100n).toString().padStart(2, '0')] : rounded(abs, 2, 0)
  } else {
    const digits = scale === 'k' ? 5 : 8 // cents to thousands, to millions
    const wholeDigits = (abs / 10n ** BigInt(digits)).toString().length
    ;[whole, frac] = rounded(abs, digits, Math.max(0, 3 - wholeDigits))
    // 999.95k rounds to 1000.0k: one whole digit more, one decimal less.
    if (whole.length > wholeDigits) [whole, frac] = rounded(abs, digits, Math.max(0, 3 - whole.length))
    frac = frac.replace(/0+$/, '')
  }
  const zero = /^0*$/.test(whole + frac)
  const text = `${grouped(whole, f)}${frac ? SEPARATORS[f.separators].decimal + frac : ''}`
  return `${cents < 0n && !zero ? '-' : ''}${text}${scale === 'full' || !unit ? '' : scale}`
}

/** Example amounts in a format, for choosing one. */
export function example(f: NumberFormat): string {
  return ['1234567.89', '10520', '-523.4'].map((v) => display(v, f)).join(' · ')
}

/**
 * What a planner types or pastes, to a canonical decimal string for the API.
 * Accepts 1234.56, 1,234.56, 1.234,56, 1'234.56, 1 234,56, -12, (12) and currency signs. A lone
 * separator follows the user's format: "1.234" is 1234 to a user of 1.234,56 and 1.234 (refused,
 * more than cents) to a user of 1,234.56; one the format doesn't use is read as before.
 * Returns null for an empty cell and undefined when the text is not a number.
 */
export function parse(raw: string): Amount | undefined {
  let s = raw.trim().replace(/\s|CHF|EUR|USD|[€$]/g, '')
  if (s === '') return null
  let negative = false
  if (/^\(.*\)$/.test(s)) {
    negative = true
    s = s.slice(1, -1)
  }
  if (s.startsWith('-')) {
    negative = !negative
    s = s.slice(1)
  }
  s = s.replace(/'/g, '')
  const { group, decimal } = SEPARATORS[current.separators]
  const lastDot = s.lastIndexOf('.')
  const lastComma = s.lastIndexOf(',')
  const only = lastDot >= 0 && lastComma < 0 ? '.' : lastComma >= 0 && lastDot < 0 ? ',' : null
  if (lastDot >= 0 && lastComma >= 0) {
    // Both present: the later one is the decimal separator.
    s = lastComma > lastDot ? s.replace(/\./g, '').replace(',', '.') : s.replace(/,/g, '')
  } else if (only && (s.split(only).length > 2 || (only === group && /^\d{1,3}[.,]\d{3}$/.test(s)))) {
    // 1.234.567, or 1.234 to a user of 1.234,56: thousands.
    s = s.split(only).join('')
  } else if (only === decimal) {
    s = s.replace(only, '.')
  } else if (only === ',') {
    // A comma the format doesn't use: decimals if at most two digits follow.
    const [whole, frac] = s.split(',')
    s = frac.length <= 2 ? `${whole}.${frac}` : whole + frac
  }
  if (!/^\d+(\.\d{1,2})?$/.test(s)) return undefined
  return fromCents(negative ? -toCents(s) : toCents(s))
}

/** Quantity (up to 4 decimals) × unit price, rounded half up to the cent, as the database computes
 * an item's amount (ADR 0017); undefined when either isn't a number. */
export function times(quantity: string, price: string): string | undefined {
  const q = quantity.trim().replace(',', '.') || '1'
  const p = parse(price)
  if (!/^-?\d+(\.\d{1,4})?$/.test(q) || !p) return undefined
  const [whole, frac = ''] = q.replace('-', '').split('.')
  const q4 = (BigInt(whole) * 10000n + BigInt((frac + '0000').slice(0, 4))) * (q.startsWith('-') ? -1n : 1n)
  const product = toCents(p) * q4 // cents × 10^4
  const negative = product < 0n
  const abs = negative ? -product : product
  const rounded = (abs + 5000n) / 10000n
  return fromCents(negative ? -rounded : rounded)
}
