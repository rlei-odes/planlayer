import { useCallback, useEffect, useState } from 'react'
import { api, STATUS_LABEL, type Action, type ActionRequest, type Overview, type Status, type StatusRow } from './api'

// The status page (T-20, ADR 0008): every enabled cost center of the round with its status and
// approver, rolled up per hierarchy node; approve, reject and reopen; the round's status.

type Filter = 'all' | 'overdue' | 'submitted'
type Pending = { request: ActionRequest; label: string }

const STATUSES: Status[] = ['not_started', 'in_progress', 'submitted', 'approved']
const NEEDS_COMMENT: Action[] = ['reject', 'reopen']

function matches(row: StatusRow, filter: Filter): boolean {
  if (filter === 'overdue') return row.overdue
  if (filter === 'submitted') return row.status === 'submitted'
  return true
}

/** Rows to show: matching cost centers, and the nodes that have one below them. */
function visible(rows: StatusRow[], filter: Filter): StatusRow[] {
  return rows.filter((row, i) => {
    if (row.kind === 'cost_center') return matches(row, filter)
    for (let j = i + 1; j < rows.length && rows[j].depth > row.depth; j++) {
      if (rows[j].kind === 'cost_center' && matches(rows[j], filter)) return true
    }
    return false
  })
}

export default function StatusPage({ version, onRoundChanged }: { version: string; onRoundChanged: () => void }) {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [pending, setPending] = useState<Pending | null>(null)
  const [comment, setComment] = useState('')
  const [message, setMessage] = useState<{ kind: 'error' | 'info'; text: string } | null>(null)

  const load = useCallback(() => {
    api.overview(version).then(setOverview, (e: unknown) =>
      setMessage({ kind: 'error', text: e instanceof Error ? e.message : String(e) }),
    )
  }, [version])

  useEffect(load, [load])

  async function run(request: ActionRequest) {
    try {
      const changed = await api.act(version, request)
      setMessage({ kind: 'info', text: `${request.action}: ${changed.join(', ')}` })
      setPending(null)
      setComment('')
      load()
    } catch (e) {
      setMessage({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    }
  }

  function start(request: ActionRequest, label: string) {
    if (NEEDS_COMMENT.includes(request.action)) {
      setPending({ request, label })
      setComment('')
    } else void run(request)
  }

  async function guard(task: () => Promise<unknown>) {
    try {
      await task()
      load()
    } catch (e) {
      setMessage({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    }
  }

  if (!overview) return message ? <div className={message.kind}>{message.text}</div> : null
  const round = overview.round
  const setRound = (status: 'open' | 'closed') =>
    guard(async () => {
      await api.setRoundStatus(version, status)
      onRoundChanged()
    })
  const rows = visible(overview.rows, filter)

  return (
    <section className="status">
      <div className="toolbar">
        <span>
          Round <span className={`badge round-${round.status}`}>{round.status}</span>
        </span>
        {round.deadline && <span className="muted">Deadline {round.deadline}</span>}
        {round.status !== 'open' && (
          <button type="button" onClick={() => void setRound('open')}>
            Open round
          </button>
        )}
        {round.status === 'open' && (
          <button type="button" onClick={() => void setRound('closed')}>
            Close round
          </button>
        )}
        <label>
          Show{' '}
          <select value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
            <option value="all">All</option>
            <option value="overdue">Overdue</option>
            <option value="submitted">Waiting for approval</option>
          </select>
        </label>
      </div>
      {message && <div className={message.kind}>{message.text}</div>}
      {pending && (
        <form
          className="comment"
          onSubmit={(e) => {
            e.preventDefault()
            void run({ ...pending.request, comment })
          }}
        >
          <label>
            {pending.label}: comment{' '}
            <input value={comment} onChange={(e) => setComment(e.target.value)} autoFocus required size={50} />
          </label>{' '}
          <button type="submit" disabled={!comment.trim()}>
            Confirm
          </button>{' '}
          <button type="button" onClick={() => setPending(null)}>
            Cancel
          </button>
        </form>
      )}
      <table className="overview">
        <thead>
          <tr>
            <th>Cost center</th>
            <th>Status</th>
            <th>Approver</th>
            <th>Last change</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.code} className={row.kind}>
              <td style={{ paddingLeft: `${8 + row.depth * 16}px` }}>
                {row.kind === 'cost_center' ? `${row.code} ${row.name}` : row.name}
                {row.owner && <span className="muted"> · {row.owner}</span>}
              </td>
              <td>
                {row.kind === 'node' ? (
                  <span className="counts">
                    {STATUSES.filter((s) => row.counts[s]).map((s) => (
                      <span key={s} className={`badge ${s}`}>
                        {row.counts[s]} {STATUS_LABEL[s].toLowerCase()}
                      </span>
                    ))}
                  </span>
                ) : (
                  row.status && (
                    <>
                      <span className={`badge ${row.status}`}>{STATUS_LABEL[row.status]}</span>
                      {row.overdue && <span className="badge overdue">overdue</span>}
                    </>
                  )
                )}
              </td>
              <td>{row.kind === 'cost_center' && (row.approvers.length ? row.approvers.join(', ') : 'Controller')}</td>
              <td className="muted">
                {row.changed_at && `${new Date(row.changed_at).toLocaleString()} · ${row.changed_by ?? ''}`}
              </td>
              <td className="actions">
                {round.status === 'open' && <Actions row={row} start={start} />}
                {row.kind === 'cost_center' && (
                  <button type="button" onClick={() => void guard(() => api.setEnabled(version, row.code, false))}>
                    Disable
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {overview.not_planned.length > 0 && (
        <>
          <h2>Not planned in this round</h2>
          <table className="overview">
            <tbody>
              {overview.not_planned.map((n) => (
                <tr key={n.code}>
                  <td>
                    {n.code} {n.name}
                  </td>
                  <td className="muted">{n.reason === 'new' ? 'new, not enabled' : 'disabled'}</td>
                  <td className="actions">
                    <button type="button" onClick={() => void guard(() => api.setEnabled(version, n.code, true))}>
                      Enable
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  )
}

function Actions({ row, start }: { row: StatusRow; start: (r: ActionRequest, label: string) => void }) {
  if (row.kind === 'node') {
    const n = row.counts.submitted ?? 0
    return n > 0 ? (
      <button type="button" onClick={() => start({ action: 'approve', node: row.code }, `Approve below ${row.name}`)}>
        Approve {n} submitted
      </button>
    ) : null
  }
  const one = (action: Action, label: string) => (
    <button
      type="button"
      key={action}
      onClick={() => start({ action, cost_centers: [row.code] }, `${label} ${row.code}`)}
    >
      {label}
    </button>
  )
  switch (row.status) {
    case 'submitted':
      return (
        <>
          {one('approve', 'Approve')} {one('reject', 'Reject')}
        </>
      )
    case 'approved':
      return one('reopen', 'Reopen')
    default:
      return one('submit', 'Submit')
  }
}
