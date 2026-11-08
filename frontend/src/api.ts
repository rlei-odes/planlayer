// Planning API client. Mirrors planlayer.api.planning; replaced by the generated client (F-04).

import type { Amount } from './amounts'

export type Version = { code: string; name: string; kind: string; fiscal_year: number }
export type PlanningUnit = { code: string; name: string; entity_code: string | null; owner: string | null }
export type Cell = { value: Amount; row_version: number | null }
export type Row = {
  kind: 'group' | 'account'
  code: string
  name: string
  group_code: string
  prior_year: Amount[]
  plan: Cell[]
}
export type Grid = {
  version_code: string
  version_name: string
  fiscal_year: number
  cost_center_code: string
  cost_center_name: string
  periods: string[]
  prior_periods: string[]
  rows: Row[]
}
export type Edit = {
  account_code: string
  month: number | null // null = full year, spread by prior-year actuals
  value: Amount
  expected: (number | null)[]
}
export type Conflict = { account_code: string; month: number }

export class ConflictError extends Error {
  conflicts: Conflict[]

  constructor(conflicts: Conflict[]) {
    super(`${conflicts.length} cell(s) were changed by someone else`)
    this.conflicts = conflicts
  }
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail ?? `${response.status} ${response.statusText}`)
  }
  return response.json() as Promise<T>
}

export const api = {
  versions: () => fetch('/api/versions').then((r) => json<Version[]>(r)),
  costCenters: (version: string) =>
    fetch(`/api/versions/${encodeURIComponent(version)}/cost-centers`).then((r) => json<PlanningUnit[]>(r)),
  grid: (version: string, cc: string) =>
    fetch(`/api/versions/${encodeURIComponent(version)}/cost-centers/${encodeURIComponent(cc)}/grid`).then((r) =>
      json<Grid>(r),
    ),
  async edit(version: string, cc: string, edits: Edit[]): Promise<Grid> {
    const response = await fetch(
      `/api/versions/${encodeURIComponent(version)}/cost-centers/${encodeURIComponent(cc)}/edits`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ edits }) },
    )
    if (response.status === 409) throw new ConflictError((await response.json()) as Conflict[])
    return json<Grid>(response)
  },
}
