import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type EntryForm, type MemberRow, type Version } from './api'
import PlannerPreview, { Bars } from './PlannerPreview'
import Select, { type Option } from './Select'
import type { ComparisonColumn, NewRound, Profile, Rule, SetupDraft, SetupView } from './setupTypes'

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
  save: (code: string, draft: SetupDraft) =>
    request<null>(`/api/rounds/${encodeURIComponent(code)}/setup`, { method: 'PUT', body: JSON.stringify(draft) }),
  createRound: (round: NewRound) => request<Version>('/api/rounds', { method: 'POST', body: JSON.stringify(round) }),
  profiles: () => request<Profile[]>('/api/profiles'),
  addProfile: (p: Profile) => request<null>('/api/profiles', { method: 'POST', body: JSON.stringify(p) }),
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
  const [saving, setSaving] = useState(false)

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
  }, [code])

  const dirty = useMemo(() => !!view && !!draft && JSON.stringify(view.setup) !== JSON.stringify(draft), [view, draft])
  const versionNames = useMemo(() => Object.fromEntries(rounds.map((r) => [r.code, r.name])), [rounds])

  async function save() {
    if (!draft || !view) return
    setSaving(true)
    try {
      await setupApi.save(view.round.code, draft)
      const fresh = await setupApi.get(view.round.code)
      setView(fresh)
      setDraft(fresh.setup)
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
          <h2 className="su-title">{round ? `${round.name}` : 'Planning rounds'}</h2>
        </div>
        <button type="button" className="su-btn primary" onClick={() => setCreating((c) => !c)}>
          New round
        </button>
      </div>

      {creating && (
        <NewRoundCard
          rounds={rounds}
          onCancel={() => setCreating(false)}
          onCreated={(created) => {
            setCreating(false)
            onRoundsChanged(created)
          }}
        />
      )}
      {note && <div className={`su-note ${note.kind}`} style={{ marginBottom: 14 }}>{note.text}</div>}

      {view && draft && round && (
        <div className="su-layout">
          <div className="su-col">
            <section className="su-card">
              <h3>
                <span className="su-step">1</span> Round
              </h3>
              <p className="su-hint">
                {round.kind === 'budget' ? 'Budget' : 'Forecast'} for fiscal year {round.fiscal_year} · status{' '}
                <strong>{round.status}</strong>
                {view.copied_from && ` · set up from ${versionNames[view.copied_from] ?? view.copied_from}`} ·{' '}
                {view.enabled_cost_centers} cost centers to plan
              </p>
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
            <HiddenAccounts draft={draft} setDraft={setDraft} accounts={accounts} />
            <Profiles profiles={profiles} onCreated={() => void loadProfiles()} />
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

      <div className={`su-savebar${dirty ? ' show' : ''}`} role="status">
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
        <div className="su-sub">Exceptions · the nearest one above an account wins</div>
        <div className="su-rules">
          {draft.entry_forms.map((r, i) => (
            <div className="su-rule" key={i}>
              <Select
                label="Account or group"
                value={r.account_code}
                options={accountOptions(accounts, true)}
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
      <p className="su-hint">Shown before the plan, in this order.{!actuals && ' Actuals appear here once they are mapped.'}</p>
      <div className="su-body">
        <ul className="su-list">
          {draft.comparisons.map((c, i) => (
            <li key={`${c.kind}-${c.version_code ?? ''}`}>
              <span className="grow">{label(c)}</span>
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

function HiddenAccounts({ draft, setDraft, accounts }: DraftProps & { accounts: MemberRow[] }) {
  const name = (code: string) => accounts.find((a) => a.code === code)?.name ?? ''
  return (
    <section className="su-card">
      <h3>
        <span className="su-step">5</span> Hidden accounts
      </h3>
      <p className="su-hint">Kept off every sheet in this round, e.g. accounts being phased out. Only possible while they hold no plan values.</p>
      <div className="su-body">
        <div className="su-chips">
          {draft.hidden_accounts.map((code) => (
            <span className="su-chip" key={code}>
              {code} {name(code)}
              <button
                type="button"
                aria-label={`Show ${code} again`}
                onClick={() => setDraft({ ...draft, hidden_accounts: draft.hidden_accounts.filter((c) => c !== code) })}
              >
                ✕
              </button>
            </span>
          ))}
          {draft.hidden_accounts.length === 0 && <span className="su-empty">None hidden.</span>}
        </div>
        <Select
          variant="action"
          label="Hide an account"
          display="Hide an account"
          icon={<span className="pl-plus">+</span>}
          value=""
          searchable
          options={accountOptions(
            accounts.filter((a) => !draft.hidden_accounts.includes(a.code)),
            false,
          )}
          onChange={(code) => setDraft({ ...draft, hidden_accounts: [...draft.hidden_accounts, code].sort() })}
        />
      </div>
    </section>
  )
}

const MONTHS = Array.from({ length: 12 }, (_, m) => new Intl.DateTimeFormat('en', { month: 'short' }).format(new Date(2026, m, 1)))

function Profiles({ profiles, onCreated }: { profiles: Profile[]; onCreated: () => void }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [enteredAs, setEnteredAs] = useState<'percent' | 'relative'>('relative')
  const [values, setValues] = useState<string[]>(Array(12).fill('1'))
  const [error, setError] = useState<string | null>(null)
  const numbers = values.map((v) => Number(v.replace(',', '.')))
  const total = numbers.reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0)
  const valid =
    name.trim() !== '' &&
    numbers.every((n) => Number.isFinite(n) && n >= 0) &&
    total > 0 &&
    (enteredAs === 'relative' || Math.abs(total - 100) < 1e-9)
  const code = name.trim().toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 20)

  async function create() {
    try {
      await setupApi.addProfile({ code, name: name.trim(), entered_as: enteredAs, values: values.map((v) => v.replace(',', '.')) })
      setOpen(false)
      setName('')
      setValues(Array(12).fill('1'))
      setError(null)
      onCreated()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <section className="su-card">
      <h3>
        <span className="su-step">6</span> Seasonality profiles
      </h3>
      <p className="su-hint">Shared by all rounds. Entered as percentages (summing to 100) or as any relative numbers.</p>
      <div className="su-body">
        <div className="su-profiles">
          {profiles.map((p) => (
            <div className="su-profile" key={p.code}>
              <Bars values={p.values.map(Number)} height={22} />
              <div className="meta">
                <strong>{p.name}</strong>
                <span>
                  {p.code} · {p.entered_as === 'percent' ? 'percentages' : 'relative numbers'}
                </span>
              </div>
            </div>
          ))}
          {profiles.length === 0 && <div className="su-empty">No profiles yet.</div>}
        </div>
        {!open ? (
          <button type="button" className="su-btn" style={{ marginTop: 10 }} onClick={() => setOpen(true)}>
            New profile
          </button>
        ) : (
          <div style={{ marginTop: 12 }}>
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
            <div className="su-months">
              {MONTHS.map((m, i) => (
                <label key={m}>
                  {m}
                  <input
                    inputMode="decimal"
                    aria-label={`${m} value`}
                    value={values[i]}
                    onChange={(e) => setValues(values.map((v, j) => (j === i ? e.target.value : v)))}
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
                <button type="button" className="su-btn ghost" onClick={() => setOpen(false)}>
                  Cancel
                </button>
                <button type="button" className="su-btn primary" disabled={!valid} onClick={() => void create()}>
                  Create profile
                </button>
              </span>
            </div>
            {error && <div className="su-note error" style={{ marginTop: 8 }}>{error}</div>}
          </div>
        )}
      </div>
    </section>
  )
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
      copy_from: rounds[0]?.code ?? null,
    }
  })
  const [error, setError] = useState<string | null>(null)
  async function create() {
    try {
      await setupApi.createRound(r)
      onCreated(r.code)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  return (
    <section className="su-card" style={{ marginBottom: 14 }}>
      <h3>New planning round</h3>
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
            onChange={(kind) => setR({ ...r, kind })}
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
      <div className="su-row" style={{ marginTop: 12, justifyContent: 'flex-end' }}>
        <button type="button" className="su-btn ghost" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className="su-btn primary" onClick={() => void create()}>
          Create round
        </button>
      </div>
      {error && <div className="su-note error" style={{ marginTop: 8 }}>{error}</div>}
    </section>
  )
}
