// Planning API client. Mirrors planlayer.api.planning; replaced by the generated client (F-04).

import type { Amount } from './amounts'

export type RoundStatus = 'setup' | 'open' | 'closed'
export type Status = 'not_started' | 'in_progress' | 'submitted' | 'approved'
export type EntryForm = 'monthly' | 'annual' | 'annual_even' | 'annual_profile' | 'annual_reference'
export type Action = 'submit' | 'approve' | 'reject' | 'reopen'

export type Version = {
  code: string
  name: string
  kind: string
  fiscal_year: number
  status: RoundStatus
  deadline: string | null
}
export type PlanningUnit = {
  code: string
  name: string
  entity_code: string | null
  owner: string | null
  status: Status
}
export type Cell = { value: Amount; row_version: number | null }
export type Row = {
  kind: 'group' | 'account'
  code: string
  name: string
  group_code: string
  comparisons: Amount[][] // per comparison column, 12 months
  plan: Cell[]
  entry_form: EntryForm | null
  basis: string | null
  fallback: boolean
  months_visible: boolean
  months_editable: boolean
  total_editable: boolean
}
export type Grid = {
  version_code: string
  version_name: string
  fiscal_year: number
  round_status: RoundStatus
  deadline: string | null
  cost_center_code: string
  cost_center_name: string
  status: Status
  lock_reason: string | null
  periods: string[]
  comparisons: { kind: string; label: string }[]
  rows: Row[]
}
export type Edit = {
  account_code: string
  month: number | null // null = full year, spread by the line's basis
  value: Amount
  expected: (number | null)[]
}
export type Conflict = { account_code: string; month: number }
export type Account = { code: string; name: string }
export type StatusRow = {
  kind: 'node' | 'cost_center'
  code: string
  name: string
  depth: number
  counts: Partial<Record<Status, number>>
  status: Status | null
  owner: string | null
  approvers: string[] // empty: the controller
  changed_at: string | null
  changed_by: string | null
  overdue: boolean
}
export type Overview = {
  round: Version
  rows: StatusRow[]
  not_planned: { code: string; name: string; reason: 'new' | 'disabled' }[]
}
export type ActionRequest = { action: Action; cost_centers?: string[]; node?: string; comment?: string }

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

const post = (url: string, body: unknown) =>
  fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

const v = (version: string) => `/api/versions/${encodeURIComponent(version)}`
const cc = (version: string, code: string) => `${v(version)}/cost-centers/${encodeURIComponent(code)}`

export const api = {
  versions: () => fetch('/api/versions').then((r) => json<Version[]>(r)),
  setRoundStatus: (version: string, status: RoundStatus) =>
    post(`${v(version)}/status`, { status }).then((r) => json<Version>(r)),
  costCenters: (version: string) => fetch(`${v(version)}/cost-centers`).then((r) => json<PlanningUnit[]>(r)),
  setEnabled: (version: string, code: string, enabled: boolean) =>
    post(`${cc(version, code)}/enabled`, { enabled }).then((r) => json<null>(r)),
  grid: (version: string, code: string) => fetch(`${cc(version, code)}/grid`).then((r) => json<Grid>(r)),
  async edit(version: string, code: string, edits: Edit[]): Promise<Grid> {
    const response = await post(`${cc(version, code)}/edits`, { edits })
    if (response.status === 409) throw new ConflictError((await response.json()) as Conflict[])
    return json<Grid>(response)
  },
  accounts: (version: string) => fetch(`${v(version)}/accounts`).then((r) => json<Account[]>(r)),
  addAccount: (version: string, code: string, account: string) =>
    post(`${cc(version, code)}/accounts`, { account_code: account }).then((r) => json<Grid>(r)),
  act: (version: string, request: ActionRequest) => post(`${v(version)}/workflow`, request).then((r) => json<string[]>(r)),
  overview: (version: string) => fetch(`${v(version)}/overview`).then((r) => json<Overview>(r)),
}

export const STATUS_LABEL: Record<Status, string> = {
  not_started: 'Not started',
  in_progress: 'In progress',
  submitted: 'Submitted',
  approved: 'Approved',
}
