import { useCallback, useEffect, useState } from 'react'
import { api, STATUS_LABEL, type Home, type Me, type MyNotes, type MyThread, type RoundWork, type Status, type Transfer } from './api'
import { TransferDecision, TransferLine } from './Transfers'

// The start page (ADR 0021): a greeting, the user's open tasks (transfers to accept, note tasks),
// the cost centers they plan in each open round with the sheets to fill in, their own
// transfers, and the notes that mention them or that they started.

export type Sheet = RoundWork['cost_centers'][number]['sheets'][number]
export type HomeTarget = 'status' | 'setup' | 'capex'

const WHEN = new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short' })
const TODAY = new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })
const DAY = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
const SHEET_LABEL: Record<Sheet, string> = { capacity: 'Capacity', workforce: 'Workforce', personnel: 'Pay', planning: 'Costs' }
const ORDER: Status[] = ['not_started', 'in_progress', 'submitted', 'approved']

function greeting(hour: number): string {
  if (hour < 5) return 'Good evening'
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

function daysLeft(deadline: string, today: Date): number {
  const [y, m, d] = deadline.split('-').map(Number)
  const start = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())
  return Math.round((Date.UTC(y, m - 1, d) - start) / 86_400_000)
}

function deadlineText(days: number): { tone: string; text: string } {
  const n = Math.abs(days)
  const unit = n === 1 ? 'day' : 'days'
  if (days < 0) return { tone: 'overdue', text: `Overdue by ${n} ${unit}` }
  if (days === 0) return { tone: 'in_progress', text: 'Due today' }
  return { tone: days <= 14 ? 'in_progress' : 'neutral', text: `${n} ${unit} left` }
}

