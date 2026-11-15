import type { EntryForm, MemberRow, Version } from './api'
import type { LatestActualsOut } from './schema'
import type { Option } from './Select'
import type { NewRound, Profile, Scope, SetupDraft, SetupView } from './setupTypes'

// Shared by the setup page and its parts (E-03): the setup API, entry forms, account options.

export const FORMS: { form: EntryForm; label: string; explain: string }[] = [
  { form: 'monthly', label: 'Monthly', explain: 'Planners type each month. A year total they type spreads by last year’s actuals.' },
  { form: 'annual', label: 'Year only', explain: 'Planners type only the year total; months are not shown and are stored evenly.' },
  { form: 'annual_even', label: 'Year · even', explain: 'Planners type the year total; the months show it spread evenly.' },
  { form: 'annual_profile', label: 'Year · season', explain: 'Planners type the year total; it spreads by a seasonality profile.' },
  { form: 'annual_reference', label: 'Year · reference', explain: 'Planners type the year total; it spreads like last year’s actuals or another version.' },
]

export const PROFILE_FORMS: EntryForm[] = ['monthly', 'annual_profile']

export const REFERENCE_FORMS: EntryForm[] = ['monthly', 'annual_reference']

export type Level = 'simple' | 'medium' | 'full'

export type ProfilePreset = 'working_days' | 'calendar_days'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(typeof body.detail === 'string' ? body.detail : `${response.status} ${response.statusText}`)
  }
  return (response.status === 204 ? null : response.json()) as Promise<T>
}

export const setupApi = {
  get: (code: string) => request<SetupView>(`/api/rounds/${encodeURIComponent(code)}/setup`),
  scope: (code: string) => request<Scope>(`/api/rounds/${encodeURIComponent(code)}/scope`),
  save: (code: string, draft: SetupDraft) =>
    request<null>(`/api/rounds/${encodeURIComponent(code)}/setup`, { method: 'PUT', body: JSON.stringify(draft) }),
  createRound: (round: NewRound) => request<Version>('/api/rounds', { method: 'POST', body: JSON.stringify(round) }),
  profiles: () => request<Profile[]>('/api/profiles'),
  addProfile: (p: Profile) => request<null>('/api/profiles', { method: 'POST', body: JSON.stringify(p) }),
  updateProfile: (p: Profile) =>
    request<null>(`/api/profiles/${encodeURIComponent(p.code)}`, { method: 'PUT', body: JSON.stringify(p) }),
  deleteProfile: (code: string) => request<null>(`/api/profiles/${encodeURIComponent(code)}`, { method: 'DELETE' }),
  preset: (preset: ProfilePreset, fiscalYear: number) =>
    request<string[]>(`/api/profile-presets/${preset}?fiscal_year=${fiscalYear}`),
  latestActuals: () => request<LatestActualsOut>('/api/actuals/latest'),
}

export type DraftProps = { draft: SetupDraft; setDraft: (d: SetupDraft) => void }

export function accountOptions(accounts: MemberRow[], groupsToo: boolean): Option[] {
  return accounts
    .filter((a) => groupsToo || !a.is_group)
    .map((a) => ({
      value: a.code,
      label: a.is_group ? a.name : `${a.code} ${a.name}`,
      hint: a.is_group ? 'group' : undefined,
      depth: groupsToo ? a.depth : 0,
      strong: a.is_group,
    }))
}

export const FORM_OPTIONS: Option<EntryForm>[] = FORMS.map((f) => ({ value: f.form, label: f.label }))
