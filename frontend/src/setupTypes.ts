// Short names for the setup page's API types (planlayer.api.setup), from the generated schema.ts.

import type * as S from './schema'

export type Rule = S.RuleIO
export type ComparisonColumn = S.ComparisonIO
export type SetupDraft = S.SetupIO
export type SetupView = S.SetupOut
export type Profile = S.ProfileIOOutput
export type NewRound = S.RoundIn
export type Scope = S.ScopeOut
export type CarryOver = S.CarryOverInInput
export type Created = S.CreatedOut
export type WorkforceMode = NonNullable<S.SetupIO['workforce_mode']>
