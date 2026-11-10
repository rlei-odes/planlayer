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
} from './api'
import Select, { type Option } from './Select'

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
  capacity_actuals: 'Actual hours',
}
const DIMENSIONS: Option<MemberDataset>[] = [
  { value: 'cost_centers', label: 'Cost centers' },
  { value: 'accounts', label: 'Accounts' },
  { value: 'entities', label: 'Entities' },
  { value: 'work_centers', label: 'Work centers' },
  { value: 'activity_types', label: 'Activity types' },
]
const ATTRIBUTE_HEAD: Partial<Record<MemberDataset, string>> = {
  accounts: 'Type',
  cost_centers: 'Entity · owner',
  work_centers: 'Cost center',
  activity_types: 'Credited account',
}

function attributes(dataset: MemberDataset, r: MemberRow): string {
  switch (dataset) {
    case 'accounts':
      return r.account_type ?? ''
    case 'work_centers':
      return r.cost_center_code ?? ''
    case 'activity_types':
      return [r.account_code, r.variable_account_code && `variable: ${r.variable_account_code}`].filter(Boolean).join(' · ')
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
      <div className="pg-head">
        <div>
          <div className="su-eyebrow">Master data</div>
          <h2 className="su-title">{SECTION_LABEL[section]}</h2>
          <div className="pg-sub">
            {section === 'sources'
              ? 'Where each dataset comes from: one query per dataset, run read-only on the source.'
              : section === 'sync'
                ? 'Bring members and actuals in from the source, after a preview of what changes.'
                : 'The members planners see, synced or entered here, and their hierarchy.'}
          </div>
        </div>
        <div className="seg" role="group" aria-label="Section">
          {sections.map((s) => (
            <button key={s} type="button" aria-pressed={section === s} onClick={() => setSection(s)}>
              {SECTION_LABEL[s]}
            </button>
          ))}
        </div>
      </div>
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
      {datasets.map((d) => (
        <DatasetEditor key={d.dataset} info={d} onSaved={load} />
      ))}
    </div>
  )
}

const EMPTY: Mapping = { query: '', fields: {}, hierarchy: null }

function DatasetEditor({ info, onSaved }: { info: DatasetInfo; onSaved: () => void }) {
  const [open, setOpen] = useState(!info.mapping)
  const [m, setM] = useState<Mapping>(info.mapping ?? EMPTY)
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
  const choose = (value: string, onChange: (v: string) => void, label: string) => (
    <Select value={value} options={columnOptions(value)} onChange={onChange} label={label} placeholder="–  not mapped" />
  )
  const h = m.hierarchy
  const setH = (next: Hierarchy | null) => setM({ ...m, hierarchy: next })
  const mappedCount = info.fields.filter((f) => m.fields[f.name]).length

  return (
    <section className="su-card md-dataset">
      <button type="button" className="md-dataset-head" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <strong>{DATASET_LABEL[info.dataset]}</strong>
        <span className={`su-pill ${info.mapping ? 'approved' : 'neutral'}`}>{info.mapping ? 'Mapped' : 'Not mapped'}</span>
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
          <div className="su-row">
            <button type="button" className="su-btn" disabled={!m.query.trim()} onClick={() => void run(false)}>
              Show columns
            </button>
            <span className="ink-3">
              {columns.length ? `${columns.length} columns found` : 'Run the query once to choose its columns below.'}
            </span>
          </div>

          <div className="md-fields">
            {info.fields.map((f) => (
              <div className="md-field" key={f.name}>
                <span className="md-field-name">
                  {f.name}
                  {f.required && <span className="md-req" title="required">*</span>}
                  <span className="md-type">{f.type}</span>
                </span>
                {choose(
                  m.fields[f.name] ?? '',
                  (v) => setM({ ...m, fields: { ...m.fields, [f.name]: v } }),
                  `${info.dataset} ${f.name}`,
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
            <button type="button" className="su-btn primary" onClick={() => void save()}>
              Save mapping
            </button>
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

function columnsOf(m: Mapping | null): string[] {
  if (!m) return []
  const h = m.hierarchy
  const fromH = !h ? [] : 'parent' in h ? [h.parent] : h.levels.flatMap((lv) => [lv.code, lv.name])
  return [...new Set([...Object.values(m.fields), ...fromH].filter(Boolean))]
}

// --- Sync ---------------------------------------------------------------------------------------

function Sync() {
  const [entity, setEntity] = useState('')
  const [plan, setPlan] = useState<SyncPlan | null>(null)
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set())
  const [note, setNote] = useState<Note | null>(null)
  const [busy, setBusy] = useState(false)

  async function preview() {
    setBusy(true)
    try {
      const p = await api.syncPlan(entity || null)
      setPlan(p)
      setConfirmed(new Set())
      setNote(null)
    } catch (e) {
      setNote({ kind: 'error', text: errorText(e) })
    } finally {
      setBusy(false)
    }
  }

  async function runSync() {
    setBusy(true)
    try {
      const r = await api.syncRun(entity || null, [...confirmed])
      setNote({ kind: 'ok', text: `Synced ${r.members} members and ${r.actual_rows} actual cells.` })
      setPlan(null)
    } catch (e) {
      setNote({ kind: 'error', text: errorText(e) })
    } finally {
      setBusy(false)
    }
  }

  const unconfirmed = plan ? plan.matches.filter((m) => !confirmed.has(m.key)).length : 0
  return (
    <div className="su-col">
      <section className="su-card">
        <h3>
          <span className="su-step">1</span> Preview
        </h3>
        <p className="su-hint">Reads every mapped dataset and shows what would change. Nothing is written.</p>
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
          <button type="button" className="su-btn md-push" disabled={busy} onClick={() => void preview()}>
            {busy && !plan ? 'Reading the source…' : plan ? 'Preview again' : 'Preview sync'}
          </button>
        </div>
      </section>

      {note && <div className={`su-note ${note.kind}`}>{note.text}</div>}

      {plan && (
        <section className="su-card">
          <h3>
            <span className="su-step">2</span> What the sync would do
          </h3>
          <p className="su-hint">Datasets: {plan.datasets.join(', ') || 'none mapped'}</p>
          <div className="su-body">
            <div className="pb-tiles md-tiles">
              <Tile label="new" value={plan.new.length} tone="add" />
              <Tile label="changed" value={plan.changed.length} tone="chg" />
              <Tile label="no longer delivered" value={plan.gone.length} tone="rem" />
              <Tile label="actual cells" value={plan.actual_cells ?? 0} tone="same" />
            </div>
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
            <Changes label="Changed" items={plan.changed} />
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

function Tile({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className={`pb-tile ${tone}`}>
      <div className="pb-value">{value.toLocaleString('en-US')}</div>
      <div className="pb-label">{label}</div>
    </div>
  )
}

function Changes({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <details className="md-changes">
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
      <div className="md-bar">
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
        <div className="md-search">
          <svg width="14" height="14" viewBox="0 0 20 20" aria-hidden="true">
            <circle cx="9" cy="9" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.7" />
            <path d="m13.5 13.5 3 3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
          </svg>
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter by code, name or owner" aria-label="Filter members" />
        </div>
        <span className="ink-3 md-counts">
          {rows.length} members · {manual} entered here · {changed} changed here
        </span>
        <button type="button" className="su-btn primary" onClick={() => setAdding((a) => !a)}>
          Add member
        </button>
      </div>

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
