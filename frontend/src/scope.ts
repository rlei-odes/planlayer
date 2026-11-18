// Helpers for the setup page's Cost centers and Accounts tabs (ADR 0014).

import { display, numberFormat } from './amounts'
import type * as S from './schema'

export type ScopeRow = S.ScopeRowOut
export type AccountSet = S.AccountSetIO

/** An amount in thousands, in the user's separators, for display only; blank stays blank. */
export function thousands(value: string | null): string {
  return display(value, { ...numberFormat(), scale: 'k' }, false)
}

/** The set assigned to a member, and the one it inherits from the nearest node above. */
export function setOf(
  sets: AccountSet[],
  rows: ScopeRow[],
  code: string,
): { own: string | null; inherited: { set: string; from: string } | null } {
  const assigned = new Map<string, string>()
  for (const s of sets) for (const c of s.assigned_to ?? []) assigned.set(c, s.name)
  const parent = new Map(rows.map((r) => [r.code, r.parent]))
  let inherited: { set: string; from: string } | null = null
  for (let node = parent.get(code) ?? null; node; node = parent.get(node) ?? null) {
    const set = assigned.get(node)
    if (set) {
      inherited = { set, from: node }
      break
    }
  }
  return { own: assigned.get(code) ?? null, inherited }
}

/** Assign ``code`` to the set named ``name``, or to none. */
export function assign(sets: AccountSet[], code: string, name: string | null): AccountSet[] {
  return sets.map((s) => {
    const rest = (s.assigned_to ?? []).filter((c) => c !== code)
    return { ...s, assigned_to: s.name === name ? [...rest, code].sort() : rest }
  })
}

/** Whether a set holds the account itself, or covers it through a group above it. */
export function inSet(set: AccountSet, rows: ScopeRow[], code: string): 'own' | 'group' | null {
  if (set.accounts.includes(code)) return 'own'
  const parent = new Map(rows.map((r) => [r.code, r.parent]))
  for (let node = parent.get(code) ?? null; node; node = parent.get(node) ?? null) {
    if (set.accounts.includes(node)) return 'group'
  }
  return null
}

/** Rows whose own text matches, with every node above them, keeping tree order. */
export function filterTree(rows: ScopeRow[], keep: (r: ScopeRow) => boolean): ScopeRow[] {
  const parent = new Map(rows.map((r) => [r.code, r.parent]))
  const shown = new Set<string>()
  for (const r of rows) {
    if (!keep(r)) continue
    for (let c: string | null = r.code; c && !shown.has(c); c = parent.get(c) ?? null) shown.add(c)
  }
  return rows.filter((r) => shown.has(r.code))
}
