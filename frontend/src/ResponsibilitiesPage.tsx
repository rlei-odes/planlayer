import { useCallback, useEffect, useState } from 'react'
import { api, type Responsibilities, type Responsibility } from './api'
import Select, { type Option } from './Select'

// Responsibilities (ADR 0009), for the controller: per cost center the owner matched from the
// source and the planner it gives, which the controller can override; deputies on cost centers
// and nodes; approvers on nodes.

const MATCHED = '__matched__'
const NOBODY = '__nobody__'
type Note = { kind: 'ok' | 'error'; text: string }

export default function ResponsibilitiesPage() {
  const [data, setData] = useState<Responsibilities | null>(null)
  const [note, setNote] = useState<Note | null>(null)
  const [query, setQuery] = useState('')

  const load = useCallback(() => {
    api.responsibilities().then(setData, (e: unknown) => setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) }))
  }, [])

  useEffect(load, [load])

  async function run(task: () => Promise<unknown>, done: string) {
    try {
      await task()
      setNote({ kind: 'ok', text: done })
      load()
    } catch (e) {
      setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    }
  }

  if (!data) return note ? <div className="su-note error">{note.text}</div> : null

  function setPlanner(row: Responsibility, choice: string) {
    if (choice === MATCHED) void run(() => api.clearOwner(row.code), `${row.code} is planned by its owner from the source again.`)
    else
      void run(
        () => api.setOwner(row.code, choice === NOBODY ? null : choice),
        choice === NOBODY ? `${row.code} has no planner now.` : `${choice} plans ${row.code}.`,
      )
  }

  const plannerValue = (row: Responsibility) => (!row.overridden ? MATCHED : (row.planner ?? NOBODY))
  const plannerOptions = (row: Responsibility): Option[] => [
    { value: MATCHED, label: row.matched ?? 'nobody', hint: 'from the source' },
    { value: NOBODY, label: 'nobody' },
    ...data.users.map((u) => ({ value: u, label: u })),
  ]
  const q = query.trim().toLowerCase()
  const shown = q
    ? data.rows.filter((r) =>
        [r.code, r.name, r.owner, r.planner, ...r.deputies, ...r.approvers].join(' ').toLowerCase().includes(q),
      )
    : data.rows
  const overrides = data.rows.filter((r) => r.overridden).length

  return (
    <div className="su rs">
      <div className="pg-head">
        <div>
          <div className="su-eyebrow">Responsibilities</div>
          <h2 className="su-title">Who plans and who approves</h2>
          <div className="pg-sub">
            Owners come from the source and plan their cost centers. Overrides, deputies and approvers are kept here.
          </div>
        </div>
      </div>

      {data.unmatched_owners.length > 0 && (
        <div className="su-note warn rs-gap">
          <strong>Owners without a user:</strong> {data.unmatched_owners.join(', ')}. Their cost centers have no planner
          until a user exists or you choose one below.
        </div>
      )}
      {note && <div className={`su-note ${note.kind} rs-gap`}>{note.text}</div>}

      <div className="md-bar rs-gap">
        <div className="md-search">
          <svg width="14" height="14" viewBox="0 0 20 20" aria-hidden="true">
            <circle cx="9" cy="9" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.7" />
            <path d="m13.5 13.5 3 3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
          </svg>
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter by cost center or person" aria-label="Filter" />
        </div>
        <span className="ink-3 md-counts">
          {overrides} planner override{overrides === 1 ? '' : 's'}
        </span>
      </div>

      <div className="tbl-card">
        <div className="tbl-scroll">
          <table className="tbl">
            <thead>
              <tr>
                <th>Cost center · owner in the source</th>
                <th>Planner</th>
                <th>Deputies</th>
                <th>Approvers</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((row) => (
                <tr key={row.code} className={row.kind === 'node' ? 'node' : undefined}>
                  <td style={{ paddingLeft: 14 + (q ? 0 : row.depth * 18) }}>
                    {row.kind === 'cost_center' ? (
                      <div className="st-cc">
                        <span>
                          <span className="code">{row.code}</span>
                          {row.name}
                        </span>
                        <span className="st-owner">{row.owner ?? 'no owner in the source'}</span>
                      </div>
                    ) : (
                      row.name
                    )}
                  </td>
                  <td>
                    {row.kind === 'cost_center' && (
                      <span className="rs-planner">
                        <Select
                          variant="compact"
                          value={plannerValue(row)}
                          label={`Planner of ${row.code}`}
                          options={plannerOptions(row)}
                          searchable
                          onChange={(v) => setPlanner(row, v)}
                        />
                        {row.overridden && <span className="su-pill in_progress">Override</span>}
                      </span>
                    )}
                  </td>
                  <td>
                    <People
                      people={row.deputies}
                      users={data.users}
                      label={`Add a deputy for ${row.code}`}
                      onAdd={(u) => void run(() => api.addDeputy(row.code, u), `${u} may now plan ${row.code}.`)}
                      onRemove={(u) => void run(() => api.removeDeputy(row.code, u), `${u} no longer plans ${row.code}.`)}
                    />
                  </td>
                  <td>
                    {row.kind === 'node' && (
                      <People
                        people={row.approvers}
                        users={data.users}
                        label={`Add an approver for ${row.code}`}
                        onAdd={(u) => void run(() => api.addApprover(row.code, u), `${u} approves below ${row.name}.`)}
                        onRemove={(u) => void run(() => api.removeApprover(row.code, u), `${u} no longer approves below ${row.name}.`)}
                      />
                    )}
                  </td>
                </tr>
              ))}
              {shown.length === 0 && (
                <tr>
                  <td colSpan={4} className="st-empty">
                    Nothing matches “{query}”.
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

function People(props: {
  people: string[]
  users: string[]
  label: string
  onAdd: (login: string) => void
  onRemove: (login: string) => void
}) {
  const free = props.users.filter((u) => !props.people.includes(u))
  return (
    <span className="rs-people">
      {props.people.map((p) => (
        <span key={p} className="su-chip">
          {p}
          <button type="button" aria-label={`Remove ${p}`} onClick={() => props.onRemove(p)}>
            ✕
          </button>
        </span>
      ))}
      <Select
        variant="compact"
        className="rs-add"
        value=""
        display="Add"
        label={props.label}
        icon={<span className="pl-plus">+</span>}
        options={free.map((u) => ({ value: u, label: u }))}
        searchable
        disabled={free.length === 0}
        onChange={props.onAdd}
      />
    </span>
  )
}
