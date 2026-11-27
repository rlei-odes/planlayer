import { useEffect, useState } from 'react'
import { api, type ListSummary } from './api'
import Select from './Select'

// A controller hands a disabled user's list to themself or to someone who plans its cost center
// (ADR 0036).

export default function ItemListTakeOver({
  list,
  onDone,
  onClose,
}: {
  list: ListSummary
  onDone: () => void
  onClose: () => void
}) {
  const [people, setPeople] = useState<{ login: string; name: string }[] | null>(null)
  const [owner, setOwner] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.listOwners(list.list_id).then(setPeople, (e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [list.list_id])

  async function hand() {
    try {
      await api.takeOverList(list.list_id, owner)
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="il-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="il-dialog il-export"
        role="dialog"
        aria-modal="true"
        aria-labelledby="il-take-title"
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Escape') onClose()
        }}
      >
        <header className="il-head">
          <div>
            <div className="su-eyebrow">
              Item lists · {list.cost_center_code} · {list.account_code}
            </div>
            <h3 id="il-take-title">New owner</h3>
            <div className="ink-3 il-sub">
              {list.owner_name} is disabled. The new owner changes the list’s visibility, moves it and deletes it.
            </div>
          </div>
          <button type="button" className="su-icon" aria-label="Close" title="Close (Esc)" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="il-body il-place-body">
          <label className="su-field">
            Owner
            <Select
              label="New owner"
              value={owner}
              searchable
              placeholder="You, or someone who plans the cost center"
              options={(people ?? []).map((p) => ({ value: p.login, label: p.name, hint: p.login }))}
              onChange={setOwner}
            />
          </label>
          {error && <div className="su-note error">{error}</div>}
        </div>
        <footer className="il-foot">
          <span />
          <button type="button" className="su-btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="su-btn primary" disabled={!owner} onClick={() => void hand()}>
            Hand over
          </button>
        </footer>
      </div>
    </div>
  )
}
