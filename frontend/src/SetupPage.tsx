import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type EntryForm, type MemberRow, type Version } from './api'
import PlannerPreview, { Bars } from './PlannerPreview'
import Select, { type Option } from './Select'
import { AccountsTab, CostCentersTab } from './ScopeTabs'
import type { CarryOver, ComparisonColumn, NewRound, Profile, Rule, Scope, SetupDraft, SetupView } from './setupTypes'

// The yearly planning setup (E-03, ADR 0007): what a round plans and in what form, with a live
// preview of the planner's sheet. Edits stay a draft until saved; the save is all or nothing.

const FORMS: { form: EntryForm; label: string; explain: string }[] = [
  { form: 'monthly', label: 'Monthly', explain: 'Planners type each month. A year total they type spreads by last year’s actuals.' },
  { form: 'annual', label: 'Year only', explain: 'Planners type only the year total; months are not shown and are stored evenly.' },
  { form: 'annual_even', label: 'Year · even', explain: 'Planners type the year total; the months show it spread evenly.' },
  { form: 'annual_profile', label: 'Year · season', explain: 'Planners type the year total; it spreads by a seasonality profile.' },
  { form: 'annual_reference', label: 'Year · reference', explain: 'Planners type the year total; it spreads like last year’s actuals or another version.' },
]
const PROFILE_FORMS: EntryForm[] = ['monthly', 'annual_profile']
const REFERENCE_FORMS: EntryForm[] = ['monthly', 'annual_reference']

type Level = 'simple' | 'medium' | 'full'
type Tab = 'forms' | 'cost_centers' | 'accounts'
const TABS: { tab: Tab; label: string }[] = [
  { tab: 'forms', label: 'Forms & columns' },
  { tab: 'cost_centers', label: 'Cost centers' },
  { tab: 'accounts', label: 'Accounts' },
]
type ProfilePreset = 'working_days' | 'calendar_days'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(typeof body.detail === 'string' ? body.detail : `${response.status} ${response.statusText}`)
  }
  return (response.status === 204 ? null : response.json()) as Promise<T>
}

const setupApi = {
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
}

function levelOf(d: SetupDraft): Level | null {
  if (d.default_form === 'annual' && d.comparisons.length === 0 && d.entry_forms.length === 0) return 'simple'
  if (
    d.default_form === 'annual_even' &&
    d.comparisons.every((c) => c.kind === 'version') &&
    d.entry_forms.every((r) => r.form === 'annual_profile')
  )
    return 'medium'
  if (d.default_form === 'monthly' && d.comparisons.some((c) => c.kind === 'prior_year_actuals')) return 'full'
  return null
}

