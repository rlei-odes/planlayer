// Generated from the API's OpenAPI schema by `planlayer api-types` (npm run gen:api).
// Do not edit: change the Pydantic models in backend/src/planlayer/api instead.

export type AccountOut = {
  code: string
  name: string
}

export type ActionIn = {
  action: "submit" | "approve" | "reject" | "reopen"
  cost_centers?: string[] | null
  node?: string | null
  comment?: string | null
}

export type AddAccountIn = {
  account_code: string
}

export type CapacityRowOut = {
  kind: "group" | "line" | "credit"
  activity_type_code: string
  activity_type_name: string
  plan: CellOut[]
  prior: (string | null)[]
  work_center_code: string | null
  work_center_name: string | null
  added: boolean
  rate: string | null
  basis: string | null
  spread: "even" | "profile" | "reference" | null
  chosen_spread: LineSpreadIO | null
  profile: string | null
  fallback: boolean
  editable: boolean
  account_code: string | null
  account_name: string | null
}

export type CapacitySheetOut = {
  version_code: string
  version_name: string
  fiscal_year: number
  round_status: "setup" | "open" | "closed"
  deadline: string | null
  cost_center_code: string
  cost_center_name: string
  status: "not_started" | "in_progress" | "submitted" | "approved"
  lock_reason: string | null
  periods: string[]
  rows: CapacityRowOut[]
  inactive: InactiveLineOut[]
  work_centers: CodeName[]
  activity_types: CodeName[]
  batch_id?: string | null
}

export type CapacityUnitOut = {
  code: string
  name: string
  status: "not_started" | "in_progress" | "submitted" | "approved"
  can_write: boolean
}

export type CellOut = {
  value: string | null
  row_version: number | null
}

export type ChangeOut = {
  kind: "added" | "changed" | "removed"
  cost_center_code: string
  account_code: string
  period: string
  old: string | null
  new: string | null
}

export type CodeName = {
  code: string
  name: string
}

export type ComparisonIO = {
  kind: "prior_year_actuals" | "actuals" | "version"
  version_code?: string | null
}

export type ComparisonOut = {
  kind: string
  label: string
}

export type ConflictOut = {
  account_code: string
  month: number
}

export type CountsOut = {
  code: string
  added: number
  changed: number
  removed: number
  unchanged: number
}

export type DatasetOut = {
  dataset: "entities" | "cost_centers" | "accounts" | "actuals" | "activity_types" | "work_centers" | "work_center_rates" | "capacity_actuals"
  fields: FieldOut[]
  has_hierarchy: boolean
  mapping: MappingIO | null
}

export type DevLoginIn = {
  login: string
}

export type DevUser = {
  login: string
  display_name: string
}

export type EditIn = {
  account_code: string
  month?: number | null
  value: string | null
  expected: (number | null)[]
}

export type EditsIn = {
  edits: EditIn[]
}

export type EnableIn = {
  enabled: boolean
}

export type FieldOut = {
  name: string
  type: string
  required: boolean
}

export type GridOut = {
  version_code: string
  version_name: string
  fiscal_year: number
  round_status: "setup" | "open" | "closed"
  deadline: string | null
  cost_center_code: string
  cost_center_name: string
  status: "not_started" | "in_progress" | "submitted" | "approved"
  lock_reason: string | null
  periods: string[]
  comparisons: ComparisonOut[]
  rows: RowOut[]
  batch_id?: string | null
}

export type HTTPValidationError = {
  detail?: ValidationError[]
}

export type Health = {
  status: "ok" | "unavailable"
  version: string
  schema_revision: string | null
}

export type HoursEditIn = {
  work_center_code: string
  activity_type_code: string
  month?: number | null
  value: string | null
  expected: (number | null)[]
}

export type HoursEditsIn = {
  edits: HoursEditIn[]
}

export type InactiveLineOut = {
  work_center_code: string
  work_center_name: string
  activity_type_code: string
  activity_type_name: string
}

export type LineChangeIn = {
  rate?: string | null
  active?: boolean | null
  spread?: LineSpreadIO | null
}

export type LineIn = {
  work_center_code: string
  activity_type_code: string
  rate: string
  new_work_center_name?: string | null
}

export type LineSpreadIO = {
  basis: "even" | "profile" | "reference"
  profile_code?: string | null
}

export type LoginIn = {
  login: string
}

export type LoginOptions = {
  dev_login: boolean
}

export type MappingIO = {
  query: string
  fields: Record<string, string>
  hierarchy?: Record<string, unknown> | null
}

export type MatchOut = {
  key: string
  dimension: string
  code: string
  name: string
}

export type Me = {
  login: string
  display_name: string
  email: string | null
  is_admin: boolean
  is_controller: boolean
  is_viewer: boolean
  reads_cost_centers: boolean
}

export type MemberChange = {
  name?: string | null
  parent?: string | null
  entity_code?: string | null
  owner?: string | null
  account_type?: string | null
  cost_center_code?: string | null
  account_code?: string | null
  variable_account_code?: string | null
}

export type MemberIn = {
  code: string
  name: string
  parent?: string | null
  is_group?: boolean
  entity_code?: string | null
  owner?: string | null
  account_type?: string | null
  cost_center_code?: string | null
  account_code?: string | null
  variable_account_code?: string | null
}

