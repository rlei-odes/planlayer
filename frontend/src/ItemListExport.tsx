import { useState } from 'react'
import type { ListSummary } from './api'

// Item lists as an Excel workbook (ADR 0017): an overview sheet, then one sheet per list. The
// user's own lists, or every list they can see; others' private lists are never included. Opened
// only when there are lists, so the chosen scope always has one.

type Scope = 'mine' | 'visible'

export default function ItemListExport({ lists, onClose }: { lists: ListSummary[]; onClose: () => void }) {
  const mine = lists.filter((l) => l.mine).length
  const [scope, setScope] = useState<Scope>(mine > 0 ? 'mine' : 'visible')
  const count = scope === 'mine' ? mine : lists.length
  const choices: [Scope, string, string, number][] = [
    ['mine', 'Mine', 'The lists you own.', mine],
    ['visible', 'Visible to me', 'Every list on this page, yours and those shared with you.', lists.length],
  ]

  return (
    <div className="il-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="il-dialog il-export"
        role="dialog"
        aria-modal="true"
        aria-labelledby="il-export-title"
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Escape') onClose()
        }}
      >
        <header className="il-head">
          <div>
            <div className="su-eyebrow">Item lists</div>
            <h3 id="il-export-title">Export to Excel</h3>
            <div className="ink-3 il-sub">An overview sheet, then one sheet per list with its items and subtotals.</div>
          </div>
          <button type="button" className="su-icon" aria-label="Close" title="Close (Esc)" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="il-body il-export-body" role="radiogroup" aria-label="Lists to export">
          {choices.map(([value, label, text, n]) => (
            <label key={value} className={`il-export-choice${scope === value ? ' on' : ''}${n === 0 ? ' off' : ''}`}>
              <input
                type="radio"
                name="il-export-scope"
                checked={scope === value}
                disabled={n === 0}
                onChange={() => setScope(value)}
              />
              <span>
                <strong>
                  {label} <span className="ink-3">· {n}</span>
                </strong>
                <span className="su-hint">{text}</span>
              </span>
            </label>
          ))}
        </div>
        <footer className="il-foot">
          <span className="su-hint">
            {count === 1 ? '1 list' : `${count} lists`}, sorted by cost center and account.
          </span>
          <button type="button" className="su-btn ghost" onClick={onClose}>
            Cancel
          </button>
          <a className="su-btn primary" href={`/api/item-lists/export.xlsx?scope=${scope}`} download onClick={onClose}>
            Download
          </a>
        </footer>
      </div>
    </div>
  )
}