export default function SetupPage({
  rounds,
  code,
  onRoundsChanged,
}: {
  rounds: Version[]
  code: string
  onRoundsChanged: (select?: string) => void
}) {
  const [view, setView] = useState<SetupView | null>(null)
  const [draft, setDraft] = useState<SetupDraft | null>(null)
  const [accounts, setAccounts] = useState<MemberRow[]>([])
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [note, setNote] = useState<{ kind: 'ok' | 'error' | 'warn'; text: string } | null>(null)
  const [creating, setCreating] = useState(false)
  const [justCreated, setJustCreated] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [tab, setTab] = useState<Tab>('forms')
  const [scope, setScope] = useState<Scope | null>(null)

  const loadProfiles = useCallback(() => setupApi.profiles().then(setProfiles), [])

  useEffect(() => {
    void loadProfiles()
    api.members('accounts').then(setAccounts, () => setAccounts([]))
  }, [loadProfiles])

  useEffect(() => {
    if (!code) return
    setupApi.get(code).then(
      (v) => {
        setView(v)
        setDraft(v.setup)
        setNote(null)
      },
      (e: unknown) => setNote({ kind: 'error', text: String(e) }),
    )
    setupApi.scope(code).then(setScope, (e: unknown) => setNote({ kind: 'error', text: String(e) }))
  }, [code])

  const dirty = useMemo(() => !!view && !!draft && JSON.stringify(view.setup) !== JSON.stringify(draft), [view, draft])
  const versionNames = useMemo(() => Object.fromEntries(rounds.map((r) => [r.code, r.name])), [rounds])

  async function save() {
    if (!draft || !view) return
    setSaving(true)
    try {
      await setupApi.save(view.round.code, draft)
      const [fresh, freshScope] = await Promise.all([setupApi.get(view.round.code), setupApi.scope(view.round.code)])
      setView(fresh)
      setDraft(fresh.setup)
      setScope(freshScope)
      setNote({ kind: 'ok', text: 'Setup saved. Planners see it on their next load.' })
      onRoundsChanged()
    } catch (e) {
      setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setSaving(false)
    }
  }

  function applyLevel(level: Level) {
    if (!draft) return
    const topGroups = accounts.filter((a) => a.is_group && a.depth === 1).map((a) => a.code)
    switch (level) {
      case 'simple':
        setDraft({ ...draft, default_form: 'annual', comparisons: [], entry_forms: [] })
        break
      case 'medium':
        setDraft({
          ...draft,
          default_form: 'annual_even',
          comparisons: draft.comparisons.filter((c) => c.kind === 'version'),
          entry_forms: profiles.length
            ? topGroups.map((g) => ({ account_code: g, form: 'annual_profile', profile_code: profiles[0].code }))
            : [],
        })
        if (!profiles.length) setNote({ kind: 'warn', text: 'Create a seasonality profile below, then choose it per account group.' })
        break
      case 'full':
        setDraft({
          ...draft,
          default_form: 'monthly',
          comparisons: [{ kind: 'prior_year_actuals' }, { kind: 'actuals' }],
          entry_forms: [],
        })
    }
  }

  const round = view?.round
  return (
    <div className="su">
      <div className="su-head">
        <div>
          <div className="su-eyebrow">Yearly planning setup</div>
          <h2 className="su-title">{creating ? 'New planning round' : round ? `${round.name}` : 'Planning rounds'}</h2>
        </div>
        {!creating && (
          <button type="button" className="su-btn primary" onClick={() => setCreating(true)}>
            New round
          </button>
        )}
      </div>

      {/* Creating a round is a step of its own: what the new round takes from an earlier one.
          Its forms, cost centers and accounts are set up afterwards, on the round itself. */}
      {creating && (
        <NewRoundCard
          rounds={rounds}
          onCancel={() => setCreating(false)}
          onCreated={(created) => {
            setCreating(false)
            setJustCreated(created)
            onRoundsChanged(created)
          }}
        />
      )}
      {!creating && justCreated === code && (
        <div className="su-note ok" style={{ marginBottom: 14 }}>
          Round created, in status setup: planners don’t see it yet. Set up its forms, cost centers and accounts on the
          tabs below; all of it stays changeable until you open the round on the status page.
        </div>
      )}
      {!creating && note && <div className={`su-note ${note.kind}`} style={{ marginBottom: 14 }}>{note.text}</div>}

      {!creating && view && draft && round && (
        <div className="seg su-tabs" role="tablist" aria-label="Setup">
          {TABS.map((t) => (
            <button key={t.tab} type="button" role="tab" aria-selected={tab === t.tab} aria-pressed={tab === t.tab} onClick={() => setTab(t.tab)}>
              {t.label}
            </button>
          ))}
        </div>
      )}
      {!creating && view && draft && round && scope && tab !== 'forms' && (
        <div className="su-scope">
          {tab === 'cost_centers' ? (
            <CostCentersTab
              draft={draft}
              setDraft={setDraft}
              rows={scope.cost_centers}
              fiscalYear={round.fiscal_year}
              sourceName={scope.source_round ? (versionNames[scope.source_round] ?? scope.source_round) : null}
            />
          ) : (
            <AccountsTab
              draft={draft}
              setDraft={setDraft}
              rows={scope.accounts}
              fiscalYear={round.fiscal_year}
              sourceName={scope.source_round ? (versionNames[scope.source_round] ?? scope.source_round) : null}
            />
          )}
        </div>
      )}
      {!creating && view && draft && round && tab === 'forms' && (
        <div className="su-layout">
          <div className="su-col">
            <section className="su-card">
              <h3>
                <span className="su-step">1</span> Round
              </h3>
              <p className="su-hint">
                {round.kind === 'budget' ? 'Budget' : 'Forecast'} for fiscal year {round.fiscal_year} · status{' '}
                <strong>{round.status}</strong> · {view.enabled_cost_centers} cost centers to plan
              </p>
              <CreatedFrom view={view} sourceName={view.copied_from ? (versionNames[view.copied_from] ?? view.copied_from) : null} />
              <div className="su-body su-row">
                <label className="su-field">
                  Deadline
                  <input
                    className="su-input"
                    type="date"
                    value={draft.deadline ?? ''}
                    onChange={(e) => setDraft({ ...draft, deadline: e.target.value || null })}
                  />
                </label>
                {round.status === 'setup' && (
                  <span className="su-note warn" style={{ alignSelf: 'flex-end' }}>
                    Planners can’t enter values until the round is opened on the status page.
                  </span>
                )}
              </div>
            </section>

            <section className="su-card">
              <h3>
                <span className="su-step">2</span> Starting point
              </h3>
              <p className="su-hint">Pick the closest one, then adjust below. A starting point replaces the entry forms and comparisons; nothing is saved until you save.</p>
              <div className="su-body su-levels">
                <LevelCard
                  level="simple"
                  current={levelOf(draft)}
                  title="Simple"
                  text="Plan only. No actuals, a full-year value per account."
                  onPick={applyLevel}
                  icon={<rect x="3" y="8" width="18" height="8" rx="2" />}
                />
                <LevelCard
                  level="medium"
                  current={levelOf(draft)}
                  title="Medium"
                  text="Plan only, with seasonality profiles spreading the year."
                  onPick={applyLevel}
                  icon={<path d="M3 17c3-1 4-9 7-9s3 6 6 6 3-5 5-6" fill="none" strokeWidth="2" stroke="currentColor" />}
                />
                <LevelCard
                  level="full"
                  current={levelOf(draft)}
                  title="Full"
                  text={view.actuals_available ? 'Monthly, next to actuals from the source.' : 'Needs actuals: map them under Master data.'}
                  disabled={!view.actuals_available}
                  onPick={applyLevel}
                  icon={
                    <g fill="currentColor">
                      <rect x="3" y="12" width="3" height="6" rx="1" />
                      <rect x="8" y="9" width="3" height="9" rx="1" />
                      <rect x="13" y="6" width="3" height="12" rx="1" />
                      <rect x="18" y="10" width="3" height="8" rx="1" />
                    </g>
                  }
                />
              </div>
            </section>

            <EntryForms draft={draft} setDraft={setDraft} accounts={accounts} profiles={profiles} rounds={rounds} current={code} />
            <Comparisons draft={draft} setDraft={setDraft} rounds={rounds} current={code} actuals={view.actuals_available} />
            <Profiles profiles={profiles} periods={view.periods} fiscalYear={round.fiscal_year} onChanged={() => void loadProfiles()} />
          </div>

          <PlannerPreview
            draft={draft}
            accounts={accounts}
            profiles={profiles}
            versionNames={versionNames}
            roundName={round.name}
            fiscalYear={round.fiscal_year}
            periods={view.periods}
            sampleCostCenter={view.sample_cost_center}
            actualsAvailable={view.actuals_available}
          />
        </div>
      )}

      <div className={`su-savebar${dirty && !creating ? ' show' : ''}`} role="status">
        <span>Unsaved changes to {round?.name}</span>
        <button type="button" className="su-btn ghost" onClick={() => view && setDraft(view.setup)}>
          Discard
        </button>
        <button type="button" className="su-btn primary" disabled={saving} onClick={() => void save()}>
          {saving ? 'Saving…' : 'Save setup'}
        </button>
      </div>
    </div>
  )
}

