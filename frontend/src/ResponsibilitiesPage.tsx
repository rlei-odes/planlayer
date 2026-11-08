import { useCallback, useEffect, useState } from 'react'
import { api, type Responsibilities, type Responsibility } from './api'

// Responsibilities (ADR 0009), for the controller: per cost center the owner matched from the
// source and the planner it gives, which the controller can override; deputies on cost centers
// and nodes; approvers on nodes.

const MATCHED = '__matched__'
const NOBODY = '__nobody__'

export default function ResponsibilitiesPage() {
  const [data, setData] = useState<Responsibilities | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    api.responsibilities().then(setData, (e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  useEffect(load, [load])

  async function run(task: () => Promise<unknown>) {
    try {
      await task()
      setError(null)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  if (!data) return error ? <div className="error">{error}</div> : null

  function setPlanner(row: Responsibility, choice: string) {
    if (choice === MATCHED) void run(() => api.clearOwner(row.code))
    else void run(() => api.setOwner(row.code, choice === NOBODY ? null : choice))
  }

  const plannerValue = (row: Responsibility) => (!row.overridden ? MATCHED : (row.planner ?? NOBODY))

  return (
    <section className="responsibilities">
      {error && <div className="error">{error}</div>}
      {data.unmatched_owners.length > 0 && (
        <div className="info">
          Owners from the source without an active user: {data.unmatched_owners.join(', ')}. Their cost centers have no
          planner until a user exists or you choose one below.
        </div>
      )}
      <table className="overview">
        <thead>
          <tr>
            <th>Cost center</th>
            <th>Owner in the source</th>
            <th>Planner</th>
            <th>Deputies</th>
            <th>Approvers</th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row) => (
            <tr key={row.code} className={row.kind}>
              <td style={{ paddingLeft: `${8 + row.depth * 16}px` }}>
                {row.kind === 'cost_center' ? `${row.code} ${row.name}` : row.name}
              </td>
              <td className="muted">{row.owner ?? (row.kind === 'cost_center' ? '–' : '')}</td>
              <td>
                {row.kind === 'cost_center' && (
                  <select
                    value={plannerValue(row)}
                    aria-label={`Planner of ${row.code}`}
                    className={row.overridden ? 'overridden' : undefined}
                    onChange={(e) => setPlanner(row, e.target.value)}
                  >
                    <option value={MATCHED}>{row.matched ? `${row.matched} (from source)` : 'none (from source)'}</option>
                    <option value={NOBODY}>nobody</option>
                    {data.users.map((u) => (
                      <option key={u} value={u}>
                        {u}
                      </option>
                    ))}
                  </select>
                )}
              </td>
              <td>
                <People
                  people={row.deputies}
                  users={data.users}
                  label={`Add deputy for ${row.code}`}
                  onAdd={(u) => void run(() => api.addDeputy(row.code, u))}
                  onRemove={(u) => void run(() => api.removeDeputy(row.code, u))}
                />
              </td>
              <td>
                {row.kind === 'node' && (
                  <People
                    people={row.approvers}
                    users={data.users}
                    label={`Add approver for ${row.code}`}
                    onAdd={(u) => void run(() => api.addApprover(row.code, u))}
                    onRemove={(u) => void run(() => api.removeApprover(row.code, u))}
                  />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function People(props: {
  people: string[]
  users: string[]
  label: string
  onAdd: (login: string) => void
  onRemove: (login: string) => void
}) {
  return (
    <span className="people">
      {props.people.map((p) => (
        <span key={p} className="badge">
          {p}{' '}
          <button type="button" className="link" aria-label={`Remove ${p}`} onClick={() => props.onRemove(p)}>
            ×
          </button>
        </span>
      ))}
      <select value="" aria-label={props.label} onChange={(e) => e.target.value && props.onAdd(e.target.value)}>
        <option value="">+</option>
        {props.users
          .filter((u) => !props.people.includes(u))
          .map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
      </select>
    </span>
  )
}
