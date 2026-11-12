import { useEffect, useState } from 'react'
import { api, type MyNotes, type MyThread } from './api'

// The user's open threads (ADR 0017): tasks assigned to them, threads that mention them, and
// threads they started. Each opens its sheet with the line's notes.

const WHEN = new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short' })

const SECTIONS: { reason: MyThread['reason']; title: string; empty: string }[] = [
  { reason: 'assigned', title: 'Tasks for me', empty: 'No open tasks assigned to you.' },
  { reason: 'mentioned', title: 'Mentioning me', empty: 'No open threads mention you.' },
  { reason: 'started', title: 'Started by me', empty: 'You have no open threads.' },
]

export default function MyNotesPage({ onOpen }: { onOpen: (thread: MyThread) => void }) {
  const [data, setData] = useState<MyNotes | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.myNotes().then(setData, (e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  return (
    <div className="su mn">
      <div className="su-head">
        <div>
          <div className="su-eyebrow">Notes</div>
          <h2 className="su-title">My notes</h2>
        </div>
      </div>
      {error && <div className="su-note error">{error}</div>}
      {data &&
        SECTIONS.map((s) => {
          const threads = data.threads.filter((t) => t.reason === s.reason)
          return (
            <section key={s.reason} className="su-card mn-section">
              <h3>
                {s.title} <span className="ink-3">{threads.length || ''}</span>
              </h3>
              {threads.length === 0 ? (
                <p className="su-hint mn-empty">{s.empty}</p>
              ) : (
                <ul className="mn-list">
                  {threads.map((t) => (
                    <li key={t.thread_id}>
                      <button type="button" className="mn-item" onClick={() => onOpen(t)}>
                        <span className="mn-line">
                          <span className="pl-code">{t.cost_center_code}</span>
                          {t.cost_center_name}
                          <span className="ink-3"> · </span>
                          <span className="pl-code">{t.line_code}</span>
                          {t.line_name}
                          {t.module === 'capacity' && <span className="su-pill neutral">Capacity</span>}
                        </span>
                        <span className="mn-first">{t.first}</span>
                        <span className="mn-meta ink-3">
                          {t.notes} note{t.notes === 1 ? '' : 's'} · last by {t.last_author},{' '}
                          {WHEN.format(new Date(t.last_at))}
                          {t.assignee && s.reason !== 'assigned' && ` · task for ${t.assignee}`}
                          {' · '}opens in {t.round_code}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )
        })}
    </div>
  )
}
