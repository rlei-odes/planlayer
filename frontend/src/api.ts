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
  can_write: boolean
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
  actions: Action[] // what the logged-in user may do now
}
export type Overview = {
  round: Version
  rows: StatusRow[]
  not_planned: { code: string; name: string; reason: 'new' | 'disabled' }[]
}
export type ActionRequest = { action: Action; cost_centers?: string[]; node?: string; comment?: string }

export type Me = {
  login: string
  display_name: string
  email: string | null
  is_admin: boolean
  is_controller: boolean
  is_viewer: boolean
}
export type User = Me & { disabled: boolean }
export type Responsibility = {
  kind: 'node' | 'cost_center'
  code: string
  name: string
  depth: number
  owner: string | null // synced owner value
  matched: string | null // login matched from it
  planner: string | null // effective owner (login)
  overridden: boolean
  deputies: string[]
  approvers: string[]
}
export type Responsibilities = { rows: Responsibility[]; unmatched_owners: string[]; users: string[] }

export type Dataset = 'entities' | 'cost_centers' | 'accounts' | 'actuals'
export type MemberDataset = Exclude<Dataset, 'actuals'>
export type Hierarchy = { parent: string } | { levels: { code: string; name: string }[] }
export type Mapping = { query: string; fields: Record<string, string>; hierarchy: Hierarchy | null }
export type DatasetInfo = {
  dataset: Dataset
  fields: { name: string; type: string; required: boolean }[]
  has_hierarchy: boolean
  mapping: Mapping | null
}
export type Preview = { columns: string[]; rows: (string | null)[][]; error: string | null }
export type SyncPlan = {
  datasets: string[]
  problems: string[]
  new: string[]
  changed: string[]
  gone: string[]
  matches: { key: string; dimension: string; code: string; name: string }[]
  actual_cells: number | null
}
export type MemberRow = {
  code: string
  name: string
  source_name: string | null
  origin: 'source' | 'manual'
  is_group: boolean
  depth: number
  parent: string | null
  source_parent: string | null
  renamed: boolean
  moved: boolean
  entity_code: string | null
  owner: string | null
  account_type: string | null
}
export type NewMember = {
  code: string
  name: string
  parent: string | null
  is_group: boolean
  entity_code?: string | null
  owner?: string | null
  account_type?: string | null
}

/** The session is missing or has ended. */
export class UnauthorizedError extends Error {}

export class ConflictError extends Error {
  conflicts: Conflict[]

  constructor(conflicts: Conflict[]) {
    super(`${conflicts.length} cell(s) were changed by someone else`)
    this.conflicts = conflicts
  }
}

async function json<T>(response: Response): Promise<T> {
  if (response.status === 401) {
    window.dispatchEvent(new Event('planlayer:unauthorized'))
    throw new UnauthorizedError('not logged in')
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail ?? `${response.status} ${response.statusText}`)
  }
  return response.json() as Promise<T>
}

const send = (method: string, url: string, body?: unknown) =>
  fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
const post = (url: string, body: unknown) => send('POST', url, body)
const r = (code: string) => `/api/responsibilities/${encodeURIComponent(code)}`

const v = (version: string) => `/api/versions/${encodeURIComponent(version)}`
const cc = (version: string, code: string) => `${v(version)}/cost-centers/${encodeURIComponent(code)}`

export const api = {
  me: () => fetch('/api/me').then((res) => json<Me>(res)),
  loginOptions: () => fetch('/api/auth/options').then((res) => json<{ dev_login: boolean }>(res)),
  devUsers: () => fetch('/api/auth/dev-users').then((res) => json<{ login: string; display_name: string }[]>(res)),
  devLogin: (login: string) => post('/api/auth/dev-login', { login }).then((res) => json<Me>(res)),
  logout: () => post('/api/auth/logout', {}).then((res) => json<null>(res)),
  users: () => fetch('/api/users').then((res) => json<User[]>(res)),
  createUser: (user: Omit<User, 'disabled'>) => post('/api/users', user).then((res) => json<null>(res)),
  changeUser: (login: string, change: Partial<User>) =>
    send('PATCH', `/api/users/${encodeURIComponent(login)}`, change).then((res) => json<null>(res)),
  responsibilities: () => fetch('/api/responsibilities').then((res) => json<Responsibilities>(res)),
  setOwner: (cc: string, login: string | null) => send('PUT', `${r(cc)}/owner`, { login }).then((res) => json<null>(res)),
  clearOwner: (cc: string) => send('DELETE', `${r(cc)}/owner`).then((res) => json<null>(res)),
  addDeputy: (code: string, login: string) => post(`${r(code)}/deputies`, { login }).then((res) => json<null>(res)),
  removeDeputy: (code: string, login: string) =>
    send('DELETE', `${r(code)}/deputies/${encodeURIComponent(login)}`).then((res) => json<null>(res)),
  addApprover: (code: string, login: string) => post(`${r(code)}/approvers`, { login }).then((res) => json<null>(res)),
  removeApprover: (code: string, login: string) =>
    send('DELETE', `${r(code)}/approvers/${encodeURIComponent(login)}`).then((res) => json<null>(res)),
  datasets: () => fetch('/api/datasets').then((res) => json<DatasetInfo[]>(res)),
  saveMapping: (d: Dataset, m: Mapping) => send('PUT', `/api/datasets/${d}/mapping`, m).then((res) => json<null>(res)),
  deleteMapping: (d: Dataset) => send('DELETE', `/api/datasets/${d}/mapping`).then((res) => json<null>(res)),
  preview: (d: Dataset, m: Mapping, mapped: boolean) =>
    post(`/api/datasets/${d}/preview`, { ...m, mapped }).then((res) => json<Preview>(res)),
  syncPlan: (entity: string | null) => post('/api/sync/plan', { entity }).then((res) => json<SyncPlan>(res)),
  syncRun: (entity: string | null, confirm: string[]) =>
    post('/api/sync/run', { entity, confirm }).then((res) =>
      json<{ members: number; actual_rows: number; matched: string[] }>(res),
    ),
  members: (d: MemberDataset) => fetch(`/api/members/${d}`).then((res) => json<MemberRow[]>(res)),
  addMember: (d: MemberDataset, m: NewMember) => post(`/api/members/${d}`, m).then((res) => json<null>(res)),
  changeMember: (d: MemberDataset, code: string, change: Record<string, string | null>) =>
    send('PATCH', `/api/members/${d}/${encodeURIComponent(code)}`, change).then((res) => json<null>(res)),
  resetMember: (d: MemberDataset, code: string) =>
    send('DELETE', `/api/members/${d}/${encodeURIComponent(code)}/override`).then((res) => json<null>(res)),
  deleteMember: (d: MemberDataset, code: string) =>
    send('DELETE', `/api/members/${d}/${encodeURIComponent(code)}`).then((res) => json<null>(res)),
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
