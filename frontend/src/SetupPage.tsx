import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type MemberRow, type Version } from './api'
import PageHeader from './PageHeader'
import PlannerPreview from './PlannerPreview'
import { AccountsTab, CostCentersTab } from './ScopeTabs'
import type { Profile, Scope, SetupDraft, SetupView } from './setupTypes'
import { type Level, setupApi } from './setupShared'
import { Comparisons, EntryForms, LevelCard } from './SetupForms'
import { Profiles } from './SetupProfiles'
import { Capex } from './SetupCapex'
import { Workforce } from './SetupWorkforce'
import { CreatedFrom, ForecastActuals, NewRoundCard, PeriodFields } from './SetupRound'

// The yearly planning setup (E-03, ADR 0007): what a round plans and in what form, with a live
// preview of the planner's sheet. Edits stay a draft until saved; the save is all or nothing.

type Tab = 'forms' | 'cost_centers' | 'accounts'

const TABS: { tab: Tab; label: string }[] = [
  { tab: 'forms', label: 'Forms & columns' },
  { tab: 'cost_centers', label: 'Cost centers' },
  { tab: 'accounts', label: 'Accounts' },
]

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
      <PageHeader
        eyebrow="Yearly planning setup"
        title={creating ? 'New planning round' : round ? round.name : 'Planning rounds'}
        actions={
          !creating && (
            <button type="button" className="su-btn primary" onClick={() => setCreating(true)}>
              New round
            </button>
          )
        }
        tabs={
          !creating &&
          view &&
          draft &&
          round && (
            <div className="seg" role="tablist" aria-label="Setup">
              {TABS.map((t) => (
                <button key={t.tab} type="button" role="tab" aria-selected={tab === t.tab} aria-pressed={tab === t.tab} onClick={() => setTab(t.tab)}>
                  {t.label}
                </button>
              ))}
            </div>
          )
        }
      />

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
        <div className="su-note ok">
          Round created, in status setup: planners don’t see it yet. Set up its forms, cost centers and accounts on its
          tabs; all of it stays changeable until you open the round on the status page.
        </div>
      )}
      {!creating && note && <div className={`su-note ${note.kind}`}>{note.text}</div>}

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
                <PeriodFields
                  kind={round.kind}
                  periods={view.periods}
                  grain={draft.grain ?? 'month'}
                  through={draft.actuals_through ?? null}
                  latest={view.latest_actuals}
                  onChange={(grain, actuals_through) => setDraft({ ...draft, grain, actuals_through })}
                />
                {round.status === 'setup' && (
                  <span className="su-note warn" style={{ alignSelf: 'flex-end' }}>
                    Planners can’t enter values until the round is opened on the status page.
                  </span>
                )}
              </div>
              {round.kind === 'forecast' && (
                <ForecastActuals
                  view={view}
                  through={draft.actuals_through ?? null}
                  onRefreshed={() => void setupApi.get(view.round.code).then(setView)}
                />
              )}
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
            <Workforce
              draft={draft}
              setDraft={setDraft}
              saved={view.setup.workforce_mode ?? null}
              savedPersonnel={!!view.setup.personnel_costs}
              round={round}
              onNote={setNote}
            />
            <Capex draft={draft} setDraft={setDraft} saved={view.setup.capex != null} round={round} />
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
            forecast={round.kind === 'forecast'}
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
