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

// Master data (ADR 0012): the admin's query per dataset with named columns (Sources), the sync
// preview with confirmation of matches (Sync), and the member tree with manual members and
// local overrides (Members), which also builds master data from scratch.

type Section = 'sources' | 'sync' | 'members'
const DATASET_LABEL: Record<Dataset, string> = {
  entities: 'Entities',
  cost_centers: 'Cost centers',
  accounts: 'Accounts',
  actuals: 'Actuals',
}

function errorText(e: unknown) {
  return e instanceof Error ? e.message : String(e)
}

export default function MasterDataPage({ me }: { me: Me }) {
  const sections: Section[] = [...(me.is_admin ? (['sources'] as const) : []), 'sync', 'members']
  const [section, setSection] = useState<Section>(sections[0])
  return (
    <section className="masterdata">
      <div className="toolbar">
        {sections.map((s) => (
          <button key={s} type="button" className={section === s ? 'tab active' : 'tab'} onClick={() => setSection(s)}>
            {s === 'sources' ? 'Sources' : s === 'sync' ? 'Sync' : 'Members'}
          </button>
        ))}
      </div>
      {section === 'sources' && <Sources />}
      {section === 'sync' && <Sync />}
      {section === 'members' && <Members />}
    </section>
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
    <>
      {error && <div className="error">{error}</div>}
      <p className="muted">
        One query per dataset, run read-only on the source connection. Name which result column is which field. Every
        dataset is optional: without a source, enter members by hand under Members.
      </p>
      {datasets.map((d) => (
        <DatasetEditor key={d.dataset} info={d} onSaved={load} />
      ))}
    </>
  )
}

const EMPTY: Mapping = { query: '', fields: {}, hierarchy: null }

