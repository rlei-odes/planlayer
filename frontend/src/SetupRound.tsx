import { useEffect, useState } from 'react'
import { api, type SyncPlan, type Version } from './api'
import { closable, closedCount, fiscalPeriods, fitThrough, monthName, type Grain } from './periods'
import type { LatestActualsOut } from './schema'
import Select, { type Option } from './Select'
import type { CarryOver, NewRound, SetupView } from './setupTypes'
import { setupApi } from './setupShared'

// The round itself on the setup page (ADR 0014, ADR 0015): a new round and what it carries,
// how it was created, its periods, and a forecast's closed months.

const DATE_TIME = new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short' })

/** How the round was created: from which round, by whom, and what it carried over. */
export function CreatedFrom({ view, sourceName }: { view: SetupView; sourceName: string | null }) {
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
                  {created.hours ? `, ${created.hours.toLocaleString('en-US')} hour cells (no uplift)` : ''}
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
  { key: 'forwarding', label: 'Cost forwarding', hint: 'forwardings with their receivers, recomputed in the new round' },
  { key: 'workforce', label: 'Workforce', hint: 'positions still there in the new year, lines added to workforce sheets' },
  { key: 'values', label: 'Plan values', hint: 'moved to the new year month by month, with an uplift; planned hours, FTE and headcount too, without' },
]

const NO_CARRY: CarryOver = {
  setup: true,
  compare_with_source: true,
  scope: true,
  accounts: true,
  capacity: true,
  sheets: true,
  forwarding: true,
  workforce: true,
  values: false,
  uplift: '0',
}

/** The latest round of a kind: the usual source, so budgets follow budgets and forecasts follow
 * forecasts. Rounds come newest first. A forecast follows the year's last forecast, else the
 * year's budget (ADR 0015). */
function latestOf(rounds: Version[], kind: string, fiscalYear?: number): string | null {
  if (kind === 'forecast' && fiscalYear !== undefined) {
    const ofYear = rounds.filter((v) => v.fiscal_year === fiscalYear)
    const found = ofYear.find((v) => v.kind === 'forecast') ?? ofYear.find((v) => v.kind === 'budget')
    if (found) return found.code
  }
  return (rounds.find((v) => v.kind === kind) ?? rounds[0])?.code ?? null
}

/** How the round is planned: by month or by quarter; a forecast also closes months up to its
 * actuals through (ADR 0015). By quarter, only quarter ends can be closed. */
export function PeriodFields({
  kind,
  periods,
  grain,
  through,
  latest,
  onChange,
}: {
  kind: string
  periods: string[]
  grain: Grain
  through: string | null
  latest: string | null
  onChange: (grain: Grain, through: string | null) => void
}) {
  const months: Option[] = [
    { value: '', label: 'None', hint: 'plans the whole year' },
    ...closable(periods, grain).map((p) => {
      const n = closedCount(periods, p)
      return { value: p, label: monthName(p), hint: `${n}+${12 - n}${p === latest ? ' · latest actuals' : ''}` }
    }),
  ]
  return (
    <>
      <div className="su-field su-w-select">
        Planned by
        <Select<Grain>
          label="Planned by"
          value={grain}
          options={[
            { value: 'month', label: 'Month' },
            { value: 'quarter', label: 'Quarter', hint: 'Q1–Q4' },
          ]}
          onChange={(g) => onChange(g, fitThrough(periods, g, through))}
        />
      </div>
      {kind === 'forecast' && (
        <div className="su-field su-w-select">
          Actuals through
          <Select label="Actuals through" value={through ?? ''} options={months} onChange={(p) => onChange(grain, p || null)} />
        </div>
      )}
    </>
  )
}

/** A forecast's closed months and the actuals they show: up to when they are synced, and a
 * refresh that syncs the round's fiscal year (ADR 0015), with the sync's preview first. */
