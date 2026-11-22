import type { WorkforceSheet } from './api'

/** The staff type a new position or workforce line starts with: the one most of the cost
 * center's positions and lines have, else the seeded Employee, else the first. */
export function defaultStaffType(sheet: WorkforceSheet): string {
  const used = new Map<string, number>()
  const count = (code: string | null | undefined) => code && used.set(code, (used.get(code) ?? 0) + 1)
  for (const p of sheet.positions) count(p.staff_type_code)
  for (const r of sheet.rows) if (r.staff_category_code) count(r.staff_type_code)
  const known = new Set(sheet.staff_types.map((t) => t.code))
  const most = [...used].filter(([code]) => known.has(code)).sort((a, b) => b[1] - a[1])[0]?.[0]
  return most ?? (known.has('EMP') ? 'EMP' : (sheet.staff_types[0]?.code ?? ''))
}