function DatasetEditor({ info, onSaved }: { info: DatasetInfo; onSaved: () => void }) {
  const [m, setM] = useState<Mapping>(info.mapping ?? EMPTY)
  const [columns, setColumns] = useState<string[]>(() => columnsOf(info.mapping))
  const [preview, setPreview] = useState<Preview | null>(null)
  const [message, setMessage] = useState<{ kind: 'error' | 'info'; text: string } | null>(null)

  async function run(mapped: boolean) {
    try {
      const p = await api.preview(info.dataset, m, mapped)
      setPreview(p)
      if (!mapped && !p.error) setColumns(p.columns)
      setMessage(p.error ? { kind: 'error', text: p.error } : null)
    } catch (e) {
      setMessage({ kind: 'error', text: errorText(e) })
    }
  }

  async function save() {
    try {
      await api.saveMapping(info.dataset, m)
      setMessage({ kind: 'info', text: 'Saved.' })
      onSaved()
    } catch (e) {
      setMessage({ kind: 'error', text: errorText(e) })
    }
  }

  const choose = (value: string, onChange: (v: string) => void, label: string) => (
    <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
      <option value="">–</option>
      {[...new Set([...columns, value].filter(Boolean))].map((c) => (
        <option key={c} value={c}>
          {c}
        </option>
      ))}
    </select>
  )
  const h = m.hierarchy
  const setH = (next: Hierarchy | null) => setM({ ...m, hierarchy: next })

  return (
    <details className="dataset" open={!info.mapping}>
      <summary>
        <strong>{DATASET_LABEL[info.dataset]}</strong>{' '}
        <span className="muted">{info.mapping ? 'mapped' : 'not mapped'}</span>
      </summary>
      <textarea
        aria-label={`Query for ${info.dataset}`}
        rows={4}
        value={m.query}
        placeholder="SELECT … FROM …"
        onChange={(e) => setM({ ...m, query: e.target.value })}
      />
      <div className="toolbar">
        <button type="button" disabled={!m.query.trim()} onClick={() => void run(false)}>
          Show columns
        </button>
      </div>
      <table className="fields">
        <tbody>
          {info.fields.map((f) => (
            <tr key={f.name}>
              <td>
                {f.name}
                {f.required && ' *'} <span className="muted">{f.type}</span>
              </td>
              <td>
                {choose(m.fields[f.name] ?? '', (v) => setM({ ...m, fields: { ...m.fields, [f.name]: v } }), `${info.dataset} ${f.name}`)}
              </td>
            </tr>
          ))}
          {info.has_hierarchy && (
            <tr>
              <td>hierarchy</td>
              <td>
                <label>
                  <input type="radio" checked={!!h && 'parent' in h} onChange={() => setH({ parent: '' })} /> parent column
                </label>{' '}
                <label>
                  <input
                    type="radio"
                    checked={!!h && 'levels' in h}
                    onChange={() => setH({ levels: [{ code: '', name: '' }] })}
                  />{' '}
                  level columns
                </label>
                {h && 'parent' in h && <div>{choose(h.parent, (v) => setH({ parent: v }), `${info.dataset} parent`)}</div>}
                {h && 'levels' in h && (
                  <div>
                    {h.levels.map((lv, i) => (
                      <div key={i}>
                        Level {i + 1}: code{' '}
                        {choose(lv.code, (v) => setH({ levels: h.levels.map((x, j) => (j === i ? { ...x, code: v } : x)) }), `level ${i + 1} code`)}{' '}
                        name{' '}
                        {choose(lv.name, (v) => setH({ levels: h.levels.map((x, j) => (j === i ? { ...x, name: v } : x)) }), `level ${i + 1} name`)}{' '}
                        <button type="button" className="link" onClick={() => setH({ levels: h.levels.filter((_, j) => j !== i) })}>
                          ×
                        </button>
                      </div>
                    ))}
                    <button type="button" onClick={() => setH({ levels: [...h.levels, { code: '', name: '' }] })}>
                      Add level
                    </button>
                  </div>
                )}
              </td>
            </tr>
          )}
        </tbody>
      </table>
      <div className="toolbar">
        <button type="button" disabled={!m.query.trim()} onClick={() => void run(true)}>
          Preview as synced
        </button>
        <button type="button" onClick={() => void save()}>
          Save mapping
        </button>
        {info.mapping && (
          <button type="button" onClick={() => void api.deleteMapping(info.dataset).then(onSaved)}>
            Remove mapping
          </button>
        )}
      </div>
      {message && <div className={message.kind}>{message.text}</div>}
      {preview && !preview.error && (
        <div className="scroll">
          <table className="overview preview">
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
                    <td key={j}>{v ?? <span className="muted">null</span>}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
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
  const [message, setMessage] = useState<{ kind: 'error' | 'info'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  async function preview() {
    setBusy(true)
    try {
      const p = await api.syncPlan(entity || null)
      setPlan(p)
      setConfirmed(new Set())
      setMessage(null)
    } catch (e) {
      setMessage({ kind: 'error', text: errorText(e) })
    } finally {
      setBusy(false)
    }
  }

  async function runSync() {
    setBusy(true)
    try {
      const r = await api.syncRun(entity || null, [...confirmed])
      setMessage({ kind: 'info', text: `Synced ${r.members} members and ${r.actual_rows} actual cells.` })
      setPlan(null)
    } catch (e) {
      setMessage({ kind: 'error', text: errorText(e) })
    } finally {
      setBusy(false)
    }
  }

  const unconfirmed = plan ? plan.matches.filter((m) => !confirmed.has(m.key)).length : 0
  return (
    <>
      <div className="toolbar">
        <label>
          Actuals of entity <input value={entity} onChange={(e) => setEntity(e.target.value)} placeholder="all" size={6} />
        </label>
        <button type="button" disabled={busy} onClick={() => void preview()}>
          Preview sync
        </button>
        {plan && (
          <button type="button" disabled={busy || plan.problems.length > 0 || unconfirmed > 0} onClick={() => void runSync()}>
            Run sync
          </button>
        )}
        {busy && <span className="muted">Working…</span>}
      </div>
      {message && <div className={message.kind}>{message.text}</div>}
      {plan && (
        <div className="plan">
          <p>
            Datasets: {plan.datasets.join(', ') || 'none mapped'}
            {plan.actual_cells !== null && ` · ${plan.actual_cells} actual cells`}
          </p>
          {plan.problems.length > 0 && (
            <div className="error">
              <strong>{plan.problems.length} problem(s), nothing can be synced:</strong>
              <ul>
                {plan.problems.slice(0, 50).map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </div>
          )}
          {plan.matches.length > 0 && (
            <div className="info">
              <strong>
                Found {plan.matches.length} match{plan.matches.length === 1 ? '' : 'es'} from the source.
              </strong>{' '}
              Confirm each: the source takes the member over, its manual data is kept as a shadow record.
              <ul>
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
                      />{' '}
                      {m.dimension} {m.code} “{m.name}”
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <Changes label="New" items={plan.new} />
          <Changes label="Changed" items={plan.changed} />
          <Changes label="No longer delivered (kept)" items={plan.gone} />
        </div>
      )}
    </>
  )
}

function Changes({ label, items }: { label: string; items: string[] }) {
  return (
    <details>
      <summary>
        {label}: {items.length}
      </summary>
      <ul>
        {items.slice(0, 200).map((i) => (
          <li key={i}>{i}</li>
        ))}
      </ul>
    </details>
  )
}

// --- Members ------------------------------------------------------------------------------------

function Members() {
  const [dataset, setDataset] = useState<MemberDataset>('cost_centers')
  const [rows, setRows] = useState<MemberRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<{ code: string; name: string } | null>(null)
  const [draft, setDraft] = useState({ code: '', name: '', parent: '', is_group: false, entity_code: '', owner: '', account_type: '' })

  const load = useCallback(() => {
    api.members(dataset).then(setRows, (e: unknown) => setError(errorText(e)))
  }, [dataset])
  useEffect(load, [load])

  async function run(task: () => Promise<unknown>) {
    try {
      await task()
      setError(null)
      load()
    } catch (e) {
      setError(errorText(e))
    }
  }

  const groups = rows.filter((r) => r.is_group).map((r) => r.code)
  const tree = dataset !== 'entities'
  return (
    <>
      <div className="toolbar">
        <select value={dataset} onChange={(e) => setDataset(e.target.value as MemberDataset)} aria-label="Dimension">
          <option value="cost_centers">Cost centers</option>
          <option value="accounts">Accounts</option>
          <option value="entities">Entities</option>
        </select>
        <span className="muted">
          Synced members can be renamed and moved; the change survives syncs. Manual members are fully editable.
        </span>
      </div>
      {error && <div className="error">{error}</div>}
      <form
        className="toolbar"
        onSubmit={(e) => {
          e.preventDefault()
          void run(async () => {
            await api.addMember(dataset, {
              code: draft.code,
              name: draft.name,
              parent: draft.parent || null,
              is_group: draft.is_group,
              entity_code: draft.entity_code || null,
              owner: draft.owner || null,
              account_type: draft.account_type || null,
            })
            setDraft({ ...draft, code: '', name: '' })
          })
        }}
      >
        <input placeholder="Code" value={draft.code} onChange={(e) => setDraft({ ...draft, code: e.target.value })} required size={8} />
        <input placeholder="Name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} required />
        {tree && (
          <select value={draft.parent} onChange={(e) => setDraft({ ...draft, parent: e.target.value })} aria-label="Parent">
            <option value="">(top level)</option>
            {groups.map((g) => (
              <option key={g} value={g}>
                under {g}
              </option>
            ))}
          </select>
        )}
        {tree && (
          <label>
            <input type="checkbox" checked={draft.is_group} onChange={(e) => setDraft({ ...draft, is_group: e.target.checked })} /> group
          </label>
        )}
        {dataset === 'cost_centers' && (
          <>
            <input placeholder="Entity" value={draft.entity_code} onChange={(e) => setDraft({ ...draft, entity_code: e.target.value })} size={6} />
            <input placeholder="Owner (e-mail)" value={draft.owner} onChange={(e) => setDraft({ ...draft, owner: e.target.value })} />
          </>
        )}
        {dataset === 'accounts' && (
          <input
            placeholder="Type (cost/revenue)"
            value={draft.account_type}
            onChange={(e) => setDraft({ ...draft, account_type: e.target.value })}
            size={10}
          />
        )}
        <button type="submit">Add member</button>
      </form>
      <table className="overview">
        <thead>
          <tr>
            <th>Member</th>
            <th>Origin</th>
            {tree && <th>Parent</th>}
            <th>{dataset === 'accounts' ? 'Type' : dataset === 'cost_centers' ? 'Entity · owner' : ''}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.code} className={r.is_group ? 'node' : undefined}>
              <td style={{ paddingLeft: `${8 + r.depth * 16}px` }}>
                {r.code}{' '}
                {editing?.code === r.code ? (
                  <form
                    className="inline"
                    onSubmit={(e) => {
                      e.preventDefault()
                      void run(() => api.changeMember(dataset, r.code, { name: editing.name })).then(() => setEditing(null))
                    }}
                  >
                    <input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} autoFocus />
                    <button type="submit">OK</button>
                  </form>
                ) : (
                  <>
                    {r.name}
                    {r.renamed && <span className="muted"> (source: {r.source_name})</span>}
                  </>
                )}
              </td>
              <td>
                <span className={`badge ${r.origin === 'manual' ? 'in_progress' : ''}`}>{r.origin}</span>
                {(r.renamed || r.moved) && <span className="badge overridden">changed here</span>}
              </td>
              {tree && (
                <td>
                  <select
                    value={r.parent ?? ''}
                    aria-label={`Parent of ${r.code}`}
                    onChange={(e) => void run(() => api.changeMember(dataset, r.code, { parent: e.target.value || null }))}
                  >
                    <option value="">(top level)</option>
                    {groups
                      .filter((g) => g !== r.code)
                      .map((g) => (
                        <option key={g} value={g}>
                          {g}
                        </option>
                      ))}
                  </select>
                  {r.moved && <span className="muted"> (source: {r.source_parent ?? 'top'})</span>}
                </td>
              )}
              <td className="muted">
                {dataset === 'accounts' ? r.account_type : [r.entity_code, r.owner].filter(Boolean).join(' · ')}
              </td>
              <td className="actions">
                <button type="button" onClick={() => setEditing({ code: r.code, name: r.name })}>
                  Rename
                </button>
                {(r.renamed || r.moved) && (
                  <button type="button" onClick={() => void run(() => api.resetMember(dataset, r.code))}>
                    Back to source
                  </button>
                )}
                {r.origin === 'manual' && (
                  <button type="button" onClick={() => void run(() => api.deleteMember(dataset, r.code))}>
                    Delete
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}
