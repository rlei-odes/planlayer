// Types of the setup page's API (planlayer.api.setup); replaced by the generated client (F-04).

import type { EntryForm, Version } from './api'

export type Rule = {
  account_code: string
  form: EntryForm
  profile_code?: string | null
  reference_version_code?: string | null
}
export type ComparisonColumn = { kind: 'prior_year_actuals' | 'actuals' | 'version'; version_code?: string | null }
export type SetupDraft = {
  default_form: EntryForm
  entry_forms: Rule[]
  comparisons: ComparisonColumn[]
  hidden_accounts: string[]
  deadline: string | null
}
export type SetupView = {
  round: Version
  copied_from: string | null
  setup: SetupDraft
  actuals_available: boolean
  enabled_cost_centers: number
  sample_cost_center: string | null
  periods: string[]
}
export type Profile = { code: string; name: string; entered_as: 'percent' | 'relative'; values: string[] }
export type NewRound = {
  code: string
  name: string
  kind: 'budget' | 'forecast'
  fiscal_year: number
  deadline: string | null
  copy_from: string | null
}
