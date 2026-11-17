import { useCallback, useEffect, useState } from 'react'
import {
  api,
  type Dataset,
  type DatasetInfo,
  type Hierarchy,
  type Mapping,
  type Me,
  type MemberDataset,
  type MemberRow,
  type Preview,
  type SyncPlan,
  type SyncYears,
} from './api'
import FilterBar, { FilterSearch } from './FilterBar'
import PageHeader from './PageHeader'
import Select, { type Option } from './Select'
import Tile from './Tile'

// Master data (ADR 0012): the admin's query per dataset with named columns (Sources), the sync
// preview with confirmation of matches (Sync), and the member tree with manual members and
// local overrides (Members), which also builds master data from scratch.

type Section = 'sources' | 'sync' | 'members'
type Note = { kind: 'ok' | 'error' | 'warn'; text: string }

const SECTION_LABEL: Record<Section, string> = { sources: 'Sources', sync: 'Sync', members: 'Members' }
const DATASET_LABEL: Record<Dataset, string> = {
  entities: 'Entities',
  cost_centers: 'Cost centers',
  accounts: 'Accounts',
  actuals: 'Actuals',
  activity_types: 'Activity types',
  work_centers: 'Work centers',
  work_center_rates: 'Work center rates',
  capacity_actuals: 'Work center actual hours',
  staff_types: 'Staff types',
  staff_categories: 'Staff categories',
  staff: 'Staff (HR)',
  workforce_actuals: 'Workforce actuals',
}
// Datasets grouped by the module that needs them, so the admin sees what a setup requires.
const DATASET_GROUPS: { title: string; hint: string; datasets: Dataset[] }[] = [
  {
    title: 'Master data',
    hint: 'Every planning module plans on these. Members only grow: one the source stops delivering is kept.',
    datasets: ['entities', 'cost_centers', 'accounts'],
  },
  {
    title: 'Cost center planning',
    hint: 'Actuals for the comparison columns and as the reference for spreading year totals.',
    datasets: ['actuals'],
  },
  {
    title: 'Capacity planning',
    hint: 'Only needed when productive cost centers plan their work centers’ hours.',
    datasets: ['activity_types', 'work_centers', 'work_center_rates', 'capacity_actuals'],
  },
  {
    title: 'Workforce planning',
    hint: 'Only needed when a round plans FTE, headcount or positions. Staff types come seeded (Employee, Apprentice, Temporary, Freelancer); map them only if HR has its own.',
    datasets: ['staff_types', 'staff_categories', 'staff', 'workforce_actuals'],
  },
]
// Values per period: synced for a window of fiscal years, chosen on Sync.
const PERIOD_DATASETS: Dataset[] = ['actuals', 'capacity_actuals', 'workforce_actuals']
const DIMENSIONS: Option<MemberDataset>[] = [
  { value: 'cost_centers', label: 'Cost centers' },
  { value: 'accounts', label: 'Accounts' },
  { value: 'entities', label: 'Entities' },
  { value: 'work_centers', label: 'Work centers' },
  { value: 'activity_types', label: 'Activity types' },
  { value: 'staff_types', label: 'Staff types' },
  { value: 'staff_categories', label: 'Staff categories' },
]
const ATTRIBUTE_HEAD: Partial<Record<MemberDataset, string>> = {
  accounts: 'Type',
  cost_centers: 'Entity · owner',
  work_centers: 'Cost center',
  activity_types: 'Credited account',
  staff_types: 'Kind',
}

function attributes(dataset: MemberDataset, r: MemberRow): string {
  switch (dataset) {
    case 'accounts':
      return r.account_type ?? ''
    case 'work_centers':
      return r.cost_center_code ?? ''
    case 'activity_types':
      return [r.account_code, r.variable_account_code && `variable: ${r.variable_account_code}`].filter(Boolean).join(' · ')
    case 'staff_types':
      return r.external ? 'external (contract % optional)' : 'internal'
    case 'staff_categories':
      return ''

    default:
      return [r.entity_code, r.owner].filter(Boolean).join(' · ')
  }
}

function errorText(e: unknown) {
  return e instanceof Error ? e.message : String(e)
}

