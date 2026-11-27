// Generated from the API's OpenAPI schema by `planlayer api-types` (npm run gen:api).
// Do not edit: change the Pydantic models in backend/src/planlayer/api instead.

export type AccountIO = {
  code: string
  name: string
}

export type AccountOut = {
  code: string
  name: string
}

export type AccountSetIO = {
  name: string
  accounts: string[]
  assigned_to?: string[]
}

export type ActionIn = {
  action: "submit" | "recall" | "approve" | "reject" | "reopen"
  cost_centers?: string[] | null
  node?: string | null
  comment?: string | null
}

export type AddAccountIn = {
  account_code: string
}

export type AreaRowOut = {
  kind: "node" | "cost_center"
  code: string
  name: string
  depth: number
  sums: SumsOut
}

export type BatchDetailOut = {
  batch_id: string
  lines: LineOut[]
  total_lines: number
}

export type BatchOut = {
  batch_id: string
  at: string
  actor: PersonOut
  sheets: ("costs" | "capacity" | "workforce")[]
  sources: ("plan" | "personnel" | "forwarding" | "credit")[]
  cost_centers: CostCenterOut[]
  cost_center_count: number
  cells: number
  cleared: number
  calculated: boolean
}

export type BuildUpOut = {
  start: number
  end: number
  entering: number[]
  new: number[]
  replacements: number[]
  leaving: number[]
  moving_in: number[]
  moving_out: number[]
}

export type CapacityRowOut = {
  kind: "group" | "line" | "credit"
  activity_type_code: string
  activity_type_name: string
  plan: CellOut[]
  comparisons: ((string | null)[])[]
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
  staffed: boolean
  account_code: string | null
  account_name: string | null
  notes?: NotesMarkerOut | null
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
  comparisons: ComparisonOut[]
  rows: CapacityRowOut[]
  inactive: InactiveLineOut[]
  work_centers: CodeName[]
  activity_types: CodeName[]
  batch_id?: string | null
  version_kind: string
  grain: "month" | "quarter"
  actuals_through: string | null
  closed_months: number
  typed_hours: boolean
  staff_hours: boolean
}

export type CapacityUnitOut = {
  code: string
  name: string
  status: "not_started" | "in_progress" | "submitted" | "approved"
  can_write: boolean
  staff_hours: boolean
}

export type CapexOut = {
  round: Version
  on: boolean
  settings: CapexSettingsIO
  lock_reason: string | null
  periods: string[]
  next_year: string
  types: CapexTypeIO[]
  cost_centers: CostCenterRefOut[]
  items: ItemOut[]
  confirms: boolean
  created?: number[]
}

export type CapexOverviewOut = {
  round: Version
  on: boolean
  cash_out: boolean
  areas: AreaRowOut[]
  types: TypeRowOut[]
  total: SumsOut
  schedule: CashOutRowOut[]
  next_year: string
  waiting: number
}

export type CapexSettingsIO = {
  project_threshold?: string | null
  cash_out?: boolean
}

export type CapexTypeIO = {
  code: string
  name: string
  asset_account?: string | null
  useful_life?: number | null
  active?: boolean
  used?: boolean
}

export type CarryOverInInput = {
  setup?: boolean
  compare_with_source?: boolean
  scope?: boolean
  accounts?: boolean
  capacity?: boolean
  sheets?: boolean
  forwarding?: boolean
  workforce?: boolean
  values?: boolean
  uplift?: number | string
}

export type CarryOverInOutput = {
  setup?: boolean
  compare_with_source?: boolean
  scope?: boolean
  accounts?: boolean
  capacity?: boolean
  sheets?: boolean
  forwarding?: boolean
  workforce?: boolean
  values?: boolean
  uplift?: string
}

export type CashOutIO = {
  period: string
  amount: string
}

export type CashOutIn = {
  periods: CashOutIO[]
}