export default function HomePage({
  me,
  onOpenThread,
  onOpenSheet,
  onGo,
  onChanged,
}: {
  me: Me
  onOpenThread: (thread: MyThread) => void
  onOpenSheet: (round: string, costCenter: string, sheet: Sheet) => void
  onGo: (round: string, page: HomeTarget) => void
  onChanged: (tasks: number) => void
}) {
  const [home, setHome] = useState<Home | null>(null)
  const [notes, setNotes] = useState<MyNotes | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [now] = useState(() => new Date())
  const readsNotes = me.is_controller || me.is_viewer || me.reads_cost_centers

  const load = useCallback(() => {
    const failed = (e: unknown) => setError(e instanceof Error ? e.message : String(e))
    api.home().then((h) => {
      setHome(h)
      onChanged(h.tasks)
    }, failed)
    if (readsNotes) api.myNotes().then(setNotes, failed)
  }, [readsNotes, onChanged])
  useEffect(load, [load])

  async function decide(t: Transfer, accept: boolean, reason?: string) {
    setBusy(true)
    setError(null)
    try {
      await api.decideTransfer(t.transfer_id, accept, reason)
      setMessage(
        accept
          ? `${t.person_name ?? t.title} now plans with ${t.to_code}${t.starts ? ` from ${DAY.format(new Date(t.starts))}` : ''}.`
          : `Transfer of ${t.person_name ?? t.title} declined; it stays with ${t.from_code}.`,
      )
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function withdraw(t: Transfer) {
    setBusy(true)
    try {
      await api.cancelTransfer(t.transfer_id)
      setMessage(`Transfer of ${t.person_name ?? t.title} to ${t.to_code} withdrawn.`)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const first = me.display_name.split(' ')[0]
  const transfers = home?.transfers ?? []
  const toDecide = transfers.filter((t) => t.status === 'proposed' && t.can_decide)
  const mine = transfers.filter((t) => !(t.status === 'proposed' && t.can_decide))
  const threads = notes?.threads ?? []
  const assigned = threads.filter((t) => t.reason === 'assigned')
  const approvals = (home?.rounds ?? []).filter((w) => w.to_approve > 0)
  // HR (ADR 0022): small groups to charge elsewhere, and submitted pay to review.
  const payTasks = (home?.rounds ?? []).filter((w) => w.small_groups.length > 0 || w.to_review.length > 0 || w.pay_step)
  // Capex (ADR 0028): open investments to confirm.
  const confirms = (home?.rounds ?? []).filter((w) => (w.to_confirm ?? 0) > 0)
  const taskCount = toDecide.length + assigned.length + approvals.length + payTasks.length + confirms.length
  const loaded = home !== null && (notes !== null || !readsNotes)

  return (
    <div className="su hm">
      <div className="hm-hero">
        <div className="su-eyebrow">{TODAY.format(now)}</div>
        <h2 className="hm-greeting">
          {greeting(now.getHours())}, {first}
        </h2>
        {loaded && (
          <p className="hm-lead">
            {taskCount === 0
              ? 'Nothing is waiting for you right now.'
              : `${taskCount} thing${taskCount === 1 ? '' : 's'} ${taskCount === 1 ? 'is' : 'are'} waiting for you.`}
          </p>
        )}
      </div>
      {error && <div className="su-note error">{error}</div>}
      {message && <div className="su-note ok">{message}</div>}

      {loaded && taskCount > 0 && (
        <section className="su-card hm-section hm-tasks">
          <h3>
            Open tasks <span className="hm-count">{taskCount}</span>
          </h3>
          <ul className="hm-list">
            {toDecide.map((t) => (
              <li key={`t${t.transfer_id}`} className="hm-task">
                <span className="hm-kind transfer">Transfer to accept</span>
                <TransferLine t={t} />
                <TransferDecision t={t} busy={busy} onDecide={(x, accept, reason) => void decide(x, accept, reason)} />
              </li>
            ))}
            {approvals.map((w) => (
              <li key={`a${w.round.code}`} className="hm-task">
                <span className="hm-kind approve">To approve</span>
                <span className="tr-line">
                  <strong>
                    {w.to_approve} cost center{w.to_approve === 1 ? '' : 's'} submitted in {w.round.name}
                  </strong>
                  <span className="ink-3">Waiting for your approval</span>
                </span>
                <span className="tr-actions">
                  <button type="button" className="su-btn sm" onClick={() => onGo(w.round.code, 'status')}>
                    Open status
                  </button>
                </span>
              </li>
            ))}
            {confirms.map((w) => (
              <li key={`c${w.round.code}`} className="hm-task">
                <span className="hm-kind approve">To confirm</span>
                <span className="tr-line">
                  <strong>
                    {w.to_confirm} investment{w.to_confirm === 1 ? '' : 's'} open in {w.round.name}
                  </strong>
                  <span className="ink-3">Waiting for your confirmation</span>
                </span>
                <span className="tr-actions">
                  <button type="button" className="su-btn sm" onClick={() => onGo(w.round.code, 'capex')}>
                    Open investments
                  </button>
                </span>
              </li>
            ))}
            {payTasks.map((w) => (
              <li key={`p${w.round.code}`} className="hm-task">
                <span className="hm-kind pay">Pay</span>
                <span className="tr-line">
                  <strong>
                    {[
                      w.pay_step === 'release' ? 'Release access control' : '',
                      w.pay_step === 'hand_over'
                        ? `Hand over pay of ${w.to_hand_over.length} cost center${w.to_hand_over.length === 1 ? '' : 's'}`
                        : '',
                      w.to_review.length ? `${w.to_review.length} cost center${w.to_review.length === 1 ? '' : 's'} to review` : '',
                      w.small_groups.length
                        ? `${w.small_groups.length} with small groups to charge elsewhere`
                        : '',
                    ]
                      .filter(Boolean)
                      .join(' · ')}{' '}
                    in {w.round.name}
                  </strong>
                  <span className="ink-3">
                    {w.pay_step === 'release'
                      ? 'The round is open: check who has access, then release it.'
                      : [...new Set([...w.to_hand_over, ...w.to_review, ...w.small_groups])].slice(0, 8).join(', ')}
                  </span>
                </span>
                <span className="tr-actions">
                  <button
                    type="button"
                    className="su-btn sm"
                    onClick={() =>
                      onOpenSheet(w.round.code, w.to_hand_over[0] ?? w.small_groups[0] ?? w.to_review[0] ?? '', 'personnel')
                    }
                  >
                    Open personnel costs
                  </button>
                </span>
              </li>
            ))}
            {assigned.map((t) => (
              <li key={`n${t.thread_id}`} className="hm-task">
                <span className="hm-kind note">Note task</span>
                <ThreadLine t={t} />
                <span className="tr-actions">
                  <button type="button" className="su-btn sm" onClick={() => onOpenThread(t)}>
                    Open
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {loaded && (
        <section className="hm-section">
          <div className="hm-head">
            <h3>Your planning</h3>
          </div>
          {home.rounds.length === 0 ? (
            <div className="su-card hm-empty">
              <p className="su-hint">
                No round is open for planning.{' '}
                {me.is_controller ? 'Open one under Setup when it is ready.' : 'Your controller opens the next one.'}
              </p>
            </div>
          ) : (
            <div className="hm-rounds">
              {home.rounds.map((w) => (
                <RoundCard key={w.round.code} work={w} now={now} onOpenSheet={onOpenSheet} onGo={onGo} />
              ))}
            </div>
          )}
        </section>
      )}

      {mine.length > 0 && (
        <section className="su-card hm-section">
          <h3>
            Your transfers <span className="ink-3">{mine.length}</span>
          </h3>
          <ul className="hm-list">
            {mine.map((t) => (
              <li key={t.transfer_id} className="hm-task">
                <span className={`su-pill ${t.status === 'proposed' ? 'submitted' : t.status === 'accepted' ? 'approved' : 'neutral'}`}>
                  {t.status === 'proposed' ? 'Waiting' : t.status === 'accepted' ? 'Accepted' : t.status === 'declined' ? 'Declined' : 'Withdrawn'}
                </span>
                <span className="tr-line">
                  <TransferLine t={t} />
                  {t.decided_by && t.status !== 'cancelled' && (
                    <span className="ink-3">
                      {t.status === 'accepted' ? 'Accepted' : 'Declined'} by {t.decided_by}
                      {t.decided_at ? `, ${WHEN.format(new Date(t.decided_at))}` : ''}
                      {t.reason ? `: “${t.reason}”` : ''}
                    </span>
                  )}
                </span>
                {t.status === 'proposed' && t.can_cancel && (
                  <span className="tr-actions">
                    <button type="button" className="su-btn ghost sm" disabled={busy} onClick={() => void withdraw(t)}>
                      Withdraw
                    </button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {readsNotes &&
        notes &&
        (
          [
            ['mentioned', 'Notes mentioning me'],
            ['started', 'My notes'],
          ] as const
        ).map(([reasonKey, title]) => {
          const list = threads.filter((t) => t.reason === reasonKey)
          if (list.length === 0) return null
          return (
            <section key={reasonKey} className="su-card mn-section">
              <h3>
                {title} <span className="ink-3">{list.length}</span>
              </h3>
              <ul className="mn-list">
                {list.map((t) => (
                  <li key={t.thread_id}>
                    <button type="button" className="mn-item" onClick={() => onOpenThread(t)}>
                      <ThreadLine t={t} />
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )
        })}
    </div>
  )
}

function ThreadLine({ t }: { t: MyThread }) {
  return (
    <span className="tr-line">
      <span className="mn-line">
        <span className="pl-code">{t.cost_center_code}</span>
        {t.cost_center_name}
        <span className="ink-3"> · </span>
        <span className="pl-code">{t.line_code}</span>
        {t.line_name}
        {t.module === 'capacity' && <span className="su-pill neutral">Capacity</span>}
        {t.module === 'capex' && <span className="su-pill neutral">Investment</span>}
      </span>
      <span className="mn-first">{t.first}</span>
      <span className="mn-meta ink-3">
        {t.notes} note{t.notes === 1 ? '' : 's'} · last by {t.last_author}, {WHEN.format(new Date(t.last_at))}
        {t.assignee && t.reason !== 'assigned' && ` · task for ${t.assignee}`}
        {' · '}opens in {t.round_code}
      </span>
    </span>
  )
}

function RoundCard({
  work,
  now,
  onOpenSheet,
  onGo,
}: {
  work: RoundWork
  now: Date
  onOpenSheet: (round: string, costCenter: string, sheet: Sheet) => void
  onGo: (round: string, page: HomeTarget) => void
}) {
  const r = work.round
  const due = r.deadline ? deadlineText(daysLeft(r.deadline, now)) : null
  const ccs = work.cost_centers
  const done = ccs.filter((c) => c.status === 'submitted' || c.status === 'approved').length
  const counts = work.counts
  const total = counts ? ORDER.reduce((a, s) => a + counts[s], 0) : 0
  return (
    <article className="su-card hm-round">
      <header className="hm-round-head">
        <div>
          <div className="su-eyebrow">
            {r.kind === 'forecast' ? 'Forecast' : 'Budget'} · FY {r.fiscal_year}
          </div>
          <h4>{r.name}</h4>
        </div>
        {due && (
          <span className={`su-pill ${due.tone}`} title={`Deadline ${r.deadline}`}>
            {due.text}
          </span>
        )}
      </header>

      {ccs.length > 0 && (
        <>
          <div className="hm-progress" aria-label={`${done} of ${ccs.length} submitted`}>
            <i style={{ width: `${(100 * done) / ccs.length}%` }} />
          </div>
          <div className="hm-progress-text ink-3">
            {done} of {ccs.length} cost center{ccs.length === 1 ? '' : 's'} submitted
          </div>
          <ul className="hm-ccs">
            {ccs.map((c) => (
              <li key={c.code}>
                <span className="hm-cc">
                  <span className="pl-code">{c.code}</span>
                  <span className="hm-cc-name">{c.name}</span>
                </span>
                <span className="hm-state">
                  <span className={`su-pill ${c.overdue ? 'overdue' : c.status}`}>
                    {c.overdue ? 'Overdue' : STATUS_LABEL[c.status]}
                  </span>
                  {c.returned && (
                    <span className="su-pill overdue" title={`${c.returned.by}: “${c.returned.comment}”`}>
                      {c.returned.how === 'rejected' ? 'Rejected' : 'Reopened'}
                    </span>
                  )}
                </span>
                <span className="hm-sheets">
                  {c.sheets.map((s) => {
                    // Pay waits for HR to release the round to its readers (ADR 0023).
                    const waiting = s === 'personnel' && work.pay_released === false
                    return (
                      <button
                        key={s}
                        type="button"
                        className="su-btn ghost sm"
                        disabled={waiting}
                        title={waiting ? 'HR hasn’t released pay for this round yet' : undefined}
                        onClick={() => onOpenSheet(r.code, c.code, s)}
                      >
                        {SHEET_LABEL[s]}
                      </button>
                    )
                  })}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {counts && total > 0 && (
        <div className="hm-overview">
          <div className="hm-stack" role="img" aria-label="Cost centers by status">
            {ORDER.map((s) => (counts[s] ? <i key={s} className={s} style={{ flex: counts[s] }} title={`${counts[s]} ${STATUS_LABEL[s]}`} /> : null))}
          </div>
          <div className="hm-legend">
            {ORDER.map((s) => (
              <span key={s}>
                <i className={s} /> {counts[s]} {STATUS_LABEL[s].toLowerCase()}
              </span>
            ))}
          </div>
          <button type="button" className="su-btn sm" onClick={() => onGo(r.code, 'status')}>
            Open status
          </button>
        </div>
      )}

      {ccs.length === 0 && !counts && <p className="su-hint">Nothing for you to plan in this round.</p>}
    </article>
  )
}