export default function MasterDataPage({ me }: { me: Me }) {
  const sections: Section[] = [...(me.is_admin ? (['sources'] as const) : []), 'sync', 'members']
  const [section, setSection] = useState<Section>(sections[0])
  return (
    <div className="su md">
      <PageHeader
        eyebrow="Master data"
        title={SECTION_LABEL[section]}
        sub={
          section === 'sources'
            ? 'Where each dataset comes from: one query per dataset, run read-only on the source.'
            : section === 'sync'
              ? 'Bring members and actuals in from the source, after a preview of what changes.'
              : 'The members planners see, synced or entered here, and their hierarchy.'
        }
        actions={
          <div className="seg" role="group" aria-label="Section">
            {sections.map((s) => (
              <button key={s} type="button" aria-pressed={section === s} onClick={() => setSection(s)}>
                {SECTION_LABEL[s]}
              </button>
            ))}
          </div>
        }
      />
      {section === 'sources' && <Sources />}
      {section === 'sync' && <Sync />}
      {section === 'members' && <Members />}
    </div>
  )
}

// --- Sources ------------------------------------------------------------------------------------

function Sources() {
  const [datasets, setDatasets] = useState<DatasetInfo[]>([])
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => {
    api.datasets().then(setDatasets, (e: unknown) => setError(errorText(e)))
  }, [])
  useEffect(load, [load])
  return (
    <div className="su-col">
      {error && <div className="su-note error">{error}</div>}
      <div className="su-note">
        Name which result column is which field. Every dataset is optional: without a source, enter members by hand
        under Members.
      </div>
      {DATASET_GROUPS.map((g) => (
        <div className="md-group" key={g.title}>
          <div className="md-group-head">
            <h3>{g.title}</h3>
            <span className="ink-3">{g.hint}</span>
          </div>
          {datasets
            .filter((d) => g.datasets.includes(d.dataset))
            .map((d) => (
              <DatasetEditor key={d.dataset} info={d} onSaved={load} />
            ))}
        </div>
      ))}
    </div>
  )
}

const EMPTY: Mapping = { query: '', fields: {}, hierarchy: null }

/** A mapping as saved: unmapped fields dropped, fields in a fixed order, so an edit and its undo
 * compare equal. */
function normal(m: Mapping): string {
  const fields = Object.fromEntries(
    Object.entries(m.fields)
      .filter(([, c]) => c)
      .sort(([a], [b]) => a.localeCompare(b)),
  )
  return JSON.stringify({ query: m.query.trim(), fields, hierarchy: m.hierarchy })
}

// What a field must hold, where the type alone doesn't say.
const FIELD_HINT: Record<string, string> = {
  period: 'any day counts as its month',
  valid_from: 'a date, a timestamp or text like 2026-01-01',
  valid_to: 'a date, a timestamp or text like 2026-12-31',
}

const DAY = new Intl.DateTimeFormat('en-CA', { timeZone: 'UTC' }) // 2026-01-01