export type CashOutRowOut = {
  period: string | null
  open: string
  confirmed: string
  declined: string
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

export type ChangesIn = {
  create?: NewItemIn[]
  update?: ItemChangeIn[]
}

export type ChangesOut = {
  batches: BatchOut[]
  counts: Record<string, number>
  cursor: string | null
  users: PersonOut[]
  cost_centers: CostCenterOut[]
}

export type ClientSecretIn = {
  secret: string | null
}

export type CodeName = {
  code: string
  name: string
}

export type CodeNameOut = {
  code: string
  name: string
}

export type ComparisonIO = {
  kind: "prior_year_actuals" | "actuals" | "version" | "projection"
  version_code?: string | null
  note?: string | null
  through?: string | null
}

export type ComparisonOut = {
  kind: string
  label: string
  note: string | null
  actuals_until: string | null
  partial: boolean
}

export type ConflictOut = {
  account_code: string
  month: number
}

export type ConnectionOut = {
  role: "app" | "source" | "output"
  host: string | null
  port: number | null
  database: string | null
  user: string | null
  schema_name: string
  shared: boolean
  ok: boolean
  server?: string | null
  error?: string | null
}

export type CostCenterOut = {
  code: string
  name: string
}

export type CostCenterRefOut = {
  code: string
  name: string
  can_write: boolean
}

export type CountsOut = {
  code: string
  added: number
  changed: number
  removed: number
  unchanged: number
}

export type CreatedOut = {
  by: string | null
  at: string
  carried: CarryOverInOutput | null
  cells: number | null
  hours?: number | null
}

export type DatasetOut = {
  dataset: "entities" | "cost_centers" | "accounts" | "actuals" | "activity_types" | "work_centers" | "work_center_rates" | "capacity_actuals" | "staff_types" | "staff_categories" | "staff" | "workforce_actuals"
  fields: FieldOut[]
  has_hierarchy: boolean
  mapping: MappingIO | null
  window?: WindowOut | null
  pay_fields: string[]
  pay_from_source: boolean | null
}

export type DecisionIn = {
  accept: boolean
  reason?: string | null
}

export type DemoLoginIn = {
  login: string
}

export type DemoUser = {
  login: string
  display_name: string
}

export type EditIn = {
  account_code: string
  month?: number | null
  quarter?: number | null
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

export type ForwardedOut = {
  code: string
  name: string
  side: "charge" | "credit"
  method: "full" | "fixed" | "rate"
  source_code: string
  source_name: string
  receivers: number
}

export type ForwardingIn = {
  code: string
  name: string
  method: "full" | "fixed" | "rate"
  source_code: string
  group_code: string
  charge_account: AccountIO
  credit_account: AccountIO
  receivers: ReceiverIn[]
  shares?: "percent" | "portion" | null
  amount?: string | null
  rate?: string | null
  unit?: string | null
}

export type ForwardingOut = {
  code: string
  name: string
  method: "full" | "fixed" | "rate"
  source_code: string
  source_name: string
  shares: "percent" | "portion" | null
  amount: string | null
  rate: string | null
  unit: string | null
  group_code: string | null
  charge_account: AccountIO
  credit_account: AccountIO
  receivers: ReceiverOut[]
  passed_on: string | null
  changed_by: string | null
}

export type ForwardingPageOut = {
  version_code: string
  version_name: string
  fiscal_year: number
  round_status: "setup" | "open" | "closed"
  lock_reason: string | null
  closed_months: number
  forwardings: ForwardingOut[]
  sources: SourceOut[]
  cost_centers: CodeNameOut[]
  groups: GroupOut[]
  cost_center_tree: TreeNodeOut[]
  batch_id?: string | null
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
  version_kind: string
  grain: "month" | "quarter"
  actuals_through: string | null
  closed_months: number
  batch_id?: string | null
  returned?: ReturnedOut | null
  can_recall?: boolean
}

export type GridRowIO = {
  text: string
  amount: string | null
}

export type GroupOut = {
  code: string
  name: string
  depth: number
}

export type HTTPValidationError = {
  detail?: ValidationError[]
}

export type Health = {
  status: "ok" | "unavailable"
  version: string
  schema_revision: string | null
}

export type HomeOut = {
  tasks: number
  transfers: TransferOut[]
  rounds: RoundWorkOut[]
}

export type HoursEditIn = {
  work_center_code: string
  activity_type_code: string
  month?: number | null
  quarter?: number | null
  value: string | null
  expected: (number | null)[]
}

export type HoursEditsIn = {
  edits: HoursEditIn[]
}

export type HrTakenOut = {
  added: number
}

export type IdsIn = {
  item_ids: number[]
}

export type InactiveLineOut = {
  work_center_code: string
  work_center_name: string
  activity_type_code: string
  activity_type_name: string
}

export type ItemChangeIn = {
  item_id: number
  expected?: number | null
  values: ItemValuesIn
}

export type ItemIO = {
  kind?: "item" | "heading"
  name: string
  supplier?: string | null
  quantity?: string
  unit_price?: string | null
  amount?: string | null
  comment?: string | null
}

export type ItemListOut = {
  list_id: number
  cost_center_code: string
  cost_center_name: string
  account_code: string
  account_name: string
  owner_login: string
  owner_name: string
  visibility: "private" | "cost_center" | "public"
  total: string
  items: number
  changed_at: string
  changed_by: string
  mine: boolean
  can_edit: boolean
  entries: ItemIO[]
}

export type ItemOut = {
  item_id: number
  kind: "line" | "project"
  project_id: number | null
  cost_center_code: string
  cost_center_name: string
  name: string
  type_code: string | null
  priority: number
  start_month: string | null
  amount: string | null
  status: "open" | "confirmed" | "declined"
  status_by: string | null
  status_at: string | null
  row_version: number
  can_write: boolean
  can_confirm: boolean
  notes: NotesMarkerOut | null
  cash_out: CashOutIO[]
  scheduled: string
  above_threshold: boolean
  lines: number
  changed_by: string | null
  changed_at: string | null
}

export type ItemValuesIn = {
  name?: string | null
  cost_center?: string | null
  type?: string | null
  priority?: number | null
  start_month?: string | null
  amount?: string | null
}

export type LatestActualsOut = {
  month: string | null
  fiscal_year: number | null
  start_month: number
}

export type LdapTestIn = {
  settings: LoginSettingsIO
  login: string
  password: string
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

export type LineNotesOut = {
  threads: ThreadOut[]
  notepad: NotepadOut | null
  can_write: boolean
}

export type LineOut = {
  sheet: "costs" | "capacity" | "workforce"
  cost_center: CostCenterOut
  code: string
  name: string
  unit: "amount" | "hours" | "fte" | "headcount"
  source: "plan" | "personnel" | "forwarding" | "credit"
  months: string[]
  before: string | null
  after: string | null
}

export type LineRefIO = {
  work_center_code: string
  activity_type_code: string
}

export type LineSpreadIO = {
  basis: "even" | "profile" | "reference"
  profile_code?: string | null
}

export type ListMarkerOut = {
  list_id: number
  total: string
  visibility: "private" | "cost_center" | "public"
}

export type ListSummaryOut = {
  list_id: number
  cost_center_code: string
  cost_center_name: string
  account_code: string
  account_name: string
  owner_login: string
  owner_name: string
  visibility: "private" | "cost_center" | "public"
  total: string
  items: number
  changed_at: string
  changed_by: string
  mine: boolean
  can_edit: boolean
}

export type LoginIn = {
  login: string
}

export type LoginOptions = {
  demo: boolean
  setup: boolean
  oidc: string | null
  ldap: boolean
}

export type LoginPageOut = {
  settings: LoginSettingsIO
  from_env: boolean
  redirect_uri: string
  client_secret: "env" | "saved" | null
  problems: string[]
  suggested?: LoginSettingsIO | null
}

export type LoginSettingsIO = {
  method: "none" | "oidc" | "ldap"
  oidc_label?: string
  oidc_issuer?: string
  oidc_client_id?: string
  oidc_scopes?: string
  oidc_login_claim?: string
  oidc_email_claim?: string
  oidc_name_claim?: string
  oidc_groups_claim?: string
  oidc_hosted_domain?: string
  ldap_url?: string
  ldap_bind_dn?: string
  ldap_base_dn?: string
  ldap_login_attribute?: string
  ldap_email_attribute?: string
  ldap_name_attribute?: string
  ca_pem?: string
  allowed_group?: string
  admin_group?: string
  controller_group?: string
  viewer_group?: string
  hr_group?: string
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
  is_hr: boolean
  reads_cost_centers: boolean
  reads_pay: boolean
  number_format: NumberFormatIO
  number_default: NumberFormatIO
  number_own: NumberOverrideIO
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
  external?: boolean | null
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
  external?: boolean | null
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
  missing_since?: string | null
  external?: boolean | null
}

export type MoveIn = {
  project_id: number | null
}

export type MyCostCenterOut = {
  code: string
  name: string
  status: "not_started" | "in_progress" | "submitted" | "approved"
  overdue: boolean
  sheets: ("capacity" | "workforce" | "personnel" | "planning")[]
  returned?: ReturnedOut | null
}

export type MyNotesOut = {
  tasks: number
  threads: MyThreadOut[]
}

export type MyThreadOut = {
  thread_id: number
  reason: "assigned" | "mentioned" | "started"
  module: "cost_center" | "capacity" | "capex"
  cost_center_code: string
  cost_center_name: string
  line_code: string
  line_name: string
  round_code: string
  first: string
  last_author: string
  last_at: string
  assignee: string | null
  notes: number
}

export type NewItemIn = {
  kind?: "line" | "project"
  project_id?: number | null
  cost_center?: string | null
  name?: string
  type?: string | null
  priority?: number | null
  start_month?: string | null
  amount?: string | null
}

export type NewListIn = {
  cost_center: string
  account: string
  visibility?: "private" | "cost_center" | "public"
  items?: ItemIO[]
}

export type NotPlannedOut = {
  code: string
  name: string
  reason: "new" | "disabled"
}

export type NoteEditIn = {
  version: string
  body: string
  mentions?: string[]
}

export type NoteIn = {
  version: string
  body: string
  mentions?: string[]
  assign?: string | null
}

export type NoteLineIn = {
  module: "cost_center" | "capacity" | "capex"
  cost_center?: string | null
  account?: string | null
  work_center?: string | null
  activity_type?: string | null
  investment?: number | null
}

export type NoteOut = {
  note_id: number
  author: PersonOut
  body: string
  created_at: string
  edited_at: string | null
  mentions: PersonOut[]
  mine: boolean
}

export type NotepadIn = {
  version: string
  line: NoteLineIn
  rows: GridRowIO[]
}

export type NotepadOut = {
  rows: GridRowIO[]
  total: string
  changed_at: string
  changed_by: PersonOut
  carried_from: string | null
}

export type NotesMarkerOut = {
  open: number
  resolved: number
  task_for_me: boolean
  notepad: boolean
}

export type NumberFormatIO = {
  separators: "comma_dot" | "dot_comma" | "apostrophe_dot" | "space_comma"
  decimals: boolean
  scale: "full" | "k" | "M"
  units: boolean
}

export type NumberOverrideIO = {
  separators?: "comma_dot" | "dot_comma" | "apostrophe_dot" | "space_comma" | null
  decimals?: boolean | null
  scale?: "full" | "k" | "M" | null
  units?: boolean | null
}

export type OidcTestIn = {
  settings: LoginSettingsIO
}

export type OidcTestOut = {
  url: string
  state: string
}

export type OverviewOut = {
  round: Version
  rows: StatusRowOut[]
  not_planned: NotPlannedOut[]
}

export type OwnerIn = {
  login: string | null
}

export type PasswordLoginIn = {
  login: string
  password: string
}

export type PayAccountOut = {
  code: string
  name: string
  months: (string | null)[]
  small: number | null
}

export type PayFromSourceIn = {
  on: boolean
}

export type PayHandoverIn = {
  handed_over: boolean
}

export type PayIn = {
  salary?: string | null
  bonus?: string | null
  charged_to?: string | null
  hr_salary?: string | null
  excluded_rules?: number[] | null
}

export type PayLogOut = {
  at: string
  actor: string | null
  action: string
  round_code: string | null
  cost_center: string | null
  user: string | null
}

export type PayPageOut = {
  round_code: string
  round_name: string
  round_status: "setup" | "open" | "closed"
  fiscal_year: number
  cost_center_code: string
  cost_center_name: string
  status: "not_started" | "in_progress" | "submitted" | "approved"
  lock_reason: string | null
  periods: string[]
  closed_months: number
  min_group: number
  positions: PayPositionOut[]
  accounts: PayAccountOut[]
  review: PayReviewOut | null
  may_review: boolean
  may_charge: boolean
  charge_targets: CodeName[]
  released: boolean
  handover: PayReviewOut | null
  review_required: boolean
  rules: PayRuleRefOut[]
  bonus_account: string | null
  batch_id?: string | null
}

export type PayPartOut = {
  source: "pay" | "bonus" | "rule"
  rule_id: number | null
  year: string
}

export type PayPersonOut = {
  login: string
  name: string
}

export type PayPositionOut = {
  position_id: number
  code: string
  title: string
  person_name: string | null
  staff_type: string
  staff_category: string
  entry_date: string | null
  leave_date: string | null
  contract_percent: string | null
  is_plan: boolean
  typed: boolean | null
  hr_salary: string | null
  salary: string | null
  bonus: string | null
  charged_to: string | null
  year_cost: string
  transferred_to: string | null
  entity: string
  salary_account: string | null
  excluded_rules: number[]
  parts: PayPartOut[]
}

export type PayReadersIn = {
  readers: string[]
  owner_decided?: boolean
}

export type PayReleaseIn = {
  released: boolean
}

export type PayReviewIn = {
  confirmed: boolean
}

export type PayReviewOut = {
  confirmed_by: string
  confirmed_at: string
}

export type PayRightsOut = {
  round_code: string
  round_status: "setup" | "open" | "closed"
  released_by: string | null
  released_at: string | null
  rows: PayRightsRowOut[]
  users: PayPersonOut[]
  log: PayLogOut[]
}

export type PayRightsRowOut = {
  code: string
  name: string
  readers: PayPersonOut[]
  suggested: string[]
  source_owner: PayPersonOut | null
  proposal: boolean
}

export type PayRuleIO = {
  id?: number | null
  kind: "extra" | "raise" | "award" | "contribution"
  name: string
  account: string
  entity?: string | null
  salaries?: string | null
  percent?: string | null
  ceiling?: string | null
  starts?: string | null
  steps: PayStepIO[]
}

export type PayRuleRefOut = {
  id: number
  kind: "extra" | "raise" | "award" | "contribution"
  name: string
  account: string
  entity: string | null
}

export type PayRulesIO = {
  min_group: number
  review_required: boolean
  bonus_account: string | null
  staff_types: PayStaffTypeIO[]
  rules: PayRuleIO[]
}

export type PayRulesPageOut = {
  round_code: string
  round_status: "setup" | "open" | "closed"
  fiscal_year: number
  workforce_positions: boolean
  on: boolean
  may_edit: boolean
  periods: string[]
  rules: PayRulesIO
  accounts: CodeName[]
  entities: CodeName[]
}

export type PayStaffTypeIO = {
  code: string
  name?: string
  external?: boolean
  account: string | null
  typed: boolean
}

export type PayStepIO = {
  years: number
  salaries?: string | null
  amount?: string | null
}

export type PayUnitOut = {
  code: string
  name: string
  status: "not_started" | "in_progress" | "submitted" | "approved"
  positions: number
  small_groups: number
  confirmed: boolean
  handed_over: boolean
}

export type PayUnitsOut = {
  released: boolean
  units: PayUnitOut[]
}

export type PersonOut = {
  login: string
  name: string
}

export type PlanOut = {
  datasets: string[]
  problems: string[]
  new: string[]
  changed: string[]
  gone: string[]
  matches: MatchOut[]
  actual_cells: number | null
  years: YearsIO | null
  actual_years: YearCountOut[]
}

export type PlanningUnit = {
  code: string
  name: string
  entity_code: string | null
  owner: string | null
  status: "not_started" | "in_progress" | "submitted" | "approved"
  can_write: boolean
}

export type PositionOut = {
  position_id: number
  code: string | null
  title: string
  person_name: string | null
  staff_type_code: string
  staff_category_code: string
  entry_date: string | null
  leave_date: string | null
  contract_percent: string | null
  is_plan: boolean
  is_replacement: boolean
  description: string | null
  counted: boolean[]
  elsewhere: boolean[]
  changed_by: string | null
  origin: "hr" | "manual"
  removable: boolean
  transferred_from: string | null
  transferred_to: string | null
  transfer: TransferOut | null
  transferable: boolean
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
  hours_rows: number
  workforce_rows: number
  capex_rows: number
  cash_out_rows: number
  capex_changed: boolean
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

export type ReceiverIn = {
  cost_center_code: string
  quantity: string
}

export type ReceiverOut = {
  cost_center_code: string
  cost_center_name: string
  quantity: string
  percent: string | null
  year: string | null
  status: "not_started" | "in_progress" | "submitted" | "approved"
}

export type ReleaseOut = {
  current: string
  latest?: string | null
  notes_url?: string | null
  upgrade_url?: string
}

export type ResolveIn = {
  version: string
  resolved: boolean
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

export type ReturnedOut = {
  how: "rejected" | "reopened"
  by: string
  at: string
  comment: string
}

export type RoundIn = {
  code: string
  name: string
  kind: "budget" | "forecast"
  fiscal_year: number
  deadline?: string | null
  copy_from?: string | null
  carry?: CarryOverInInput
  grain?: "month" | "quarter"
  actuals_through?: string | null
}

export type RoundStatusIn = {
  status: "setup" | "open" | "closed"
  unapproved_ok?: boolean
}

export type RoundWorkOut = {
  round: Version
  cost_centers: MyCostCenterOut[]
  to_approve: number
  counts: Record<string, number> | null
  small_groups: string[]
  to_review: string[]
  pay_released: boolean | null
  pay_step: "release" | "hand_over" | null
  to_hand_over: string[]
  to_confirm?: number
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
  forwarded?: ForwardedOut | null
  personnel?: boolean
  credit?: boolean
  not_planned?: "round" | "set" | null
  notes?: NotesMarkerOut | null
  item_list?: ListMarkerOut | null
}

export type RuleIO = {
  account_code: string
  form: "monthly" | "annual" | "annual_even" | "annual_profile" | "annual_reference"
  profile_code?: string | null
  reference_version_code?: string | null
}

export type SalaryUploadInfoOut = {
  round_code: string
  round_status: "setup" | "open" | "closed"
  pay_from_source: boolean
  people: number
  with_salary: number
  reason: string | null
}

export type SalaryUploadOut = {
  round_code: string
  applied: boolean
  rows: SalaryUploadRowOut[]
  ignored: string[]
  positions: number
  staff_list: number
  batch_id?: string | null
}

export type SalaryUploadRowOut = {
  line: number
  number: string | null
  name: string | null
  cost_center: string | null
  file_cost_center: string | null
  old: string | null
  new: string | null
  outcome: "changed" | "unchanged" | "blank" | "not_in_round" | "unknown" | "failed"
  message: string | null
  old_bonus: string | null
  new_bonus: string | null
}

export type SaveListIn = {
  visibility: "private" | "cost_center" | "public"
  items: ItemIO[]
}

export type ScopeOut = {
  source_round: string | null
  cost_centers: ScopeRowOut[]
  accounts: ScopeRowOut[]
}

export type ScopeRowOut = {
  code: string
  name: string
  depth: number
  is_group: boolean
  parent: string | null
  planned: boolean | null
  new: boolean
  missing: boolean
  prior_actuals: string | null
  source_plan: string | null
}

export type SetupIO = {
  default_form: "monthly" | "annual" | "annual_even" | "annual_profile" | "annual_reference"
  entry_forms: RuleIO[]
  comparisons: ComparisonIO[]
  not_planned_accounts: string[]
  deadline: string | null
  capacity_work_centers?: string[]
  capacity_workforce?: string[]
  fte_hours?: string | null
  planned_cost_centers?: string[] | null
  account_sets?: AccountSetIO[]
  grain?: "month" | "quarter"
  actuals_through?: string | null
  workforce_mode?: "fte" | "headcount" | "both" | "positions" | null
  personnel_costs?: boolean
  capex?: CapexSettingsIO | null
}

export type SetupIn = {
  code: string
  login: string
  display_name: string
  email?: string | null
  password: string
}

export type SetupOut = {
  round: Version
  copied_from: string | null
  created: CreatedOut | null
  setup: SetupIO
  actuals_available: boolean
  enabled_cost_centers: number
  sample_cost_center: string | null
  periods: string[]
  latest_actuals: string | null
}

export type SignsIn = {
  convention: "ledger" | "result"
}

export type SignsOut = {
  convention: "ledger" | "result"
  cost_accounts: number
  revenue_accounts: number
  credit_accounts: number
  untyped: CodeNameOut[]
  credits: CodeNameOut[]
}

export type SourceOut = {
  cost_center_code: string
  cost_center_name: string
  costs: string | null
  other_credits: string | null
  passed_on: string | null
  remaining: string | null
}

export type SourceStatusOut = {
  ok: boolean
  database?: string | null
  user?: string | null
  server?: string | null
  schemas?: string[]
  error?: string | null
}

export type SpreadIn = {
  spread: LineSpreadIO | null
}

export type StaffDefaultsIn = {
  line: LineRefIO | null
  percent: string | null
}

export type StaffLineOut = {
  work_center_code: string
  work_center_name: string
  activity_type_code: string
  activity_type_name: string
  rate: string
  hours: string | null
  typed: boolean
}

export type StaffOut = {
  cost_center_code: string
  cost_center_name: string
  lock_reason: string | null
  fte_hours: string | null
  typed_hours: boolean
  default_work_center_code: string | null
  default_activity_type_code: string | null
  default_percent: string | null
  lines: StaffLineOut[]
  positions: StaffPositionOut[]
  batch_id?: string | null
}

export type StaffPositionIn = {
  line: LineRefIO | null
  percent: string | null
  hours?: string | null
}

export type StaffPositionOut = {
  position_id: number
  code: string
  title: string
  person_name: string | null
  staff_type: string
  entry_date: string | null
  leave_date: string | null
  contract_percent: string | null
  productive_percent: string | null
  typed_hours: string | null
  work_center_code: string | null
  activity_type_code: string | null
  hours: string | null
}

export type StaffTypeOut = {
  code: string
  name: string
  external: boolean
}

export type StatusIn = {
  item_ids: number[]
  status: "open" | "confirmed" | "declined"
  comment?: string | null
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
  actions: ("submit" | "recall" | "approve" | "reject" | "reopen")[]
}

export type StatusSumsOut = {
  open: string
  confirmed: string
  declined: string
}

export type Step = {
  label: string
  ok: boolean | null
  detail: string
}

export type SumsOut = {
  by_priority: StatusSumsOut[]
  count: number
}

export type SyncIn = {
  entity?: string | null
  years?: YearsIO | null
}

export type SyncRunIn = {
  entity?: string | null
  years?: YearsIO | null
  confirm?: string[]
}

export type SyncRunOut = {
  members: number
  actual_rows: number
  matched: string[]
  seconds: number
}

export type SyncYearsOut = {
  default: WindowOut | null
  years: YearOut[]
}

export type TestOut = {
  steps: Step[]
  ok: boolean
  env: string | null
  groups?: string[]
}

export type ThreadIn = {
  version: string
  body: string
  mentions?: string[]
  assign?: string | null
  line: NoteLineIn
}

export type ThreadOut = {
  thread_id: number
  notes: NoteOut[]
  assignee: PersonOut | null
  resolved_at: string | null
  resolved_by: PersonOut | null
  carried_from: string | null
}

export type TransferIn = {
  to_cost_center: string
  kind: "full" | "during_year"
  starts?: string | null
  note?: string | null
}

export type TransferOut = {
  transfer_id: number
  version_code: string
  version_name: string
  status: "proposed" | "accepted" | "declined" | "cancelled"
  kind: "full" | "during_year"
  starts: string | null
  position_id: number
  position_code: string
  title: string
  person_name: string | null
  staff_type_name: string
  staff_category_name: string
  contract_percent: string | null
  from_code: string
  from_name: string
  to_code: string
  to_name: string
  note: string | null
  proposed_by: string
  proposed_at: string
  decided_by: string | null
  decided_at: string | null
  reason: string | null
  can_decide: boolean
  can_cancel: boolean
}

export type TransferTargetOut = {
  code: string
  name: string
  status: "not_started" | "in_progress" | "submitted" | "approved"
  editable: boolean
}

export type TreeNodeOut = {
  code: string
  name: string
  depth: number
  is_group: boolean
}

export type TypeRowOut = {
  code: string | null
  name: string
  sums: SumsOut
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
  is_hr?: boolean | null
  disabled?: boolean | null
}

export type UserIn = {
  login: string
  display_name: string
  email?: string | null
  is_admin?: boolean
  is_controller?: boolean
  is_viewer?: boolean
  is_hr?: boolean
}

export type UserOut = {
  login: string
  display_name: string
  email: string | null
  is_admin: boolean
  is_controller: boolean
  is_viewer: boolean
  is_hr: boolean
  disabled: boolean
  owns: number
  deputy: number
  local: boolean
  provider: "oidc" | "ldap" | null
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
  grain?: "month" | "quarter"
  actuals_through?: string | null
}

export type WindowOut = {
  first: number
  last: number
  period_from: string
  before: string
}

export type WorkforceEditIn = {
  staff_type_code: string
  staff_category_code: string
  measure: "fte" | "headcount"
  month?: number | null
  quarter?: number | null
  value: string | null
  expected: (number | null)[]
}

export type WorkforceEditsIn = {
  edits: WorkforceEditIn[]
}

export type WorkforceLineIn = {
  staff_type_code: string
  staff_category_code: string
}

export type WorkforcePositionIn = {
  title: string
  staff_type_code: string
  staff_category_code: string
  person_name?: string | null
  entry_date?: string | null
  leave_date?: string | null
  contract_percent?: string | null
  is_plan?: boolean
  is_replacement?: boolean
  description?: string | null
}

export type WorkforceRowOut = {
  kind: "line" | "group" | "total"
  measure: "fte" | "headcount"
  staff_type_code: string | null
  staff_type_name: string | null
  staff_category_code: string | null
  staff_category_name: string | null
  external: boolean
  plan: CellOut[]
  comparisons: ((string | null)[])[]
  editable: boolean
}

export type WorkforceSheetOut = {
  version_code: string
  version_name: string
  fiscal_year: number
  round_status: "setup" | "open" | "closed"
  deadline: string | null
  cost_center_code: string
  cost_center_name: string
  status: "not_started" | "in_progress" | "submitted" | "approved"
  lock_reason: string | null
  mode: "fte" | "headcount" | "both" | "positions"
  periods: string[]
  comparisons: ComparisonOut[]
  rows: WorkforceRowOut[]
  version_kind: string
  grain: "month" | "quarter"
  actuals_through: string | null
  closed_months: number
  staff_types: StaffTypeOut[]
  staff_categories: CodeName[]
  positions: PositionOut[]
  build_up: BuildUpOut | null
  hr_available: number
  incoming: TransferOut[]
  batch_id?: string | null
}

export type YearCountOut = {
  fiscal_year: number
  stored: number
  delivered: number
}

export type YearOut = {
  fiscal_year: number
  stored: number
}

export type YearsIO = {
  first: number
  last: number
}
