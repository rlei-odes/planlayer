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
export type LineSpread = S.LineSpreadIO
export type ProfileRef = S.ProfileIOOutput
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
export type SyncYears = S.SyncYearsOut
export type MemberRow = S.MemberOut
export type CapacityUnit = S.CapacityUnitOut
export type CapacitySheet = S.CapacitySheetOut
export type CapacityRow = S.CapacityRowOut
export type HoursEdit = S.HoursEditIn
// Workforce planning (ADR 0020).
export type WorkforceSheet = S.WorkforceSheetOut
export type WorkforceRow = S.WorkforceRowOut
export type WorkforceEdit = S.WorkforceEditIn
export type Position = S.PositionOut
export type PositionIn = S.WorkforcePositionIn
// Published signs (ADR 0019).
export type Signs = S.SignsOut
// Cost forwarding (ADR 0018).
export type ForwardingPage = S.ForwardingPageOut
export type Forwarding = S.ForwardingOut
export type ForwardingIn = S.ForwardingIn
export type NewMember = S.MemberIn
// Line notes and item lists (ADR 0017).
export type NoteLine = S.NoteLineIn
export type LineNotes = S.LineNotesOut
export type Thread = S.ThreadOut
export type NoteEntry = S.NoteOut
export type Person = S.PersonOut
export type Notepad = S.NotepadOut
export type NotepadRow = S.GridRowIO
export type MyNotes = S.MyNotesOut
export type MyThread = S.MyThreadOut
export type NotesMarker = S.NotesMarkerOut
export type ItemList = S.ItemListOut
export type ListSummary = S.ListSummaryOut
export type ItemRow = S.ItemIO
export type Visibility = S.ListSummaryOut['visibility']

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
const cap = (version: string, code: string) => `${v(version)}/capacity/${encodeURIComponent(code)}`
const wf = (version: string, code: string) => `${v(version)}/workforce/${encodeURIComponent(code)}`
const fw = (version: string) => `${v(version)}/forwarding`