export type MemberOut = {
  code: string
  name: string
  source_name: string | null
  origin: "source" | "manual"
  is_group: boolean
  depth: number
  parent: string | null
  source_parent: string | null
  renamed: boolean
  moved: boolean
  entity_code: string | null
  owner: string | null
  account_type: string | null
  valid_from: string | null
  valid_to: string | null
  cost_center_code: string | null
  account_code: string | null
  variable_account_code: string | null
}

export type NotPlannedOut = {
  code: string
  name: string
  reason: "new" | "disabled"
}

export type OverviewOut = {
  round: Version
  rows: StatusRowOut[]
  not_planned: NotPlannedOut[]
}

export type OwnerIn = {
  login: string | null
}

export type PlanOut = {
  datasets: string[]
  problems: string[]
  new: string[]
  changed: string[]
  gone: string[]
  matches: MatchOut[]
  actual_cells: number | null
}

export type PlanningUnit = {
  code: string
  name: string
  entity_code: string | null
  owner: string | null
  status: "not_started" | "in_progress" | "submitted" | "approved"
  can_write: boolean
}

export type ProfileIOInput = {
  code: string
  name: string
  entered_as: "percent" | "relative"
  values: (number | string)[]
}

export type ProfileIOOutput = {
  code: string
  name: string
  entered_as: "percent" | "relative"
  values: string[]
}

export type PublishPreviewOut = {
  version_code: string
  scope: "approved" | "all"
  rows: number
  added: number
  changed: number
  removed: number
  unchanged: number
  cost_centers: CountsOut[]
  changes: ChangeOut[]
  fingerprint: string
}

export type PublishRunIn = {
  scope: "approved" | "all"
  fingerprint: string
}

export type PublishRunOut = {
  id: number
  scope: "approved" | "all"
  actor: string | null
  started_at: string
  finished_at: string | null
  rows: number | null
  added: number | null
  changed: number | null
  removed: number | null
}

export type QueryPreviewIn = {
  query: string
  fields: Record<string, string>
  hierarchy?: Record<string, unknown> | null
  mapped?: boolean
}

export type QueryPreviewOut = {
  columns: string[]
  rows: ((string | null)[])[]
  error?: string | null
}

export type ResponsibilitiesOut = {
  rows: ResponsibilityOut[]
  unmatched_owners: string[]
  users: string[]
}

export type ResponsibilityOut = {
  kind: "node" | "cost_center"
  code: string
  name: string
  depth: number
  owner: string | null
  matched: string | null
  planner: string | null
  overridden: boolean
  deputies: string[]
  capacity_planners: string[]
  approvers: string[]
}

export type RoundIn = {
  code: string
  name: string
  kind: "budget" | "forecast"
  fiscal_year: number
  deadline?: string | null
  copy_from?: string | null
}

export type RoundStatusIn = {
  status: "setup" | "open" | "closed"
}

export type RowOut = {
  kind: "group" | "account"
  code: string
  name: string
  group_code: string
  comparisons: ((string | null)[])[]
  plan: CellOut[]
  entry_form: "monthly" | "annual" | "annual_even" | "annual_profile" | "annual_reference" | null
  basis: string | null
  fallback: boolean
  spread: "even" | "profile" | "reference" | null
  chosen_spread: LineSpreadIO | null
  reference_version: string | null
  profile: string | null
  months_visible: boolean
  months_editable: boolean
  total_editable: boolean
  capacity: boolean
}

export type RuleIO = {
  account_code: string
  form: "monthly" | "annual" | "annual_even" | "annual_profile" | "annual_reference"
  profile_code?: string | null
  reference_version_code?: string | null
}

export type SetupIO = {
  default_form: "monthly" | "annual" | "annual_even" | "annual_profile" | "annual_reference"
  entry_forms: RuleIO[]
  comparisons: ComparisonIO[]
  hidden_accounts: string[]
  deadline: string | null
  productive_cost_centers?: string[]
}

export type SetupOut = {
  round: Version
  copied_from: string | null
  setup: SetupIO
  actuals_available: boolean
  enabled_cost_centers: number
  sample_cost_center: string | null
  periods: string[]
}

export type SpreadIn = {
  spread: LineSpreadIO | null
}

export type StatusRowOut = {
  kind: "node" | "cost_center"
  code: string
  name: string
  depth: number
  counts: Record<string, number>
  status: "not_started" | "in_progress" | "submitted" | "approved" | null
  owner: string | null
  approvers: string[]
  changed_at: string | null
  changed_by: string | null
  overdue: boolean
  actions: ("submit" | "approve" | "reject" | "reopen")[]
}

export type SyncIn = {
  entity?: string | null
}

export type SyncRunIn = {
  entity?: string | null
  confirm?: string[]
}

export type SyncRunOut = {
  members: number
  actual_rows: number
  matched: string[]
}

export type UndoIn = {
  batch_id: string
}

export type UserChange = {
  display_name?: string | null
  email?: string | null
  is_admin?: boolean | null
  is_controller?: boolean | null
  is_viewer?: boolean | null
  disabled?: boolean | null
}

export type UserIn = {
  login: string
  display_name: string
  email?: string | null
  is_admin?: boolean
  is_controller?: boolean
  is_viewer?: boolean
}

export type UserOut = {
  login: string
  display_name: string
  email: string | null
  is_admin: boolean
  is_controller: boolean
  is_viewer: boolean
  disabled: boolean
}

export type ValidationError = {
  loc: (string | number)[]
  msg: string
  type: string
  input?: unknown
  ctx?: Record<string, unknown>
}

export type Version = {
  code: string
  name: string
  kind: string
  fiscal_year: number
  status: "setup" | "open" | "closed"
  deadline: string | null
}