function LevelCard(props: {
  level: Level
  current: Level | null
  title: string
  text: string
  icon: React.ReactNode
  disabled?: boolean
  onPick: (l: Level) => void
}) {
  return (
    <button
      type="button"
      className="su-level"
      aria-pressed={props.current === props.level}
      disabled={props.disabled}
      onClick={() => props.onPick(props.level)}
    >
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        {props.icon}
      </svg>
      <strong>{props.title}</strong>
      <span>{props.text}</span>
    </button>
  )
}

type DraftProps = { draft: SetupDraft; setDraft: (d: SetupDraft) => void }

function accountOptions(accounts: MemberRow[], groupsToo: boolean): Option[] {
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

const FORM_OPTIONS: Option<EntryForm>[] = FORMS.map((f) => ({ value: f.form, label: f.label }))

function EntryForms({
  draft,
  setDraft,
  accounts,
  profiles,
  rounds,
  current,
}: DraftProps & { accounts: MemberRow[]; profiles: Profile[]; rounds: Version[]; current: string }) {
  const explain = FORMS.find((f) => f.form === draft.default_form)?.explain
  const update = (i: number, rule: Rule) => setDraft({ ...draft, entry_forms: draft.entry_forms.map((r, j) => (j === i ? rule : r)) })
  const free = accounts.find((a) => a.is_group && !draft.entry_forms.some((r) => r.account_code === a.code))
  return (
    <section className="su-card">
      <h3>
        <span className="su-step">3</span> Entry forms
      </h3>
      <p className="su-hint">How planners enter values: a default, and exceptions per account group or account.</p>
      <div className="su-body">
        <div className="su-sub">Default</div>
        <div className="su-forms" role="group" aria-label="Default entry form">
          {FORMS.map((f) => (
            <button
              key={f.form}
              type="button"
              aria-pressed={draft.default_form === f.form}
              onClick={() => setDraft({ ...draft, default_form: f.form })}
            >
              {f.label}
            </button>
          ))}
        </div>
        <p className="su-explain">{explain}</p>
        <p className="su-hint su-form-change">
          Every form stores months. Changing a form keeps the months already stored, carried over or typed: a line
          switched to a year form shows their total, and only a year total typed afterwards is spread the new way.
        </p>
        <div className="su-sub">Exceptions · the nearest one above an account wins</div>
        <div className="su-rules">
          {draft.entry_forms.map((r, i) => (
            <div className="su-rule" key={i}>
              <Select
                label="Account or group"
                value={r.account_code}
                options={accountOptions(accounts, true).map((o) =>
                  o.value !== r.account_code && draft.entry_forms.some((other) => other.account_code === o.value)
                    ? { ...o, disabled: true, hint: 'has an exception' }
                    : o,
                )}
                searchable
                onChange={(code) => update(i, { ...r, account_code: code })}
              />
              <Select
                label="Entry form"
                value={r.form}
                options={FORM_OPTIONS}
                onChange={(form) => {
                  update(i, {
                    ...r,
                    form,
                    profile_code: PROFILE_FORMS.includes(form)
                      ? (r.profile_code ?? (form === 'annual_profile' ? (profiles[0]?.code ?? null) : null))
                      : null,
                    reference_version_code: REFERENCE_FORMS.includes(form) ? r.reference_version_code : null,
                  })
                }}
              />
              {PROFILE_FORMS.includes(r.form) && (r.form === 'annual_profile' || !r.reference_version_code) ? (
                <Select
                  label="Seasonality profile"
                  value={r.profile_code ?? ''}
                  placeholder="choose a profile"
                  options={[
                    ...(r.form === 'monthly' ? [{ value: '', label: 'by last year’s actuals' }] : []),
                    ...profiles.map((p) => ({ value: p.code, label: `season: ${p.name}` })),
                  ]}
                  onChange={(code) => update(i, { ...r, profile_code: code || null })}
                />
              ) : REFERENCE_FORMS.includes(r.form) ? (
                <Select
                  label="Spread reference"
                  value={r.reference_version_code ?? ''}
                  options={[
                    { value: '', label: 'like last year’s actuals' },
                    ...rounds.filter((v) => v.code !== current).map((v) => ({ value: v.code, label: `like ${v.name}` })),
                  ]}
                  onChange={(code) => update(i, { ...r, reference_version_code: code || null })}
                />
              ) : (
                <span />
              )}
              <button
                type="button"
                className="su-icon"
                aria-label={`Remove exception for ${r.account_code}`}
                onClick={() => setDraft({ ...draft, entry_forms: draft.entry_forms.filter((_, j) => j !== i) })}
              >
                ✕
              </button>
            </div>
          ))}
          {draft.entry_forms.length === 0 && <div className="su-empty">No exceptions: every account uses the default.</div>}
        </div>
        <div className="su-row" style={{ marginTop: 8 }}>
          <button
            type="button"
            className="su-btn"
            disabled={!free}
            onClick={() =>
              free &&
              setDraft({ ...draft, entry_forms: [...draft.entry_forms, { account_code: free.code, form: 'annual_even' }] })
            }
          >
            Add exception
          </button>
        </div>
      </div>
    </section>
  )
}

function Comparisons({
  draft,
  setDraft,
  rounds,
  current,
  actuals,
}: DraftProps & { rounds: Version[]; current: string; actuals: boolean }) {
  const label = (c: ComparisonColumn) =>
    c.kind === 'prior_year_actuals'
      ? 'Prior-year actuals'
      : c.kind === 'actuals'
        ? 'Actuals this year, to date'
        : (rounds.find((r) => r.code === c.version_code)?.name ?? c.version_code)
  const move = (i: number, by: number) => {
    const list = [...draft.comparisons]
    ;[list[i], list[i + by]] = [list[i + by], list[i]]
    setDraft({ ...draft, comparisons: list })
  }
  const has = (c: ComparisonColumn) =>
    draft.comparisons.some((x) => x.kind === c.kind && (x.version_code ?? null) === (c.version_code ?? null))
  const choices: ComparisonColumn[] = [
    ...(actuals ? ([{ kind: 'prior_year_actuals' }, { kind: 'actuals' }] as ComparisonColumn[]) : []),
    ...rounds.filter((r) => r.code !== current).map((r) => ({ kind: 'version' as const, version_code: r.code })),
  ].filter((c) => !has(c))
  return (
    <section className="su-card">
      <h3>
        <span className="su-step">4</span> Comparison columns
      </h3>
      <p className="su-hint">
        Shown before the plan, in this order. A note shows as the column’s tooltip on the sheet; actuals columns also
        say there how far their actuals go, e.g. January to June.{!actuals && ' Actuals appear here once they are mapped.'}
      </p>
      <div className="su-body">
        <ul className="su-list">
          {draft.comparisons.map((c, i) => (
            <li key={`${c.kind}-${c.version_code ?? ''}`} className="su-cmp">
              <span className="su-cmp-name">{label(c)}</span>
              <input
                className="su-input su-cmp-note"
                value={c.note ?? ''}
                maxLength={200}
                placeholder="Note to planners, e.g. January to June only"
                aria-label={`Note on ${label(c)}`}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    comparisons: draft.comparisons.map((x, j) => (j === i ? { ...x, note: e.target.value || null } : x)),
                  })
                }
              />
              <button type="button" className="su-icon" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}>
                ↑
              </button>
              <button
                type="button"
                className="su-icon"
                aria-label="Move down"
                disabled={i === draft.comparisons.length - 1}
                onClick={() => move(i, 1)}
              >
                ↓
              </button>
              <button
                type="button"
                className="su-icon"
                aria-label={`Remove ${label(c)}`}
                onClick={() => setDraft({ ...draft, comparisons: draft.comparisons.filter((_, j) => j !== i) })}
              >
                ✕
              </button>
            </li>
          ))}
          {draft.comparisons.length === 0 && <li className="su-empty">No comparison columns.</li>}
        </ul>
        <Select
          variant="action"
          label="Add a comparison column"
          display="Add a column"
          icon={<span className="pl-plus">+</span>}
          value=""
          disabled={choices.length === 0}
          options={choices.map((c, i) => ({ value: String(i), label: label(c) ?? '' }))}
          onChange={(i) => {
            const c = choices[Number(i)]
            if (c) setDraft({ ...draft, comparisons: [...draft.comparisons, c] })
          }}
        />
      </div>
    </section>
  )
}