/** A line as query parameters: only the fields it has. */
function lineQuery(version: string, line: NoteLine): string {
  const params: Record<string, string> = { version, module: line.module, cost_center: line.cost_center }
  for (const key of ['account', 'work_center', 'activity_type'] as const) {
    const value = line[key]
    if (value) params[key] = value
  }
  return new URLSearchParams(params).toString()
}

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
  addCapacityPlanner: (code: string, login: string) =>
    post(`${r(code)}/capacity-planners`, { login }).then((res) => json<null>(res)),
  removeCapacityPlanner: (code: string, login: string) =>
    send('DELETE', `${r(code)}/capacity-planners/${encodeURIComponent(login)}`).then((res) => json<null>(res)),
  addApprover: (code: string, login: string) => post(`${r(code)}/approvers`, { login }).then((res) => json<null>(res)),
  removeApprover: (code: string, login: string) =>
    send('DELETE', `${r(code)}/approvers/${encodeURIComponent(login)}`).then((res) => json<null>(res)),
  datasets: () => fetch('/api/datasets').then((res) => json<DatasetInfo[]>(res)),
  saveMapping: (d: Dataset, m: Mapping) => send('PUT', `/api/datasets/${d}/mapping`, m).then((res) => json<null>(res)),
  deleteMapping: (d: Dataset) => send('DELETE', `/api/datasets/${d}/mapping`).then((res) => json<null>(res)),
  preview: (d: Dataset, m: Mapping, mapped: boolean) =>
    post(`/api/datasets/${d}/preview`, { ...m, mapped }).then((res) => json<Preview>(res)),
  syncYears: () => fetch('/api/sync/years').then((res) => json<SyncYears>(res)),
  syncPlan: (entity: string | null, years: S.YearsIO | null) =>
    post('/api/sync/plan', { entity, years }).then((res) => json<SyncPlan>(res)),
  syncRun: (entity: string | null, confirm: string[], years: S.YearsIO | null) =>
    post('/api/sync/run', { entity, confirm, years }).then((res) =>
      json<S.SyncRunOut>(res),
    ),
  signs: () => fetch('/api/settings/signs').then((res) => json<Signs>(res)),
  setSigns: (convention: Signs['convention']) =>
    send('PUT', '/api/settings/signs', { convention }).then((res) => json<Signs>(res)),
  forwarding: (version: string) => fetch(fw(version)).then((res) => json<ForwardingPage>(res)),
  createForwarding: (version: string, f: ForwardingIn) => post(fw(version), f).then((res) => json<ForwardingPage>(res)),
  changeForwarding: (version: string, code: string, f: ForwardingIn) =>
    send('PUT', `${fw(version)}/${encodeURIComponent(code)}`, f).then((res) => json<ForwardingPage>(res)),
  deleteForwarding: (version: string, code: string) =>
    send('DELETE', `${fw(version)}/${encodeURIComponent(code)}`).then((res) => json<ForwardingPage>(res)),
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
  capacityUnits: (version: string) => fetch(`${v(version)}/capacity`).then((r) => json<CapacityUnit[]>(r)),
  capacitySheet: (version: string, code: string) => fetch(cap(version, code)).then((r) => json<CapacitySheet>(r)),
  async capacityEdit(version: string, code: string, edits: HoursEdit[]): Promise<CapacitySheet> {
    const response = await post(`${cap(version, code)}/edits`, { edits })
    if (response.status === 409) throw new ConflictError((await response.json()) as Conflict[])
    return json<CapacitySheet>(response)
  },
  changeLine: (version: string, code: string, workCenter: string, activityType: string, change: S.LineChangeIn) =>
    send(
      'PATCH',
      `${cap(version, code)}/lines/${encodeURIComponent(workCenter)}/${encodeURIComponent(activityType)}`,
      change,
    ).then((r) => json<CapacitySheet>(r)),
  async capacityUndo(version: string, code: string, batchId: string): Promise<CapacitySheet> {
    const response = await post(`${cap(version, code)}/undo`, { batch_id: batchId })
    if (response.status === 409) throw new ConflictError((await response.json()) as Conflict[])
    return json<CapacitySheet>(response)
  },
  addLine: (version: string, code: string, line: S.LineIn) =>
    post(`${cap(version, code)}/lines`, line).then((r) => json<CapacitySheet>(r)),
  workforceSheet: (version: string, code: string) => fetch(wf(version, code)).then((r) => json<WorkforceSheet>(r)),
  async workforceEdit(version: string, code: string, edits: WorkforceEdit[]): Promise<WorkforceSheet> {
    const response = await post(`${wf(version, code)}/edits`, { edits })
    if (response.status === 409) throw new ConflictError((await response.json()) as Conflict[])
    return json<WorkforceSheet>(response)
  },
  async workforceUndo(version: string, code: string, batchId: string): Promise<WorkforceSheet> {
    const response = await post(`${wf(version, code)}/undo`, { batch_id: batchId })
    if (response.status === 409) throw new ConflictError((await response.json()) as Conflict[])
    return json<WorkforceSheet>(response)
  },
  addWorkforceLine: (version: string, code: string, line: S.WorkforceLineIn) =>
    post(`${wf(version, code)}/lines`, line).then((r) => json<WorkforceSheet>(r)),
  addPosition: (version: string, code: string, p: PositionIn) =>
    post(`${wf(version, code)}/positions`, p).then((r) => json<WorkforceSheet>(r)),
  changePosition: (version: string, code: string, id: number, p: PositionIn) =>
    send('PUT', `${wf(version, code)}/positions/${id}`, p).then((r) => json<WorkforceSheet>(r)),
  deletePosition: (version: string, code: string, id: number) =>
    send('DELETE', `${wf(version, code)}/positions/${id}`).then((r) => json<WorkforceSheet>(r)),
  staffFromHr: (version: string, code: string) => post(`${wf(version, code)}/hr`, {}).then((r) => json<WorkforceSheet>(r)),
  workforceFromHr: (version: string) => post(`${v(version)}/workforce/hr`, {}).then((r) => json<S.HrTakenOut>(r)),
  costCenters: (version: string) => fetch(`${v(version)}/cost-centers`).then((r) => json<PlanningUnit[]>(r)),
  setEnabled: (version: string, code: string, enabled: boolean) =>
    post(`${cc(version, code)}/enabled`, { enabled }).then((r) => json<null>(r)),
  grid: (version: string, code: string) => fetch(`${cc(version, code)}/grid`).then((r) => json<Grid>(r)),
  async edit(version: string, code: string, edits: Edit[]): Promise<Grid> {
    const response = await post(`${cc(version, code)}/edits`, { edits })
    if (response.status === 409) throw new ConflictError((await response.json()) as Conflict[])
    return json<Grid>(response)
  },
  /** Revert one of the user's change batches on the sheet (E-05); the result carries the undo's
   * own batch, which undoes the undo. */
  async undo(version: string, code: string, batchId: string): Promise<Grid> {
    const response = await post(`${cc(version, code)}/undo`, { batch_id: batchId })
    if (response.status === 409) throw new ConflictError((await response.json()) as Conflict[])
    return json<Grid>(response)
  },
  /** Accounts a planner may add to the cost center's sheet: those it plans. */
  accounts: (version: string, code: string) => fetch(`${cc(version, code)}/accounts`).then((r) => json<Account[]>(r)),
  addAccount: (version: string, code: string, account: string) =>
    post(`${cc(version, code)}/accounts`, { account_code: account }).then((r) => json<Grid>(r)),
  /** How one line spreads the year totals typed from now on; null: as the round is set up. */
  setSpread: (version: string, code: string, account: string, spread: LineSpread | null) =>
    send('PUT', `${cc(version, code)}/lines/${encodeURIComponent(account)}/spread`, { spread }).then((r) =>
      json<Grid>(r),
    ),
  profiles: () => fetch('/api/profiles').then((r) => json<ProfileRef[]>(r)),
  act: (version: string, request: ActionRequest) => post(`${v(version)}/workflow`, request).then((r) => json<string[]>(r)),
  overview: (version: string) => fetch(`${v(version)}/overview`).then((r) => json<Overview>(r)),
  // Line notes (ADR 0017): written from a round, which they then belong to.
  lineNotes: (version: string, line: NoteLine) =>
    fetch(`/api/notes/line?${lineQuery(version, line)}`).then((r) => json<LineNotes>(r)),
  startThread: (version: string, line: NoteLine, body: string, mentions: string[], assign: string | null) =>
    post('/api/notes/threads', { version, line, body, mentions, assign }).then((r) => json<LineNotes>(r)),
  reply: (version: string, thread: number, body: string, mentions: string[], assign: string | null) =>
    post(`/api/notes/threads/${thread}/replies`, { version, body, mentions, assign }).then((r) => json<null>(r)),
  resolveThread: (version: string, thread: number, resolved: boolean) =>
    post(`/api/notes/threads/${thread}/resolved`, { version, resolved }).then((r) => json<null>(r)),
  editNote: (version: string, note: number, body: string, mentions: string[]) =>
    send('PUT', `/api/notes/${note}`, { version, body, mentions }).then((r) => json<null>(r)),
  deleteNote: (note: number) => send('DELETE', `/api/notes/${note}`).then((r) => json<null>(r)),
  saveNotepad: (version: string, line: NoteLine, rows: NotepadRow[]) =>
    send('PUT', '/api/notes/notepad', { version, line, rows }).then((r) => json<LineNotes>(r)),
  people: (costCenter: string, module: NoteLine['module']) =>
    fetch(`/api/notes/people?${new URLSearchParams({ cost_center: costCenter, module })}`).then((r) =>
      json<Person[]>(r),
    ),
  myNotes: () => fetch('/api/notes/mine').then((r) => json<MyNotes>(r)),
  // Item lists (ADR 0017).
  itemLists: () => fetch('/api/item-lists').then((r) => json<ListSummary[]>(r)),
  itemList: (id: number) => fetch(`/api/item-lists/${id}`).then((r) => json<ItemList>(r)),
  lineList: (costCenter: string, account: string) =>
    fetch(`/api/item-lists/line?${new URLSearchParams({ cost_center: costCenter, account })}`).then((r) =>
      json<ItemList | null>(r),
    ),
  createList: (costCenter: string, account: string, visibility: Visibility, items: ItemRow[]) =>
    post('/api/item-lists', { cost_center: costCenter, account, visibility, items }).then((r) => json<ItemList>(r)),
  saveList: (id: number, visibility: Visibility, items: ItemRow[]) =>
    send('PUT', `/api/item-lists/${id}`, { visibility, items }).then((r) => json<ItemList>(r)),
  deleteList: (id: number) => send('DELETE', `/api/item-lists/${id}`).then((r) => json<null>(r)),
}

export const STATUS_LABEL: Record<Status, string> = {
  not_started: 'Not started',
  in_progress: 'In progress',
  submitted: 'Submitted',
  approved: 'Approved',
}
