// API client. The types come from the generated schema.ts (F-04, `npm run gen:api`); the names
// here are the frontend's short names for them.

import type * as S from './schema'

export type RoundStatus = S.Version['status']
export type Status = S.PlanningUnit['status']
export type EntryForm = S.RuleIO['form']
export type Action = S.ActionIn['action']

export type Version = S.Version
export type PlanningUnit = S.PlanningUnit
export type Cell = S.CellOut
export type Row = S.RowOut
export type Grid = S.GridOut
export type Edit = S.EditIn
export type Conflict = S.ConflictOut
export type Account = S.AccountOut
export type StatusRow = S.StatusRowOut
export type Overview = S.OverviewOut
export type ActionRequest = S.ActionIn

export type Me = S.Me
export type User = S.UserOut
export type Responsibility = S.ResponsibilityOut
export type Responsibilities = S.ResponsibilitiesOut

export type Dataset = S.DatasetOut['dataset']
export type MemberDataset = Exclude<Dataset, 'actuals'>
// The API takes the hierarchy as a plain object and validates it in the sync; these are its forms.
export type Hierarchy = { parent: string } | { levels: { code: string; name: string }[] }
export type Mapping = Omit<S.MappingIO, 'hierarchy'> & { hierarchy: Hierarchy | null }
export type DatasetInfo = Omit<S.DatasetOut, 'mapping'> & { mapping: Mapping | null }
export type Preview = S.QueryPreviewOut
export type SyncPlan = S.PlanOut
export type MemberRow = S.MemberOut
export type NewMember = S.MemberIn

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
  loginOptions: () => fetch('/api/auth/options').then((res) => json<S.LoginOptions>(res)),
  devUsers: () => fetch('/api/auth/dev-users').then((res) => json<S.DevUser[]>(res)),
  devLogin: (login: string) => post('/api/auth/dev-login', { login }).then((res) => json<Me>(res)),
  logout: () => post('/api/auth/logout', {}).then((res) => json<null>(res)),
  users: () => fetch('/api/users').then((res) => json<User[]>(res)),
  createUser: (user: S.UserIn) => post('/api/users', user).then((res) => json<null>(res)),
  changeUser: (login: string, change: S.UserChange) =>
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
      json<S.SyncRunOut>(res),
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
