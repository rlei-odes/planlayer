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
export type Changes = S.ChangesOut
export type ChangeBatch = S.BatchOut
export type ChangeDetail = S.BatchDetailOut
export type ChangeLine = S.LineOut
export type ActionRequest = S.ActionIn

export type Me = S.Me
export type LoginSettings = S.LoginSettingsIO
export type LoginPage = S.LoginPageOut
export type LoginTest = S.TestOut
export type LoginStep = S.Step
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
// Capacity from the workforce (ADR 0027).
export type Staff = S.StaffOut
export type StaffPosition = S.StaffPositionOut
export type LineRef = S.LineRefIO
// Workforce planning (ADR 0020).
export type WorkforceSheet = S.WorkforceSheetOut
export type WorkforceRow = S.WorkforceRowOut
export type WorkforceEdit = S.WorkforceEditIn
export type Position = S.PositionOut
export type PositionIn = S.WorkforcePositionIn
export type Transfer = S.TransferOut
export type TransferIn = S.TransferIn
export type TransferTarget = S.TransferTargetOut
export type Home = S.HomeOut
export type RoundWork = S.RoundWorkOut
export type MyCostCenter = S.MyCostCenterOut
// Personnel costs (ADR 0022).
export type PayPage = S.PayPageOut
export type PayPosition = S.PayPositionOut
export type PayUnit = S.PayUnitOut
export type PayUnits = S.PayUnitsOut
export type PayRights = S.PayRightsOut
export type PayRightsRow = S.PayRightsRowOut
export type PayRules = S.PayRulesIO
export type PayRule = S.PayRuleIO
export type PayRulesPage = S.PayRulesPageOut
export type PayRuleRef = S.PayRuleRefOut
export type PayIn = S.PayIn
// HR's salary upload (ADR 0025).
export type SalaryUploadInfo = S.SalaryUploadInfoOut
export type SalaryUpload = S.SalaryUploadOut
export type SalaryUploadRow = S.SalaryUploadRowOut
export type SalaryFormat = 'csv' | 'xlsx' | 'parquet'
// Published signs (ADR 0019).
export type Signs = S.SignsOut
// Cost forwarding (ADR 0018).
export type ForwardingPage = S.ForwardingPageOut
export type Forwarding = S.ForwardingOut
export type ForwardingIn = S.ForwardingIn
export type NewMember = S.MemberIn
// Capex planning (ADR 0028).
export type Capex = S.CapexOut
export type CapexItem = S.ItemOut
export type CapexType = S.CapexTypeIO
export type CapexSettings = S.CapexSettingsIO
export type CapexStatus = S.ItemOut['status']
export type CapexNew = S.NewItemIn
export type CapexChange = S.ItemChangeIn
export type CapexOverview = S.CapexOverviewOut
export type CapexSums = S.SumsOut
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

/** A refusal with its HTTP status, for pages that answer one status differently (409: confirm). */
export class HttpError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function json<T>(response: Response): Promise<T> {
  if (response.status === 401) {
    window.dispatchEvent(new Event('planlayer:unauthorized'))
    throw new UnauthorizedError('not logged in')
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new HttpError(response.status, body.detail ?? `${response.status} ${response.statusText}`)
  }
  return response.json() as Promise<T>
}