const MONTH = new Intl.DateTimeFormat('en', { month: 'short', timeZone: 'UTC' })
const PRESETS: { preset: ProfilePreset; label: string }[] = [
  { preset: 'working_days', label: 'Working days' },
  { preset: 'calendar_days', label: 'Calendar days' },
]
const PRESET_NAME = /^(Working|Calendar) days \d{4}$/

function Profiles({
  profiles,
  periods,
  fiscalYear,
  onChanged,
}: {
  profiles: Profile[]
  periods: string[]
  fiscalYear: number
  onChanged: () => void
}) {
  // null: closed; '': a new profile; otherwise the code of the profile being edited.
  const [editing, setEditing] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [enteredAs, setEnteredAs] = useState<'percent' | 'relative'>('relative')
  const [values, setValues] = useState<string[]>(Array(12).fill('1'))
  const [presetYear, setPresetYear] = useState('')
  const [deleting, setDeleting] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const months = periods.length === 12 ? periods.map((d) => MONTH.format(new Date(d))) : []
  const numbers = values.map((v) => Number(v.replace(',', '.')))
  const total = numbers.reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0)
  const valid =
    name.trim() !== '' &&
    numbers.every((n) => Number.isFinite(n) && n >= 0) &&
    total > 0 &&
    (enteredAs === 'relative' || Math.abs(total - 100) < 1e-9)
  const year = Number(presetYear)
  const yearValid = Number.isInteger(year) && year >= 1900 && year <= 2200

  function open(p: Profile | null) {
    setEditing(p?.code ?? '')
    setName(p?.name ?? '')
    setEnteredAs(p?.entered_as ?? 'relative')
    setValues(p ? p.values.map(String) : Array(12).fill('1'))
    setPresetYear(String(fiscalYear))
    setDeleting(null)
    setError(null)
  }

  async function fill(preset: ProfilePreset, label: string) {
    try {
      const days = await setupApi.preset(preset, year)
      setValues(days.map(String))
      setEnteredAs('relative')
      if (name.trim() === '' || PRESET_NAME.test(name.trim())) setName(`${label} ${year}`)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function save() {
    const trimmed = name.trim()
    const body = { name: trimmed, entered_as: enteredAs, values: values.map((v) => v.replace(',', '.')) }
    try {
      if (editing) await setupApi.updateProfile({ ...body, code: editing })
      else await setupApi.addProfile({ ...body, code: profileCode(trimmed) })
      setEditing(null)
      setError(null)
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function remove(code: string) {
    try {
      await setupApi.deleteProfile(code)
      setDeleting(null)
      if (editing === code) setEditing(null)
      setError(null)
      onChanged()
    } catch (e) {
      setDeleting(null)
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <section className="su-card">
      <h3>
        <span className="su-step">5</span> Seasonality profiles
      </h3>
      <p className="su-hint">Shared by all rounds. Entered as percentages (summing to 100) or as any relative numbers.</p>
      <div className="su-body">
        <div className="su-profiles">
          {profiles.map((p) => (
            <div className={`su-profile${editing === p.code ? ' editing' : ''}`} key={p.code}>
              <Bars values={p.values.map(Number)} height={22} />
              <div className="meta">
                <strong>{p.name}</strong>
                <span>{p.entered_as === 'percent' ? 'percentages' : 'relative numbers'}</span>
              </div>
              {deleting === p.code ? (
                <span className="su-actions su-confirm">
                  Delete?
                  <button type="button" className="su-btn danger" onClick={() => void remove(p.code)}>
                    Delete
                  </button>
                  <button type="button" className="su-btn ghost" onClick={() => setDeleting(null)}>
                    Keep
                  </button>
                </span>
              ) : (
                <span className="su-actions">
                  <button type="button" className="su-btn ghost" aria-label={`Edit ${p.name}`} onClick={() => open(p)}>
                    Edit
                  </button>
                  <button
                    type="button"
                    className="su-btn ghost"
                    aria-label={`Delete ${p.name}`}
                    onClick={() => {
                      setDeleting(p.code)
                      setError(null)
                    }}
                  >
                    Delete
                  </button>
                </span>
              )}
            </div>
          ))}
          {profiles.length === 0 && <div className="su-empty">No profiles yet.</div>}
        </div>
        {editing === null ? (
          <button type="button" className="su-btn" style={{ marginTop: 10 }} onClick={() => open(null)}>
            New profile
          </button>
        ) : (
          <div className="su-profile-form">
            <div className="su-row">
              <label className="su-field" style={{ flex: 1 }}>
                Name
                <input className="su-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Retail with Q4 peak" />
              </label>
              <div className="su-field su-w-select">
                Entered as
                <Select
                  label="Entered as"
                  value={enteredAs}
                  options={[
                    { value: 'relative', label: 'relative numbers' },
                    { value: 'percent', label: 'percentages' },
                  ]}
                  onChange={setEnteredAs}
                />
              </div>
            </div>
            <div className="su-field su-presets">
              <label className="su-row">
                Fill with the days of fiscal year
                <input
                  className="su-input su-year"
                  inputMode="numeric"
                  aria-label="Fiscal year"
                  value={presetYear}
                  onChange={(e) => setPresetYear(e.target.value.trim())}
                />
              </label>
              <span className="su-row">
                {PRESETS.map(({ preset, label }) => (
                  <button key={preset} type="button" className="su-btn" disabled={!yearValid} onClick={() => void fill(preset, label)}>
                    {label}
                  </button>
                ))}
              </span>
              <span className="su-presets-hint">Working days leave out weekends; deduct holidays and shutdowns by hand.</span>
            </div>
            <div className="su-months">
              {values.map((v, i) => (
                <label key={i}>
                  {months[i] ?? `M${i + 1}`}
                  <input
                    inputMode="decimal"
                    aria-label={`${months[i] ?? `Month ${i + 1}`} value`}
                    value={v}
                    onChange={(e) => setValues(values.map((w, j) => (j === i ? e.target.value : w)))}
                  />
                </label>
              ))}
            </div>
            <div className="su-row" style={{ justifyContent: 'space-between' }}>
              <span className="su-row">
                <Bars values={numbers.map((n) => (Number.isFinite(n) && n > 0 ? n : 0))} height={22} />
                <span className={`su-sum ${enteredAs === 'percent' ? (Math.abs(total - 100) < 1e-9 ? 'good' : 'bad') : ''}`}>
                  sum {Number.isFinite(total) ? total.toLocaleString('en-US', { maximumFractionDigits: 4 }) : '–'}
                  {enteredAs === 'percent' && ' of 100'}
                </span>
              </span>
              <span className="su-row">
                <button type="button" className="su-btn ghost" onClick={() => setEditing(null)}>
                  Cancel
                </button>
                <button type="button" className="su-btn primary" disabled={!valid} onClick={() => void save()}>
                  {editing ? 'Save profile' : 'Create profile'}
                </button>
              </span>
            </div>
            {editing && (
              <p className="su-hint" style={{ margin: '8px 0 0' }}>
                Changes apply to year totals typed from now on; months already planned keep their values.
              </p>
            )}
          </div>
        )}
        {error && <div className="su-note error" style={{ marginTop: 8 }}>{error}</div>}
      </div>
    </section>
  )
}

function profileCode(name: string): string {
  return name.toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 20)
}

const DATE_TIME = new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short' })

/** How the round was created: from which round, by whom, and what it carried over. */
function CreatedFrom({ view, sourceName }: { view: SetupView; sourceName: string | null }) {
  const created = view.created
  if (!created) return sourceName ? <p className="su-hint">Set up from {sourceName}.</p> : null
  const when = `${created.by ? `by ${created.by} ` : ''}on ${DATE_TIME.format(new Date(created.at))}`
  if (!created.carried || !sourceName)
    return <p className="su-hint su-created">Created {when} from the defaults.</p>
  const carried = created.carried
  const uplift = Number(carried.uplift ?? 0)
  return (
    <div className="su-created">
      <div className="su-sub">
        Created from {sourceName} {when}
      </div>
      <ul className="su-carried">
        {CARRY.map((c) => {
          const on = !!carried[c.key]
          return (
            <li key={c.key} className={on ? undefined : 'off'}>
              <span aria-hidden="true">{on ? '✓' : '–'}</span>
              {c.label}
              {c.key === 'values' && on && (
                <span className="ink-3">
                  {' '}
                  · {uplift ? `${uplift > 0 ? '+' : ''}${uplift} %` : 'unchanged'}, {(created.cells ?? 0).toLocaleString('en-US')} cells
                </span>
              )}
              {!on && <span className="ink-3"> · not carried</span>}
            </li>
          )
        })}
      </ul>
      {carried.values && (
        <p className="su-hint">
          The carried values are this round’s plan values, written once at creation as one change batch: planners see
          them on their sheets when the round opens. Until then you can change them on the sheets, or clear them by
          marking accounts not planned or by account sets. Settings changed here later don’t copy them again.
        </p>
      )}
    </div>
  )
}

const CARRY: { key: keyof Omit<CarryOver, 'uplift'>; label: string; hint: string }[] = [
  { key: 'setup', label: 'Entry forms and comparisons', hint: 'default form, exceptions, comparison columns' },
  { key: 'compare_with_source', label: 'Compare with the source round', hint: 'adds it as a comparison column' },
  { key: 'scope', label: 'Cost center scope', hint: 'cost centers not planned stay so' },
  { key: 'accounts', label: 'Accounts', hint: 'accounts not planned, account sets and where they are assigned' },
  { key: 'capacity', label: 'Capacity', hint: 'productive cost centers and their plan rates and lines' },
  { key: 'sheets', label: 'Sheets', hint: 'accounts added to sheets, line spreads' },
  { key: 'values', label: 'Plan values', hint: 'moved to the new year month by month, with an uplift' },
]
const NO_CARRY: CarryOver = {
  setup: true,
  compare_with_source: true,
  scope: true,
  accounts: true,
  capacity: true,
  sheets: true,
  values: false,
  uplift: '0',
}

/** The latest round of a kind: the usual source, so budgets follow budgets and forecasts follow
 * forecasts. Rounds come newest first. */
function latestOf(rounds: Version[], kind: string): string | null {
  return (rounds.find((v) => v.kind === kind) ?? rounds[0])?.code ?? null
}

function NewRoundCard({
  rounds,
  onCancel,
  onCreated,
}: {
  rounds: Version[]
  onCancel: () => void
  onCreated: (code: string) => void
}) {
  const [r, setR] = useState<NewRound>(() => {
    const nextYear = (rounds[0]?.fiscal_year ?? new Date().getFullYear()) + 1
    return {
      code: `B${nextYear}`,
      name: `Budget ${nextYear}`,
      kind: 'budget',
      fiscal_year: nextYear,
      deadline: null,
      copy_from: latestOf(rounds, 'budget'),
      carry: NO_CARRY,
    }
  })
  const carry = r.carry ?? NO_CARRY
  const source = rounds.find((v) => v.code === r.copy_from)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  async function create() {
    setBusy(true)
    try {
      await setupApi.createRound(r)
      onCreated(r.code)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  const uplift = Number(carry.uplift ?? 0)
  return (
    <section className="su-card" style={{ marginBottom: 14 }}>
      <h3>
        <span className="su-step">1</span> Create the round
      </h3>
      <p className="su-hint">
        Choose the round it starts from and what it takes over. Next, on the round itself, you adjust its entry forms,
        comparison columns, cost centers and accounts. The round stays in setup, unseen by planners, until you open it.
      </p>
      <div className="su-row" style={{ marginTop: 10 }}>
        <div className="su-field su-w-select">
          Kind
          <Select
            label="Kind"
            value={r.kind}
            options={[
              { value: 'budget', label: 'Budget' },
              { value: 'forecast', label: 'Forecast' },
            ]}
            onChange={(kind) => setR({ ...r, kind, copy_from: latestOf(rounds, kind) })}
          />
        </div>
        <label className="su-field">
          Fiscal year
          <input
            className="su-input"
            type="number"
            value={r.fiscal_year}
            onChange={(e) => setR({ ...r, fiscal_year: Number(e.target.value) })}
            style={{ width: 90 }}
          />
        </label>
        <label className="su-field">
          Code
          <input className="su-input" value={r.code} onChange={(e) => setR({ ...r, code: e.target.value })} style={{ width: 90 }} />
        </label>
        <label className="su-field" style={{ flex: 1 }}>
          Name
          <input className="su-input" value={r.name} onChange={(e) => setR({ ...r, name: e.target.value })} />
        </label>
        <div className="su-field su-w-select">
          Set up like
          <Select
            label="Set up like"
            value={r.copy_from ?? ''}
            options={[{ value: '', label: 'the defaults' }, ...rounds.map((v) => ({ value: v.code, label: v.name, dot: v.status }))]}
            onChange={(code) => setR({ ...r, copy_from: code || null })}
          />
        </div>
      </div>
      {source && (
        <div className="su-carry">
          <div className="su-sub">Carry over from {source.name}</div>
          <ul>
            {CARRY.map((c) => (
              <li key={c.key}>
                <label>
                  <input
                    type="checkbox"
                    checked={!!carry[c.key]}
                    onChange={(e) => setR({ ...r, carry: { ...carry, [c.key]: e.target.checked } })}
                  />
                  <span>
                    {c.label} <span className="ink-3">· {c.hint}</span>
                  </span>
                </label>
                {c.key === 'values' && carry.values && (
                  <label className="su-uplift">
                    Uplift
                    <input
                      className="su-input"
                      type="number"
                      step="0.1"
                      value={carry.uplift ?? '0'}
                      onChange={(e) => setR({ ...r, carry: { ...carry, uplift: e.target.value || '0' } })}
                      aria-label="Uplift in percent"
                    />
                    %
                    <span className="ink-3">
                      {source.fiscal_year} → {r.fiscal_year}: 1,000 becomes {(1000 * (1 + uplift / 100)).toLocaleString('en-US', { maximumFractionDigits: 2 })}
                    </span>
                  </label>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="su-row" style={{ marginTop: 12, justifyContent: 'flex-end' }}>
        <button type="button" className="su-btn ghost" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className="su-btn primary" disabled={busy} onClick={() => void create()}>
          {busy ? 'Creating…' : 'Create round'}
        </button>
      </div>
      {error && <div className="su-note error" style={{ marginTop: 8 }}>{error}</div>}
    </section>
  )
}
