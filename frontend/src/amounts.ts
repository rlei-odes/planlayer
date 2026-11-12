// Amounts are decimal strings from the API (T-12); exact arithmetic here uses BigInt cents.
// null is a blank cell, distinct from "0.00".

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

/** "1234567.5" -> "1,234,567.50"; blank stays blank. */
export function format(value: Amount): string {
  if (value === null) return ''
  const [whole, frac = '00'] = fromCents(toCents(value)).split('.')
  const sign = whole.startsWith('-') ? '-' : ''
  return `${sign}${whole.replace('-', '').replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${frac}`
}

/**
 * What a planner types or pastes, to a canonical decimal string for the API.
 * Accepts 1234.56, 1,234.56, 1.234,56, 1'234.56, -12, (12) and currency signs.
 * Returns null for an empty cell and undefined when the text is not a number.
 */
export function parse(raw: string): Amount | undefined {
  let s = raw.trim().replace(/[\s ]|CHF|EUR|USD|[€$]/g, '')
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
  const lastDot = s.lastIndexOf('.')
  const lastComma = s.lastIndexOf(',')
  if (lastDot >= 0 && lastComma >= 0) {
    // Both present: the later one is the decimal separator.
    s = lastComma > lastDot ? s.replace(/\./g, '').replace(',', '.') : s.replace(/,/g, '')
  } else if (lastComma >= 0) {
    const parts = s.split(',')
    s = parts.length === 2 && parts[1].length <= 2 ? `${parts[0]}.${parts[1]}` : parts.join('')
  } else if ((s.match(/\./g) ?? []).length > 1) {
    s = s.replace(/\./g, '')
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