async function loginJson<T>(response: Response): Promise<T> {
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
const cx = (round: string) => `/api/rounds/${encodeURIComponent(round)}/capex`

/** A line as query parameters: only the fields it has. */
function lineQuery(version: string, line: NoteLine): string {
  const params: Record<string, string> = { version, module: line.module }
  for (const key of ['cost_center', 'account', 'work_center', 'activity_type', 'investment'] as const) {
    const value = line[key]
    if (value !== null && value !== undefined && value !== '') params[key] = String(value)
  }
  return new URLSearchParams(params).toString()
}

export const api = {
  me: () => fetch('/api/me').then((res) => json<Me>(res)),
  setOwnNumberFormat: (own: S.NumberOverrideIO) => send('PUT', '/api/me/number-format', own).then((res) => json<Me>(res)),
  // Logging in (ADR 0030): a refused login answers 401 with its reason, not "logged out".
  loginOptions: () => fetch('/api/auth/options').then((res) => loginJson<S.LoginOptions>(res)),
  demoUsers: () => fetch('/api/auth/demo-users').then((res) => loginJson<S.DemoUser[]>(res)),
  demoLogin: (login: string) => post('/api/auth/demo-login', { login }).then((res) => loginJson<Me>(res)),
  passwordLogin: (login: string, password: string) =>
    post('/api/auth/login', { login, password }).then((res) => loginJson<Me>(res)),
  setup: (body: S.SetupIn) => post('/api/auth/setup', body).then((res) => loginJson<Me>(res)),
  connections: () => fetch('/api/settings/connections').then((res) => json<S.ConnectionOut[]>(res)),
  release: () => fetch('/api/settings/release').then((res) => json<S.ReleaseOut>(res)),
  sourceStatus: () => fetch('/api/source/status').then((res) => json<S.SourceStatusOut>(res)),
  loginSettings: () => fetch('/api/settings/login').then((res) => json<LoginPage>(res)),
  saveClientSecret: (secret: string | null) =>
    send('PUT', '/api/settings/login/client-secret', { secret }).then((res) => json<LoginPage>(res)),
  saveLoginSettings: (settings: LoginSettings) =>
    send('PUT', '/api/settings/login', settings).then((res) => json<LoginPage>(res)),
  testLdap: (settings: LoginSettings, login: string, password: string) =>
    post('/api/settings/login/test-ldap', { settings, login, password }).then((res) => json<LoginTest>(res)),
  testOidc: (settings: LoginSettings) =>
    post('/api/settings/login/test-oidc', { settings }).then((res) => json<S.OidcTestOut>(res)),
  oidcTestResult: (state: string) =>
    fetch(`/api/settings/login/test-oidc/${encodeURIComponent(state)}`).then((res) => json<LoginTest>(res)),
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
  setPayFromSource: (on: boolean) =>
    send('PUT', '/api/datasets/staff/pay-from-source', { on }).then((res) => json<null>(res)),
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
  setDefaultNumberFormat: (f: S.NumberFormatIO) =>
    send('PUT', '/api/settings/number-format', f).then((res) => json<S.NumberFormatIO>(res)),
  forwarding: (version: string) => fetch(fw(version)).then((res) => json<ForwardingPage>(res)),
  createForwarding: (version: string, f: ForwardingIn) => post(fw(version), f).then((res) => json<ForwardingPage>(res)),
  changeForwarding: (version: string, code: string, f: ForwardingIn) =>
    send('PUT', `${fw(version)}/${encodeURIComponent(code)}`, f).then((res) => json<ForwardingPage>(res)),
  deleteForwarding: (version: string, code: string) =>
    send('DELETE', `${fw(version)}/${encodeURIComponent(code)}`).then((res) => json<ForwardingPage>(res)),
  capex: (round: string) => fetch(cx(round)).then((res) => json<Capex>(res)),
  capexChanges: (round: string, create: CapexNew[], update: CapexChange[]) =>
    post(`${cx(round)}/changes`, { create, update }).then((res) => json<Capex>(res)),
  capexDelete: (round: string, itemIds: number[]) =>
    post(`${cx(round)}/delete`, { item_ids: itemIds }).then((res) => json<Capex>(res)),
  capexMakeProject: (round: string, itemId: number) =>
    post(`${cx(round)}/items/${itemId}/project`, {}).then((res) => json<Capex>(res)),
  capexMove: (round: string, itemId: number, projectId: number | null) =>
    send('PUT', `${cx(round)}/items/${itemId}/project`, { project_id: projectId }).then((res) => json<Capex>(res)),
  capexStatus: (round: string, itemIds: number[], status: CapexStatus, comment: string | null) =>
    post(`${cx(round)}/status`, { item_ids: itemIds, status, comment }).then((res) => json<Capex>(res)),
  capexCashOut: (round: string, itemId: number, periods: { period: string; amount: string }[]) =>
    send('PUT', `${cx(round)}/items/${itemId}/cash-out`, { periods }).then((res) => json<Capex>(res)),
  capexOverview: (round: string) => fetch(`${cx(round)}/overview`).then((res) => json<CapexOverview>(res)),
  capexTypes: () => fetch('/api/capex-types').then((res) => json<CapexType[]>(res)),
  saveCapexTypes: (types: CapexType[]) => send('PUT', '/api/capex-types', types).then((res) => json<CapexType[]>(res)),
  members: (d: MemberDataset) => fetch(`/api/members/${d}`).then((res) => json<MemberRow[]>(res)),
  addMember: (d: MemberDataset, m: NewMember) => post(`/api/members/${d}`, m).then((res) => json<null>(res)),
  changeMember: (d: MemberDataset, code: string, change: Record<string, string | null>) =>
    send('PATCH', `/api/members/${d}/${encodeURIComponent(code)}`, change).then((res) => json<null>(res)),
  resetMember: (d: MemberDataset, code: string) =>
    send('DELETE', `/api/members/${d}/${encodeURIComponent(code)}/override`).then((res) => json<null>(res)),
  deleteMember: (d: MemberDataset, code: string) =>
    send('DELETE', `/api/members/${d}/${encodeURIComponent(code)}`).then((res) => json<null>(res)),
  versions: () => fetch('/api/versions').then((r) => json<Version[]>(r)),
  /** Closing with cost centers not approved answers 409 until ``unapprovedOk``. */
  setRoundStatus: (version: string, status: RoundStatus, unapprovedOk = false) =>
    post(`${v(version)}/status`, { status, unapproved_ok: unapprovedOk }).then((r) => json<Version>(r)),
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
  capacityStaff: (version: string, code: string) => fetch(`${cap(version, code)}/workforce`).then((r) => json<Staff>(r)),
  saveStaffDefaults: (version: string, code: string, body: S.StaffDefaultsIn) =>
    send('PUT', `${cap(version, code)}/workforce`, body).then((r) => json<Staff>(r)),
  saveStaffPosition: (version: string, code: string, positionId: number, body: S.StaffPositionIn) =>
    send('PUT', `${cap(version, code)}/workforce/positions/${positionId}`, body).then((r) => json<Staff>(r)),
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
  transferTargets: (version: string, code: string) =>
    fetch(`${wf(version, code)}/transfer-targets`).then((r) => json<TransferTarget[]>(r)),
  proposeTransfer: (version: string, code: string, positionId: number, t: TransferIn) =>
    post(`${wf(version, code)}/positions/${positionId}/transfer`, t).then((r) => json<WorkforceSheet>(r)),
  decideTransfer: (id: number, accept: boolean, reason?: string) =>
    post(`/api/transfers/${id}/decision`, { accept, reason: reason || null }).then((r) => json<null>(r)),
  cancelTransfer: (id: number) => post(`/api/transfers/${id}/cancel`, {}).then((r) => json<null>(r)),
  home: () => fetch('/api/home').then((r) => json<Home>(r)),
  addPosition: (version: string, code: string, p: PositionIn) =>
    post(`${wf(version, code)}/positions`, p).then((r) => json<WorkforceSheet>(r)),
  changePosition: (version: string, code: string, id: number, p: PositionIn) =>
    send('PUT', `${wf(version, code)}/positions/${id}`, p).then((r) => json<WorkforceSheet>(r)),
  deletePosition: (version: string, code: string, id: number) =>
    send('DELETE', `${wf(version, code)}/positions/${id}`).then((r) => json<WorkforceSheet>(r)),
  staffFromHr: (version: string, code: string) => post(`${wf(version, code)}/hr`, {}).then((r) => json<WorkforceSheet>(r)),
  workforceFromHr: (version: string) => post(`${v(version)}/workforce/hr`, {}).then((r) => json<S.HrTakenOut>(r)),
  payUnits: (version: string) => fetch(`${v(version)}/personnel`).then((r) => json<PayUnits>(r)),
  payRights: (version: string) => fetch(`${v(version)}/personnel/access`).then((r) => json<PayRights>(r)),
  setPayReaders: (version: string, code: string, readers: string[], ownerDecided = false) =>
    send('PUT', `${v(version)}/personnel/access/${encodeURIComponent(code)}`, { readers, owner_decided: ownerDecided }).then((r) =>
      json<PayRights>(r),
    ),
  releasePay: (version: string, released: boolean) =>
    send('PUT', `${v(version)}/personnel/access/release`, { released }).then((r) => json<PayRights>(r)),
  payPage: (version: string, code: string) =>
    fetch(`${v(version)}/personnel/${encodeURIComponent(code)}`).then((r) => json<PayPage>(r)),
  savePay: (version: string, code: string, id: number, pay: S.PayIn) =>
    send('PUT', `${v(version)}/personnel/${encodeURIComponent(code)}/positions/${id}`, pay).then((r) => json<PayPage>(r)),
  previewPay: (version: string, code: string, id: number, pay: S.PayIn) =>
    post(`${v(version)}/personnel/${encodeURIComponent(code)}/positions/${id}/preview`, pay).then((r) =>
      json<PayPosition>(r),
    ),
  handOverPay: (version: string, code: string, handedOver: boolean) =>
    send('PUT', `${v(version)}/personnel/${encodeURIComponent(code)}/handover`, { handed_over: handedOver }).then((r) =>
      json<PayPage>(r),
    ),
  reviewPay: (version: string, code: string, confirmed: boolean) =>
    send('PUT', `${v(version)}/personnel/${encodeURIComponent(code)}/review`, { confirmed }).then((r) => json<PayPage>(r)),
  salaryUpload: (version: string) =>
    fetch(`${v(version)}/personnel/salaries`).then((r) => json<SalaryUploadInfo>(r)),
  salaryTemplate: (version: string, format: SalaryFormat) => `${v(version)}/personnel/salaries/template.${format}`,
  uploadSalaries: (version: string, file: File, step: 'check' | 'apply') =>
    fetch(`${v(version)}/personnel/salaries/${step}?${new URLSearchParams({ filename: file.name })}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: file,
    }).then((r) => json<SalaryUpload>(r)),
  payRules: (version: string) => fetch(`${v(version)}/personnel/rules`).then((r) => json<PayRulesPage>(r)),
  savePayRules: (version: string, rules: PayRules) =>
    send('PUT', `${v(version)}/personnel/rules`, rules).then((r) => json<PayRulesPage>(r)),
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
  /** The round's change batches, newest first; filters left null are off. */
  changes: (
    version: string,
    f: { kind: string; sheet: string | null; actor: string | null; cost_center: string | null; cursor: string | null },
  ) => {
    const q = new URLSearchParams(Object.entries(f).filter((e): e is [string, string] => e[1] !== null))
    return fetch(`${v(version)}/changes?${q}`).then((r) => json<Changes>(r))
  },
  changeBatch: (version: string, batch: string, costCenter: string | null) =>
    fetch(`${v(version)}/changes/${encodeURIComponent(batch)}${costCenter ? `?cost_center=${encodeURIComponent(costCenter)}` : ''}`).then(
      (r) => json<ChangeDetail>(r),
    ),
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
  /** Every list the user can see; with a round, whether each one's line is planned there. */
  itemLists: (round?: string) =>
    fetch(`/api/item-lists${round ? `?${new URLSearchParams({ round })}` : ''}`).then((r) => json<ListSummary[]>(r)),
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
  moveList: (id: number, costCenter: string, account: string) =>
    post(`/api/item-lists/${id}/move`, { cost_center: costCenter, account }).then((r) => json<ItemList>(r)),
  listOwners: (id: number) => fetch(`/api/item-lists/${id}/owners`).then((r) => json<{ login: string; name: string }[]>(r)),
  takeOverList: (id: number, login: string) => post(`/api/item-lists/${id}/owner`, { login }).then((r) => json<null>(r)),
}

export const STATUS_LABEL: Record<Status, string> = {
  not_started: 'Not started',
  in_progress: 'In progress',
  submitted: 'Submitted',
  approved: 'Approved',
}
