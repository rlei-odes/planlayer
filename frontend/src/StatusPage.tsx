import { useCallback, useEffect, useState } from 'react'
import { api, STATUS_LABEL, type Action, type ActionRequest, type Me, type Overview, type Status, type StatusRow } from './api'
import PageHeader from './PageHeader'
import Tile from './Tile'

// The status page (T-20, ADR 0008): every enabled cost center of the round with its status and
// approver, rolled up per hierarchy node; approve, reject and reopen; the round's status. The
// actions shown are the ones the server says this user may take.

type Filter = 'all' | 'mine' | 'submitted' | 'overdue'
type Pending = { request: ActionRequest; label: string }
type Note = { kind: 'ok' | 'error'; text: string }

const STATUSES: Status[] = ['not_started', 'in_progress', 'submitted', 'approved']
const NEEDS_COMMENT: Action[] = ['reject', 'reopen']
const ACTION_LABEL: Record<Action, string> = { submit: 'Submit', approve: 'Approve', reject: 'Reject', reopen: 'Reopen' }
const PAST: Record<Action, string> = { submit: 'Submitted', approve: 'Approved', reject: 'Rejected', reopen: 'Reopened' }
const WHEN = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

/** Whole days from today to the deadline (YYYY-MM-DD); negative once it has passed. */
function daysUntil(deadline: string, today: Date): number {
  const [y, m, d] = deadline.split('-').map(Number)
  const start = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())
  return Math.round((Date.UTC(y, m - 1, d) - start) / 86_400_000)
}

/** The deadline pill: calm while there's time, amber in the last two weeks, red once overdue. */
function deadlinePill(days: number): { tone: string; text: string } {
  const n = Math.abs(days)
  const unit = n === 1 ? 'day' : 'days'
  if (days < 0) return { tone: 'overdue', text: `Overdue by ${n} ${unit}` }
  if (days === 0) return { tone: 'in_progress', text: 'Due today' }
  return { tone: days <= 14 ? 'in_progress' : 'neutral', text: `${n} ${unit} left` }
}