function DatasetEditor({ info, onSaved }: { info: DatasetInfo; onSaved: () => void }) {
  const [open, setOpen] = useState(!info.mapping)
  const [m, setM] = useState<Mapping>(info.mapping ?? EMPTY)
  const dirty = normal(m) !== normal(info.mapping ?? EMPTY)
  const [columns, setColumns] = useState<string[]>(() => columnsOf(info.mapping))
  const [preview, setPreview] = useState<Preview | null>(null)
  const [note, setNote] = useState<Note | null>(null)

  async function run(mapped: boolean) {
    try {
      const p = await api.preview(info.dataset, m, mapped)
      setPreview(p)
      if (!mapped && !p.error) setColumns(p.columns)
      setNote(p.error ? { kind: 'error', text: p.error } : null)
    } catch (e) {
      setNote({ kind: 'error', text: errorText(e) })
    }
  }

  async function save() {
    try {
      await api.saveMapping(info.dataset, m)
      setNote({ kind: 'ok', text: 'Mapping saved. The next sync reads it.' })
      onSaved()
    } catch (e) {
      setNote({ kind: 'error', text: errorText(e) })
    }
  }

  const columnOptions = (value: string): Option[] => [
    { value: '', label: '–  not mapped' },
    ...[...new Set([...columns, value].filter(Boolean))].map((c) => ({ value: c, label: c })),
  ]
  const choose = (value: string, onChange: (v: string) => void, label: string, disabled = false) => (
    <Select
      value={value}
      options={columnOptions(value)}
      onChange={onChange}
      label={label}
      placeholder="–  not mapped"
      disabled={disabled}
    />
  )
  // Staff (ADR 0025): pay is read from the source only while the admin switches it on.
  const payOff = info.pay_from_source === false
  async function switchPay(on: boolean) {
    try {
      await api.setPayFromSource(on)
      setNote({
        kind: 'ok',
        text: on
          ? 'Salaries from the source. The next sync replaces HR’s salaries.'
          : 'Salaries by HR upload. The sync no longer reads them.',
      })
      onSaved()
    } catch (e) {
      setNote({ kind: 'error', text: errorText(e) })
    }
  }
  const h = m.hierarchy
  const setH = (next: Hierarchy | null) => setM({ ...m, hierarchy: next })
  const mappedCount = info.fields.filter((f) => m.fields[f.name]).length

  return (
    <section className="su-card md-dataset">
      <button type="button" className="md-dataset-head" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <strong>{DATASET_LABEL[info.dataset]}</strong>
        <span className={`su-pill ${info.mapping ? 'approved' : 'neutral'}`}>{info.mapping ? 'Mapped' : 'Not mapped'}</span>
        {info.pay_from_source !== null && (
          <span className="su-pill neutral">{info.pay_from_source ? 'Salaries from the source' : 'Salaries by HR'}</span>
        )}
        {dirty && <span className="su-pill in_progress">Unsaved changes</span>}
        <span className="ink-3">
          {mappedCount} of {info.fields.length} fields
        </span>
        <svg className="md-chev" width="12" height="8" viewBox="0 0 10 6" aria-hidden="true">
          <path d="m1 1 4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <div className="md-dataset-body">
          <label className="su-field">
            Query
            <textarea
              className="su-input md-query"
              aria-label={`Query for ${info.dataset}`}
              rows={4}
              value={m.query}
              placeholder="SELECT … FROM …"
              spellCheck={false}
              onChange={(e) => setM({ ...m, query: e.target.value })}
            />
          </label>
          {PERIOD_DATASETS.includes(info.dataset) && <PeriodFilter info={info} column={m.fields.period ?? ''} />}
          <div className="su-row">
            <button type="button" className="su-btn" disabled={!m.query.trim()} onClick={() => void run(false)}>
              Show columns
            </button>
            <span className="ink-3">
              {columns.length ? `${columns.length} columns found` : 'Run the query once to choose its columns below.'}
            </span>
          </div>

          {info.pay_from_source !== null && (
            <label className={`wf-mode md-pay${info.pay_from_source ? ' on' : ''}`}>
              <input type="checkbox" checked={info.pay_from_source} onChange={(e) => void switchPay(e.target.checked)} />
              <span>
                <strong>Salaries from the source</strong>
                <span className="su-hint">
                  {info.pay_from_source
                    ? `The sync reads ${info.pay_fields.join(', ')}.`
                    : `Off: HR uploads salaries on the Personnel costs page.`}
                </span>
              </span>
            </label>
          )}
          <div className="md-fields">
            {info.fields.map((f) => (
              <div className={`md-field${payOff && info.pay_fields.includes(f.name) ? ' md-off' : ''}`} key={f.name}>
                <span className="md-field-name">
                  {f.name}
                  {f.required && <span className="md-req" title="required">*</span>}
                  <span className="md-type" title={FIELD_HINT[f.name]}>
                    {f.type}
                  </span>
                  {FIELD_HINT[f.name] && <span className="md-field-hint">{FIELD_HINT[f.name]}</span>}
                  {payOff && info.pay_fields.includes(f.name) && (
                    <span className="md-field-hint">not read</span>
                  )}
                </span>
                {choose(
                  m.fields[f.name] ?? '',
                  (v) => setM({ ...m, fields: { ...m.fields, [f.name]: v } }),
                  `${info.dataset} ${f.name}`,
                  payOff && info.pay_fields.includes(f.name),
                )}
              </div>
            ))}
            {info.has_hierarchy && (
              <div className="md-field md-hier">
                <span className="md-field-name">hierarchy</span>
                <div>
                  <div className="seg" role="group" aria-label={`${info.dataset} hierarchy form`}>
                    <button type="button" aria-pressed={!h} onClick={() => setH(null)}>
                      None
                    </button>
                    <button type="button" aria-pressed={!!h && 'parent' in h} onClick={() => setH({ parent: '' })}>
                      Parent column
                    </button>
                    <button
                      type="button"
                      aria-pressed={!!h && 'levels' in h}
                      onClick={() => setH({ levels: [{ code: '', name: '' }] })}
                    >
                      Level columns
                    </button>
                  </div>
                  {h && 'parent' in h && (
                    <div className="md-levels">{choose(h.parent, (v) => setH({ parent: v }), `${info.dataset} parent`)}</div>
                  )}
                  {h && 'levels' in h && (
                    <div className="md-levels">
                      {h.levels.map((lv, i) => (
                        <div className="md-level" key={i}>
                          <span className="ink-3">Level {i + 1}</span>
                          {choose(
                            lv.code,
                            (v) => setH({ levels: h.levels.map((x, j) => (j === i ? { ...x, code: v } : x)) }),
                            `level ${i + 1} code`,
                          )}
                          {choose(
                            lv.name,
                            (v) => setH({ levels: h.levels.map((x, j) => (j === i ? { ...x, name: v } : x)) }),
                            `level ${i + 1} name`,
                          )}
                          <button
                            type="button"
                            className="su-icon"
                            aria-label={`Remove level ${i + 1}`}
                            onClick={() => setH({ levels: h.levels.filter((_, j) => j !== i) })}
                          >
                            ✕
                          </button>
                        </div>
                      ))}
                      <button
                        type="button"
                        className="su-btn sm"
                        onClick={() => setH({ levels: [...h.levels, { code: '', name: '' }] })}
                      >
                        Add level
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

          <div className="su-row md-actions">
            <button type="button" className="su-btn" disabled={!m.query.trim()} onClick={() => void run(true)}>
              Preview as synced
            </button>
            <button type="button" className="su-btn primary" disabled={!dirty} onClick={() => void save()}>
              Save mapping
            </button>
            <span className="ink-3">{dirty ? 'Unsaved changes: the sync reads the saved mapping.' : info.mapping ? 'Saved.' : ''}</span>
            {info.mapping && (
              <button
                type="button"
                className="su-btn ghost danger"
                onClick={() => void api.deleteMapping(info.dataset).then(onSaved, (e: unknown) => setNote({ kind: 'error', text: errorText(e) }))}
              >
                Remove mapping
              </button>
            )}
          </div>
          {note && <div className={`su-note ${note.kind}`}>{note.text}</div>}
          {preview && !preview.error && (
            <div className="tbl-card">
              <div className="tbl-scroll md-preview">
                <table className="tbl">
                  <thead>
                    <tr>
                      {preview.columns.map((c) => (
                        <th key={c}>{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.rows.map((r, i) => (
                      <tr key={i}>
                        {r.map((v, j) => (
                          <td key={j}>{v ?? <span className="ink-3">null</span>}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

/** The filter the sync adds to a query of values per period, on the column mapped to period. */
function PeriodFilter({ info, column }: { info: DatasetInfo; column: string }) {
  const w = info.window
  const col = column || '‹period column›'
  return (
    <div className="su-hint md-period-hint">
      Synced for a window of fiscal years; the years outside it stay as stored. Leave the years out of the query: the
      sync adds the filter on the column mapped to <strong>period</strong>, and the warehouse applies it.
      {w ? (
        <>
          {' '}
          With the default window, fiscal years {w.first} to {w.last}:
          <pre className="md-filter">
            {`SELECT … FROM (your query) q\nWHERE ${col} >= '${DAY.format(new Date(w.period_from))}' AND ${col} < '${DAY.format(new Date(w.before))}'`}
          </pre>
          Other years are chosen on Sync.
        </>
      ) : (
        ' No round is in setup or open, so a sync replaces every year the query delivers.'
      )}
    </div>
  )
}

function columnsOf(m: Mapping | null): string[] {
  if (!m) return []
  const h = m.hierarchy
  const fromH = !h ? [] : 'parent' in h ? [h.parent] : h.levels.flatMap((lv) => [lv.code, lv.name])
  return [...new Set([...Object.values(m.fields), ...fromH].filter(Boolean))]
}

// --- Sync ---------------------------------------------------------------------------------------

/** A long step in progress: what it does, and for how long it has been running. */
function Working({ text, since }: { text: string; since: number }) {
  const [now, setNow] = useState(since)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  return (
    <div className="su-note info md-working" role="status">
      <span className="md-spinner" aria-hidden="true" />
      <span>{text}</span>
      <span className="md-elapsed">{Math.floor((now - since) / 1000)} s</span>
    </div>
  )
}

/** The years chosen, or null for the default window; one chosen alone is a window of one year. */
function yearsOf(first: string, last: string): { years: { first: number; last: number } | null; error?: string } {
  if (!first && !last) return { years: null }
  const a = Number(first || last)
  const b = Number(last || first)
  return a <= b ? { years: { first: a, last: b } } : { years: null, error: 'The first year comes after the last one.' }
}

function Sync() {
  const [entity, setEntity] = useState('')
  const [first, setFirst] = useState('')
  const [last, setLast] = useState('')
  const [plan, setPlan] = useState<SyncPlan | null>(null)
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set())
  const [note, setNote] = useState<Note | null>(null)
  const [busy, setBusy] = useState(false)
  // What is running and since when, for the working panel.
  const [working, setWorking] = useState<{ text: string; since: number } | null>(null)
  const [years, setYears] = useState<SyncYears | null>(null)
  useEffect(() => {
    api.syncYears().then(setYears, (e: unknown) => setNote({ kind: 'error', text: errorText(e) }))
  }, [])
  const def = years?.default ?? null
  const yearOptions = (which: 'from' | 'to'): Option[] => [
    {
      value: '',
      label: def ? `${which === 'from' ? def.first : def.last} · default` : 'every year',
    },
    ...(years?.years ?? []).map((y) => ({
      value: String(y.fiscal_year),
      label: String(y.fiscal_year),
      hint: y.stored ? `${y.stored.toLocaleString('en-US')} cells stored` : 'none stored',
    })),
  ]

  async function preview() {
    const asked = yearsOf(first, last)
    if (asked.error) {
      setNote({ kind: 'error', text: asked.error })
      return
    }
    setBusy(true)
    setWorking({ text: 'Reading every mapped dataset from the source…', since: Date.now() })
    try {
      const p = await api.syncPlan(entity || null, asked.years)
      setPlan(p)
      // Show the window the preview used, so a run syncs exactly what was previewed.
      setFirst(p.years ? String(p.years.first) : '')
      setLast(p.years ? String(p.years.last) : '')
      setConfirmed(new Set())
      setNote(null)
    } catch (e) {
      setNote({ kind: 'error', text: errorText(e) })
    } finally {
      setBusy(false)
      setWorking(null)
    }
  }

  async function runSync() {
    setBusy(true)
    const cells = plan?.actual_cells
    setWorking({
      text:
        `Syncing: reading the source again, then writing members` +
        (cells ? ` and ${cells.toLocaleString('en-US')} actual cells` : '') +
        ' in one transaction. Nothing changes until it is done.',
      since: Date.now(),
    })
    setNote(null)
    try {
      const r = await api.syncRun(entity || null, [...confirmed], plan?.years ?? null)
      setNote({
        kind: 'ok',
        text: `Synced ${r.members.toLocaleString('en-US')} members and ${r.actual_rows.toLocaleString('en-US')} actual cells in ${Math.max(1, Math.round(r.seconds))} s.`,
      })
      setPlan(null)
    } catch (e) {
      setNote({ kind: 'error', text: errorText(e) })
    } finally {
      setBusy(false)
      setWorking(null)
    }
  }

  const unconfirmed = plan ? plan.matches.filter((m) => !confirmed.has(m.key)).length : 0
  return (
    <div className="su-col">
      <section className="su-card">
        <h3>
          <span className="su-step">1</span> Sync filter
        </h3>
        <p className="su-hint">
          Members (cost centers, accounts, …) are always read in full. Actuals and work center hours are replaced only
          for the fiscal years chosen here and stay as they are for every other year.{' '}
          {def
            ? `By default these are ${def.first} to ${def.last}: the years the rounds in setup or open compare with, their own year and the year before.`
            : 'No round is in setup or open, so by default every year is replaced.'}{' '}
          The preview reads the source and writes nothing.
        </p>
        <div className="su-body su-row">
          <label className="su-field">
            Actuals of entity
            <input
              className="su-input md-entity"
              value={entity}
              onChange={(e) => setEntity(e.target.value)}
              placeholder="all entities"
            />
          </label>
          <div className="su-field">
            Actuals of fiscal years
            <span className="md-years">
              <Select
                label="First fiscal year"
                value={first}
                options={yearOptions('from')}
                onChange={(v) => {
                  setFirst(v)
                  setPlan(null)
                }}
              />
              <span className="ink-3">to</span>
              <Select
                label="Last fiscal year"
                value={last}
                options={yearOptions('to')}
                onChange={(v) => {
                  setLast(v)
                  setPlan(null)
                }}
              />
            </span>
          </div>
          <button type="button" className="su-btn md-push" disabled={busy} onClick={() => void preview()}>
            {busy && !plan ? 'Reading the source…' : plan ? 'Preview again' : 'Preview sync'}
          </button>
        </div>
      </section>

      {working && <Working text={working.text} since={working.since} />}
      {note && <div className={`su-note ${note.kind}`}>{note.text}</div>}

      {plan && (
        <section className="su-card">
          <h3>
            <span className="su-step">2</span> Sync preview
          </h3>
          <p className="su-hint">Datasets: {plan.datasets.join(', ') || 'none mapped'}</p>
          <div className="su-body">
            <div className="tiles md-tiles">
              <Tile label="new" value={plan.new.length.toLocaleString('en-US')} tone="add" />
              <Tile label="changed" value={plan.changed.length.toLocaleString('en-US')} tone="chg" />
              <Tile label="no longer delivered" value={plan.gone.length.toLocaleString('en-US')} tone="rem" />
              <Tile label="actual cells" value={(plan.actual_cells ?? 0).toLocaleString('en-US')} tone="same" />
            </div>
            {plan.actual_cells !== null && <ActualYears plan={plan} />}
            {plan.problems.length > 0 && (
              <div className="su-note error md-gap">
                <strong>
                  {plan.problems.length} problem{plan.problems.length === 1 ? '' : 's'}: nothing can be synced until they are
                  fixed in the source or the mapping.
                </strong>
                <ul className="md-list">
                  {plan.problems.slice(0, 50).map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              </div>
            )}
            {plan.matches.length > 0 && (
              <div className="su-note warn md-gap">
                <strong>
                  Found {plan.matches.length} match{plan.matches.length === 1 ? '' : 'es'} from the source.
                </strong>{' '}
                Confirm each: the source takes the member over, and its manual data is kept as a shadow record.
                <ul className="md-list md-checks">
                  {plan.matches.map((m) => (
                    <li key={m.key}>
                      <label>
                        <input
                          type="checkbox"
                          checked={confirmed.has(m.key)}
                          onChange={(e) => {
                            const next = new Set(confirmed)
                            if (e.target.checked) next.add(m.key)
                            else next.delete(m.key)
                            setConfirmed(next)
                          }}
                        />
                        {m.dimension} <span className="code">{m.code}</span>“{m.name}”
                      </label>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <Changes label="New" items={plan.new} />
            <Changes label="Changed · was → becomes" items={plan.changed} open />
            <Changes label="No longer delivered (kept with their values)" items={plan.gone} />
            <div className="su-row md-actions">
              <button
                type="button"
                className="su-btn primary"
                disabled={busy || plan.problems.length > 0 || unconfirmed > 0}
                onClick={() => void runSync()}
              >
                {busy ? 'Syncing…' : 'Run sync'}
              </button>
              {unconfirmed > 0 && <span className="ink-3">Confirm the matches above first.</span>}
            </div>
          </div>
        </section>
      )}
    </div>
  )
}

/** Actual cells per fiscal year of the window: stored now and delivered, with years the run would empty. */
function ActualYears({ plan }: { plan: SyncPlan }) {
  const emptied = plan.actual_years.filter((y) => y.stored > 0 && y.delivered === 0)
  const fmt = (n: number) => n.toLocaleString('en-US')
  return (
    <div className="md-gap">
      <p className="md-window">
        {plan.years
          ? `Actuals of fiscal years ${plan.years.first} to ${plan.years.last} are replaced; other years stay as they are.`
          : 'No round in setup or open: actuals of every year are replaced.'}
      </p>
      {plan.actual_years.length > 0 && (
        <div className="tbl-card md-years-table">
          <table className="tbl">
            <thead>
              <tr>
                <th>Fiscal year</th>
                <th className="num">Stored now</th>
                <th className="num">From the source</th>
                <th className="num">Difference</th>
              </tr>
            </thead>
            <tbody>
              {plan.actual_years.map((y) => (
                <tr key={y.fiscal_year} className={y.stored > 0 && y.delivered === 0 ? 'md-emptied' : undefined}>
                  <td>{y.fiscal_year}</td>
                  <td className="num">{fmt(y.stored)}</td>
                  <td className="num">{fmt(y.delivered)}</td>
                  <td className="num">{y.delivered - y.stored > 0 ? '+' : ''}{fmt(y.delivered - y.stored)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {emptied.length > 0 && (
        <div className="su-note warn">
          The source delivers no actuals for {emptied.map((y) => y.fiscal_year).join(', ')}: the sync would remove the{' '}
          {fmt(emptied.reduce((n, y) => n + y.stored, 0))} cells stored. If the query names a fixed year, narrow the
          years above instead.
        </div>
      )}
    </div>
  )
}

function Changes({ label, items, open = false }: { label: string; items: string[]; open?: boolean }) {
  if (items.length === 0) return null
  return (
    <details className="md-changes" open={open && items.length <= 20}>
      <summary>
        {label} <span className="ink-3">{items.length}</span>
      </summary>
      <ul className="md-list">
        {items.slice(0, 200).map((i) => (
          <li key={i}>{i}</li>
        ))}
        {items.length > 200 && <li className="ink-3">… and {items.length - 200} more</li>}
      </ul>
    </details>
  )
}

// --- Members ------------------------------------------------------------------------------------

const BLANK = {
  code: '',
  name: '',
  parent: '',
  is_group: false,
  entity_code: '',
  owner: '',
  account_type: '',
  cost_center_code: '',
  account_code: '',
  external: false,
}

function Members() {
  const [dataset, setDataset] = useState<MemberDataset>('cost_centers')
  const [rows, setRows] = useState<MemberRow[]>([])
  const [note, setNote] = useState<Note | null>(null)
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<{ code: string; name: string } | null>(null)
  const [draft, setDraft] = useState(BLANK)

  const load = useCallback(() => {
    api.members(dataset).then(setRows, (e: unknown) => setNote({ kind: 'error', text: errorText(e) }))
  }, [dataset])
  useEffect(load, [load])

  async function run(task: () => Promise<unknown>, done?: string) {
    try {
      await task()
      setNote(done ? { kind: 'ok', text: done } : null)
      load()
      return true
    } catch (e) {
      setNote({ kind: 'error', text: errorText(e) })
      return false
    }
  }

  const tree = dataset === 'cost_centers' || dataset === 'accounts'
  const groups = rows.filter((r) => r.is_group)
  const parentOptions = (except?: string): Option[] => [
    { value: '', label: '(top level)' },
    ...groups.filter((g) => g.code !== except).map((g) => ({ value: g.code, label: g.name, hint: g.code, depth: g.depth })),
  ]
  const q = query.trim().toLowerCase()
  const shown = q ? rows.filter((r) => `${r.code} ${r.name} ${r.owner ?? ''}`.toLowerCase().includes(q)) : rows
  const manual = rows.filter((r) => r.origin === 'manual').length
  const changed = rows.filter((r) => r.renamed || r.moved).length

  return (
    <div className="su-col">
      <FilterBar>
        <div className="seg" role="group" aria-label="Dimension">
          {DIMENSIONS.map((d) => (
            <button
              key={d.value}
              type="button"
              aria-pressed={dataset === d.value}
              onClick={() => {
                setDataset(d.value)
                setQuery('')
                setEditing(null)
              }}
            >
              {d.label}
            </button>
          ))}
        </div>
        <FilterSearch value={query} onChange={setQuery} placeholder="Filter by code, name or owner" label="Filter members" />
        <span className="ink-3 md-counts">
          {rows.length} members · {manual} entered here · {changed} changed here
        </span>
        <button type="button" className="su-btn primary" onClick={() => setAdding((a) => !a)}>
          Add member
        </button>
      </FilterBar>

      {adding && (
        <form
          className="su-card md-add"
          onSubmit={(e) => {
            e.preventDefault()
            void run(
              () =>
                api.addMember(dataset, {
                  code: draft.code,
                  name: draft.name,
                  parent: draft.parent || null,
                  is_group: draft.is_group,
                  entity_code: draft.entity_code || null,
                  owner: draft.owner || null,
                  account_type: draft.account_type || null,
                  cost_center_code: draft.cost_center_code || null,
                  account_code: draft.account_code || null,
                  external: dataset === 'staff_types' ? draft.external : null,
                }),
              `${draft.code} added.`,
            ).then((ok) => ok && setDraft({ ...draft, code: '', name: '' }))
          }}
        >
          <h3>New member</h3>
          <div className="su-row md-add-row">
            <label className="su-field">
              Code
              <input className="su-input md-w-code" value={draft.code} onChange={(e) => setDraft({ ...draft, code: e.target.value })} required />
            </label>
            <label className="su-field md-grow">
              Name
              <input className="su-input" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} required />
            </label>
            {tree && (
              <div className="su-field md-w-parent">
                Parent
                <Select value={draft.parent} options={parentOptions()} onChange={(v) => setDraft({ ...draft, parent: v })} label="Parent" />
              </div>
            )}
            {dataset === 'cost_centers' && (
              <>
                <label className="su-field">
                  Entity
                  <input className="su-input md-w-code" value={draft.entity_code} onChange={(e) => setDraft({ ...draft, entity_code: e.target.value })} />
                </label>
                <label className="su-field md-grow">
                  Owner (e-mail)
                  <input className="su-input" value={draft.owner} onChange={(e) => setDraft({ ...draft, owner: e.target.value })} />
                </label>
              </>
            )}
            {dataset === 'work_centers' && (
              <label className="su-field">
                Cost center
                <input
                  className="su-input md-w-code"
                  value={draft.cost_center_code}
                  onChange={(e) => setDraft({ ...draft, cost_center_code: e.target.value })}
                />
              </label>
            )}
            {dataset === 'activity_types' && (
              <label className="su-field">
                Credited account
                <input
                  className="su-input md-w-code"
                  value={draft.account_code}
                  onChange={(e) => setDraft({ ...draft, account_code: e.target.value })}
                />
              </label>
            )}
            {dataset === 'accounts' && (
              <div className="su-field md-w-parent">
                Type
                <Select
                  value={draft.account_type}
                  options={[
                    { value: '', label: 'none' },
                    { value: 'cost', label: 'cost' },
                    { value: 'revenue', label: 'revenue' },
                  ]}
                  onChange={(v) => setDraft({ ...draft, account_type: v })}
                  label="Account type"
                />
              </div>
            )}
          </div>
          <div className="su-row md-add-foot">
            {dataset === 'staff_types' && (
              <label className="md-check">
                <input type="checkbox" checked={draft.external} onChange={(e) => setDraft({ ...draft, external: e.target.checked })} />
                External staff, e.g. freelancers: the contract percentage is optional
              </label>
            )}
            {tree && (
              <label className="md-check">
                <input type="checkbox" checked={draft.is_group} onChange={(e) => setDraft({ ...draft, is_group: e.target.checked })} />
                A group: holds other members
              </label>
            )}
            <span className="md-grow" />
            <button type="button" className="su-btn ghost" onClick={() => setAdding(false)}>
              Cancel
            </button>
            <button type="submit" className="su-btn primary">
              Add member
            </button>
          </div>
        </form>
      )}

      {note && <div className={`su-note ${note.kind}`}>{note.text}</div>}

      <div className="tbl-card">
        <div className="tbl-scroll">
          <table className="tbl">
            <thead>
              <tr>
                <th>Member</th>
                <th>Origin</th>
                {tree && <th>Parent</th>}
                <th>{ATTRIBUTE_HEAD[dataset] ?? ''}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.code} className={r.is_group ? 'node' : undefined}>
                  <td style={{ paddingLeft: 14 + (q ? 0 : r.depth * 18) }}>
                    {editing?.code === r.code ? (
                      <form
                        className="md-rename"
                        onSubmit={(e) => {
                          e.preventDefault()
                          void run(() => api.changeMember(dataset, r.code, { name: editing.name }), `${r.code} renamed.`).then(
                            (ok) => ok && setEditing(null),
                          )
                        }}
                      >
                        <span className="code">{r.code}</span>
                        <input
                          className="su-input"
                          value={editing.name}
                          onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                          onKeyDown={(e) => e.key === 'Escape' && setEditing(null)}
                          aria-label={`New name of ${r.code}`}
                          autoFocus
                        />
                        <button type="submit" className="su-btn primary sm">
                          Save
                        </button>
                        <button type="button" className="su-btn ghost sm" onClick={() => setEditing(null)}>
                          Cancel
                        </button>
                      </form>
                    ) : (
                      <span>
                        <span className="code">{r.code}</span>
                        {r.name}
                        {r.renamed && <span className="md-was"> was “{r.source_name}”</span>}
                      </span>
                    )}
                  </td>
                  <td>
                    <span className="btn-row md-left">
                      <span className={`su-pill ${r.origin === 'manual' ? 'submitted' : 'neutral'}`}>
                        {r.origin === 'manual' ? 'Entered here' : 'Source'}
                      </span>
                      {(r.renamed || r.moved) && <span className="su-pill in_progress">Changed here</span>}
                    </span>
                  </td>
                  {tree && (
                    <td className="md-parent">
                      <Select
                        variant="compact"
                        value={r.parent ?? ''}
                        label={`Parent of ${r.code}`}
                        options={parentOptions(r.code)}
                        searchable
                        onChange={(v) => void run(() => api.changeMember(dataset, r.code, { parent: v || null }), `${r.code} moved.`)}
                      />
                      {r.moved && <span className="md-was">source: {r.source_parent ?? 'top level'}</span>}
                    </td>
                  )}
                  <td className="muted">{attributes(dataset, r)}</td>
                  <td className="right">
                    <span className="btn-row">
                      <button type="button" className="su-btn ghost sm" onClick={() => setEditing({ code: r.code, name: r.name })}>
                        Rename
                      </button>
                      {(r.renamed || r.moved) && (
                        <button
                          type="button"
                          className="su-btn ghost sm"
                          onClick={() => void run(() => api.resetMember(dataset, r.code), `${r.code} follows the source again.`)}
                        >
                          Back to source
                        </button>
                      )}
                      {r.origin === 'manual' && (
                        <button
                          type="button"
                          className="su-btn ghost sm danger"
                          onClick={() => void run(() => api.deleteMember(dataset, r.code), `${r.code} deleted.`)}
                        >
                          Delete
                        </button>
                      )}
                    </span>
                  </td>
                </tr>
              ))}
              {shown.length === 0 && (
                <tr>
                  <td colSpan={5} className="st-empty">
                    {rows.length === 0
                      ? 'No members yet: sync them from the source, or add them here.'
                      : `Nothing matches “${query}”.`}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
