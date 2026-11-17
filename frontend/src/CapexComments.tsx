import { useEffect, useState } from 'react'
import { api, type CapexComment, type CapexItem } from './api'
import { STATUS_TEXT } from './capex'

// An investment's comments (ADR 0028): written by everyone who reads it, deleted by their author.
// Confirming, declining or reopening with a reason adds the reason here.

const WHEN = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })

export default function CapexComments({
  version,
  item,
  onChanged,
  onClose,
}: {
  version: string
  item: CapexItem
  onChanged: () => void
  onClose: () => void
}) {
  const [comments, setComments] = useState<CapexComment[] | null>(null)
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.capexComments(version, item.item_id).then(setComments, (e: unknown) => setError(String(e)))
  }, [version, item.item_id])

  async function run(call: () => Promise<CapexComment[]>) {
    setBusy(true)
    try {
      setComments(await call())
      setError(null)
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function add() {
    if (!text.trim()) return
    await run(() => api.capexAddComment(version, item.item_id, text))
    setText('')
  }

  return (
    <div className="il-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="il-dialog cx-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={`Comments on ${item.name}`}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Escape') onClose()
        }}
      >
        <header className="il-head">
          <div>
            <div className="su-eyebrow">Comments</div>
            <h3>
              <span className="pl-code">{item.item_id}</span> {item.name}
            </h3>
            <div className="il-sub ink-3">
              {item.cost_center_code} {item.cost_center_name} · {STATUS_TEXT[item.status]}
              {item.status_by ? ` by ${item.status_by}` : ''}
            </div>
          </div>
          <button type="button" className="su-icon" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        {error && <div className="su-note error">{error}</div>}
        <div className="il-body">
          {comments?.length === 0 && <p className="su-hint">No comments yet.</p>}
          <ul className="cx-comments">
            {comments?.map((c) => (
              <li key={c.comment_id}>
                <div className="cx-comment-head">
                  <strong>{c.author}</strong>
                  <span className="ink-3">{WHEN.format(new Date(c.created_at))}</span>
                  {c.mine && (
                    <button
                      type="button"
                      className="nt-link"
                      disabled={busy}
                      onClick={() => void run(() => api.capexDeleteComment(version, item.item_id, c.comment_id))}
                    >
                      Delete
                    </button>
                  )}
                </div>
                <p>{c.body}</p>
              </li>
            ))}
          </ul>
        </div>
        <footer className="il-foot cx-comment-new">
          <textarea
            className="su-input"
            rows={2}
            value={text}
            placeholder="Comment"
            aria-label="New comment"
            autoFocus
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) void add()
            }}
          />
          <button type="button" className="su-btn primary" disabled={busy || !text.trim()} onClick={() => void add()}>
            Add
          </button>
        </footer>
      </div>
    </div>
  )
}