function matches(row: StatusRow, filter: Filter): boolean {
  if (filter === 'overdue') return row.overdue
  if (filter === 'submitted') return row.status === 'submitted'
  if (filter === 'mine') return row.actions.includes('approve')
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

function errorText(e: unknown) {
  return e instanceof Error ? e.message : String(e)
}

export default function StatusPage({
  version,
  me,
  onRoundChanged,
}: {
  version: string
  me: Me
  onRoundChanged: () => void
}) {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [pending, setPending] = useState<Pending | null>(null)
  const [comment, setComment] = useState('')
  const [note, setNote] = useState<Note | null>(null)
  const [today] = useState(() => new Date())

  const load = useCallback(() => {
    api.overview(version).then(setOverview, (e: unknown) => setNote({ kind: 'error', text: errorText(e) }))
  }, [version])

  useEffect(load, [load])

  async function run(request: ActionRequest) {
    try {
      const changed = await api.act(version, request)
      setNote({ kind: 'ok', text: `${PAST[request.action]}: ${changed.join(', ') || 'nothing to change'}.` })
      setPending(null)
      setComment('')
      load()
    } catch (e) {
      setNote({ kind: 'error', text: errorText(e) })
    }
  }

  function start(request: ActionRequest, label: string) {
    if (NEEDS_COMMENT.includes(request.action)) {
      setPending({ request, label })
      setComment('')
    } else void run(request)
  }

  async function guard(task: () => Promise<unknown>, done?: string) {
    try {
      await task()
      if (done) setNote({ kind: 'ok', text: done })
      load()
    } catch (e) {
      setNote({ kind: 'error', text: errorText(e) })
    }
  }

  if (!overview) return note ? <div className="su-note error">{note.text}</div> : null
  const round = overview.round
  const setRound = (status: 'open' | 'closed') =>
    guard(async () => {
      await api.setRoundStatus(version, status)
      onRoundChanged()
    }, status === 'open' ? 'Round opened: planners can enter values.' : 'Round closed: no edits anywhere.')

  const centers = overview.rows.filter((r) => r.kind === 'cost_center')
  const count = (s: Status) => centers.filter((r) => r.status === s).length
  const counts = { all: centers.length, ...Object.fromEntries(STATUSES.map((s) => [s, count(s)])) } as Record<
    Status | 'all',
    number
  >
  const filterCount: Record<Filter, number> = {
    all: centers.length,
    mine: centers.filter((r) => matches(r, 'mine')).length,
    submitted: counts.submitted,
    overdue: centers.filter((r) => r.overdue).length,
  }
  const rows = visible(overview.rows, filter)
  const done = centers.length ? Math.round((counts.approved / centers.length) * 100) : 0
  const due = round.deadline && round.status !== 'closed' ? deadlinePill(daysUntil(round.deadline, today)) : null

  return (
    <div className="su st">
      <PageHeader
        eyebrow="Status"
        title={round.name}
        sub={`${round.deadline ? `Deadline ${round.deadline}` : 'No deadline'} · ${centers.length} cost centers to plan`}
        actions={
          <>
            <span className={`su-pill ${round.status === 'open' ? 'open' : round.status === 'closed' ? 'overdue' : 'in_progress'}`}>
              <i className={`su-dot ${round.status}`} />
              Round {round.status}
            </span>
            {due && (
              <span className={`su-pill ${due.tone}`} title={`Deadline ${round.deadline}`}>
                {due.text}
              </span>
            )}
            {me.is_controller && round.status !== 'open' && (
              <button type="button" className="su-btn primary" onClick={() => void setRound('open')}>
                {round.status === 'closed' ? 'Reopen round' : 'Open round'}
              </button>
            )}
            {me.is_controller && round.status === 'open' && (
              <button type="button" className="su-btn" onClick={() => void setRound('closed')}>
                Close round
              </button>
            )}
          </>
        }
        tabs={
          <div className="seg" role="group" aria-label="Show">
            {(
              [
                ['all', 'All'],
                ['mine', 'Waiting for me'],
                ['submitted', 'Waiting for approval'],
                ['overdue', 'Overdue'],
              ] as [Filter, string][]
            ).map(([f, label]) => (
              <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>
                {label}
                <span className="count">{filterCount[f]}</span>
              </button>
            ))}
          </div>
        }
      />

      <div className="tiles st-summary">
        {STATUSES.map((s) => (
          <Tile key={s} value={counts[s]} label={STATUS_LABEL[s]} dot={s} />
        ))}
        <Tile value={`${done}%`} label="approved" className="st-progress">
          <StatusBar counts={counts} />
        </Tile>
      </div>

      {note && <div className={`su-note ${note.kind} st-gap`}>{note.text}</div>}
      {pending && (
        <form
          className="su-card st-comment"
          onSubmit={(e) => {
            e.preventDefault()
            void run({ ...pending.request, comment })
          }}
        >
          <strong>{pending.label}</strong>
          <input
            className="su-input"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Why? The planner sees this comment."
            aria-label="Comment"
            autoFocus
            required
          />
          <button type="button" className="su-btn ghost" onClick={() => setPending(null)}>
            Cancel
          </button>
          <button type="submit" className="su-btn primary" disabled={!comment.trim()}>
            {ACTION_LABEL[pending.request.action]}
          </button>
        </form>
      )}

      <div className="tbl-card">
        <div className="tbl-scroll">
          <table className="tbl">
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
                <tr key={row.code} className={row.kind === 'node' ? 'node' : undefined}>
                  <td style={{ paddingLeft: 14 + row.depth * 18 }}>
                    {row.kind === 'cost_center' ? (
                      <div className="st-cc">
                        <span>
                          <span className="code">{row.code}</span>
                          {row.name}
                        </span>
                        <span className="st-owner">{row.owner ?? 'no owner'}</span>
                      </div>
                    ) : (
                      row.name
                    )}
                  </td>
                  <td>
                    {row.kind === 'node' ? (
                      <span className="st-node">
                        <StatusBar counts={row.counts} />
                        <span className="st-node-text">
                          {row.counts.approved ?? 0}/{STATUSES.reduce((n, s) => n + (row.counts[s] ?? 0), 0)} approved
                        </span>
                      </span>
                    ) : (
                      row.status && (
                        <span className="btn-row">
                          <span className={`su-pill ${row.status}`}>{STATUS_LABEL[row.status]}</span>
                          {row.overdue && <span className="su-pill overdue">Overdue</span>}
                        </span>
                      )
                    )}
                  </td>
                  <td className="muted">
                    {row.kind === 'cost_center' && (row.approvers.length ? row.approvers.join(', ') : 'Controller')}
                  </td>
                  <td className="muted">
                    {row.changed_at && (
                      <span title={new Date(row.changed_at).toLocaleString()}>
                        {WHEN.format(new Date(row.changed_at))}
                        {row.changed_by && ` · ${row.changed_by}`}
                      </span>
                    )}
                  </td>
                  <td className="right">
                    <span className="btn-row">
                      <Actions row={row} rows={overview.rows} start={start} />
                      {me.is_controller && row.kind === 'cost_center' && (
                        <button
                          type="button"
                          className="su-btn ghost sm"
                          title="Take this cost center out of the round"
                          onClick={() => void guard(() => api.setEnabled(version, row.code, false), `${row.code} is no longer planned in this round.`)}
                        >
                          Disable
                        </button>
                      )}
                    </span>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={5} className="st-empty">
                    {filter === 'mine'
                      ? 'Nothing is waiting for your approval.'
                      : filter === 'overdue'
                        ? 'Nothing is overdue.'
                        : filter === 'submitted'
                          ? 'Nothing is waiting for approval.'
                          : 'No cost center is planned in this round.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {me.is_controller && overview.not_planned.length > 0 && (
        <>
          <h3 className="st-h3">Not planned in this round</h3>
          <p className="su-hint st-hint">
            New cost centers from a sync wait for you; disabled ones stay out until you enable them.
          </p>
          <div className="tbl-card">
            <table className="tbl">
              <tbody>
                {overview.not_planned.map((n) => (
                  <tr key={n.code}>
                    <td>
                      <span className="code">{n.code}</span>
                      {n.name}
                    </td>
                    <td>
                      <span className={`su-pill ${n.reason === 'new' ? 'submitted' : 'neutral'}`}>
                        {n.reason === 'new' ? 'New, not enabled' : 'Disabled'}
                      </span>
                    </td>
                    <td className="right">
                      <button
                        type="button"
                        className="su-btn sm"
                        onClick={() => void guard(() => api.setEnabled(version, n.code, true), `${n.code} is planned in this round.`)}
                      >
                        Enable
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

/** A stacked bar of statuses: not started, in progress, submitted, approved. */
function StatusBar({ counts }: { counts: Partial<Record<Status, number>> }) {
  const total = STATUSES.reduce((n, s) => n + (counts[s] ?? 0), 0)
  return (
    <span
      className="st-statusbar"
      role="img"
      aria-label={STATUSES.map((s) => `${counts[s] ?? 0} ${STATUS_LABEL[s].toLowerCase()}`).join(', ')}
      title={STATUSES.filter((s) => counts[s]).map((s) => `${counts[s]} ${STATUS_LABEL[s].toLowerCase()}`).join(' · ')}
    >
      {total > 0 &&
        STATUSES.map((s) => (counts[s] ? <i key={s} className={s} style={{ flexGrow: counts[s] }} /> : null))}
    </span>
  )
}

/** The actions the server says this user may take; on a node, approving what they may below it. */
function Actions({
  row,
  rows,
  start,
}: {
  row: StatusRow
  rows: StatusRow[]
  start: (r: ActionRequest, label: string) => void
}) {
  if (row.kind === 'node') {
    const at = rows.indexOf(row)
    let n = 0
    for (let j = at + 1; j < rows.length && rows[j].depth > row.depth; j++) {
      if (rows[j].actions.includes('approve')) n++
    }
    return n > 0 ? (
      <button
        type="button"
        className="su-btn primary sm"
        onClick={() => start({ action: 'approve', node: row.code }, `Approve below ${row.name}`)}
      >
        Approve {n} submitted
      </button>
    ) : null
  }
  return (
    <>
      {row.actions.map((action) => (
        <button
          type="button"
          key={action}
          className={`su-btn sm${action === 'approve' ? ' primary' : action === 'reject' ? ' danger' : ''}`}
          onClick={() => start({ action, cost_centers: [row.code] }, `${ACTION_LABEL[action]} ${row.code}`)}
        >
          {ACTION_LABEL[action]}
        </button>
      ))}
    </>
  )
}