export function ForecastActuals({
  view,
  through,
  onRefreshed,
}: {
  view: SetupView
  through: string | null
  onRefreshed: () => void
}) {
  const [plan, setPlan] = useState<SyncPlan | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ kind: 'ok' | 'error' | 'warn'; text: string } | null>(null)
  const year = view.round.fiscal_year
  const years = { first: year, last: year }
  const n = closedCount(view.periods, through)
  const latest = view.latest_actuals
  const short = n > 0 && (!latest || latest < view.periods[n - 1])

  async function check() {
    setBusy(true)
    setNote(null)
    try {
      setPlan(await api.syncPlan(null, years))
    } catch (e) {
      setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  async function refresh() {
    setBusy(true)
    try {
      const done = await api.syncRun(null, [], years)
      setPlan(null)
      setNote({ kind: 'ok', text: `Actuals of ${year} refreshed: ${done.actual_rows.toLocaleString('en-US')} rows in ${done.seconds} s. The sheets show them on their next load.` })
      onRefreshed()
    } catch (e) {
      setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const counts = plan?.actual_years.find((y) => y.fiscal_year === year)
  const blocked = plan !== null && (plan.problems.length > 0 || plan.matches.length > 0)
  return (
    <div className="su-forecast">
      <p className="su-hint">
        {n > 0
          ? `${n}+${12 - n}: ${monthName(view.periods[0])} to ${monthName(view.periods[n - 1])} show actuals on the sheets, read only, as synced; planners forecast the rest. A year total typed is the full year.`
          : 'No month is closed: the forecast plans the whole year.'}{' '}
        Latest synced actuals: <strong>{latest ? monthName(latest) : 'none'}</strong>.
        {short && ' The closed months go further than the synced actuals: refresh once they are booked.'}
      </p>
      <div className="su-row">
        {plan === null ? (
          <button type="button" className="su-btn" disabled={busy} onClick={() => void check()}>
            {busy ? 'Checking the source…' : `Refresh actuals ${year}`}
          </button>
        ) : (
          <>
            <span className="su-hint">
              {counts
                ? `The source delivers ${counts.delivered.toLocaleString('en-US')} actual cells for ${year}; ${counts.stored.toLocaleString('en-US')} are stored now.`
                : `No actuals for ${year} in the source.`}
              {plan.new.length > 0 && ` New members: ${plan.new.length}.`}
              {plan.changed.length > 0 && ` Changed members: ${plan.changed.length}.`}
            </span>
            <button type="button" className="su-btn ghost" disabled={busy} onClick={() => setPlan(null)}>
              Cancel
            </button>
            <button type="button" className="su-btn primary" disabled={busy || blocked} onClick={() => void refresh()}>
              {busy ? 'Refreshing…' : 'Refresh now'}
            </button>
          </>
        )}
      </div>
      {blocked && (
        <div className="su-note warn">
          The sync needs attention first ({[...plan.problems, ...plan.matches.map((m) => `confirm ${m.dimension} ${m.code}`)].slice(0, 3).join('; ')}). Run it on Master data → Sync.
        </div>
      )}
      {note && <div className={`su-note ${note.kind}`}>{note.text}</div>}
    </div>
  )
}

export function NewRoundCard({
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
      grain: 'month',
      actuals_through: null,
    }
  })
  const carry = r.carry ?? NO_CARRY
  const source = rounds.find((v) => v.code === r.copy_from)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [latest, setLatest] = useState<LatestActualsOut | null>(null)
  useEffect(() => {
    setupApi.latestActuals().then(setLatest, (e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [])
  const startMonth = latest?.start_month ?? 1
  const periods = fiscalPeriods(r.fiscal_year, startMonth)
  const grain = r.grain ?? 'month'

  /** A forecast is named by its closed months, e.g. "Forecast 6+6 2026" (FC2026-6). */
  function named(next: NewRound): NewRound {
    if (next.kind !== 'forecast') return next
    const n = closedCount(fiscalPeriods(next.fiscal_year, startMonth), next.actuals_through)
    const year = next.fiscal_year
    return { ...next, code: n ? `FC${year}-${n}` : `FC${year}`, name: n ? `Forecast ${n}+${12 - n} ${year}` : `Forecast ${year}` }
  }

  function chooseKind(kind: NewRound['kind']) {
    if (kind === 'budget') {
      const year = (rounds[0]?.fiscal_year ?? new Date().getFullYear()) + 1
      setR({ ...r, kind, fiscal_year: year, code: `B${year}`, name: `Budget ${year}`, actuals_through: null, copy_from: latestOf(rounds, kind), carry: { ...carry, values: false } })
      return
    }
    // A forecast re-plans the year of the latest actuals, closed up to them, from its budget.
    const year = latest?.fiscal_year ?? r.fiscal_year
    const through = latest?.month ? fitThrough(fiscalPeriods(year, startMonth), grain, latest.month) : null
    setR(named({ ...r, kind, fiscal_year: year, actuals_through: through, copy_from: latestOf(rounds, kind, year), carry: { ...carry, values: true } }))
  }
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
          Round type
          <Select
            label="Round type"
            value={r.kind}
            options={[
              { value: 'budget', label: 'Budget' },
              { value: 'forecast', label: 'Forecast' },
            ]}
            onChange={chooseKind}
          />
        </div>
        <label className="su-field">
          Fiscal year
          <input
            className="su-input"
            type="number"
            value={r.fiscal_year}
            onChange={(e) => {
              const year = Number(e.target.value)
              setR(named({ ...r, fiscal_year: year, actuals_through: null, copy_from: r.kind === 'forecast' ? latestOf(rounds, r.kind, year) : r.copy_from }))
            }}
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
        <PeriodFields
          kind={r.kind}
          periods={periods}
          grain={grain}
          through={r.actuals_through ?? null}
          latest={latest?.month ?? null}
          onChange={(g, actuals_through) => setR(named({ ...r, grain: g, actuals_through }))}
        />
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
                      {closedCount(periods, r.actuals_through) > 0 && ' · into the open months only'}
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
