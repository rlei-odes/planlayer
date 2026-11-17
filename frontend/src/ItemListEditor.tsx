import { useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { format, sum, times, toCents } from './amounts'
import { api, type ItemList, type ItemRow, type Visibility } from './api'
import { VISIBILITY } from './itemLists'
import Select from './Select'
import { LIMIT } from './limits'

// An item list in a large overlay (ADR 0017): the items behind a cost center × account's value.
// Each item is quantity × unit price; typing only a name and a unit price is enough. The server
// computes amounts and the total. From a sheet, "Save & apply total" writes the total as the
// line's year value, once; the list and the plan aren't linked afterwards.

const BLANK: ItemRow = { name: '', supplier: '', quantity: '1', unit_price: '', comment: '' }

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export default function ItemListEditor({
  list,
  costCenter,
  account,
  title,
  lineValue,
  canCreate,
  onApply,
  onSaved,
  onClose,
}: {
  /** The line's list, or null when it has none yet. */
  list: ItemList | null
  costCenter: string
  account: string
  title: string
  /** The line's year value on the sheet, to compare with the total. */
  lineValue?: string | null
  /** The user may plan the cost center, so may start a list on the line. */
  canCreate: boolean
  /** From a sheet whose line takes a year total: apply the list's total to it. */
  onApply?: (total: string) => Promise<void>
  onSaved: (list: ItemList | null) => void
  onClose: () => void
}) {
  const [rows, setRows] = useState<ItemRow[]>(() => (list ? list.entries.map((e) => ({ ...e })) : [{ ...BLANK }]))
  const [visibility, setVisibility] = useState<Visibility>(list?.visibility ?? 'private')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const editable = list ? list.can_edit : canCreate
  // Enter in an item adds one below it and moves to its name, as in a spreadsheet.
  const table = useRef<HTMLTableElement>(null)
  const focusRow = useRef<number | null>(null)
  useLayoutEffect(() => {
    if (focusRow.current === null) return
    table.current?.querySelectorAll('tbody tr')[focusRow.current]?.querySelector('input')?.focus()
    focusRow.current = null
  }, [rows])
  function newRowAfter(i: number) {
    focusRow.current = i + 1
    setRows((rs) => [...rs.slice(0, i + 1), { ...BLANK }, ...rs.slice(i + 1)])
  }
  const onEnter = (i: number) => (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
      e.preventDefault()
      newRowAfter(i)
    }
  }
  const owner = list ? list.mine : true

  const amounts = rows.map((r) => times(r.quantity ?? '1', r.unit_price))
  const kept = rows.filter((r) => r.name.trim() || r.unit_price.trim())
  const invalid = rows.some((r, i) => (r.name.trim() || r.unit_price.trim()) && (amounts[i] === undefined || !r.name.trim()))
  const total = sum(amounts.map((a) => a ?? null)) ?? '0.00'
  // A line without a year value yet has nothing to compare with: no message, just apply.
  const lineSet = lineValue !== undefined && lineValue !== null && toCents(lineValue) !== 0n
  const differs = lineSet && lineValue !== total
  const dirty =
    !list ||
    visibility !== list.visibility ||
    JSON.stringify(kept.map(clean)) !== JSON.stringify(list.entries.map(clean))

  function change(i: number, patch: Partial<ItemRow>) {
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  }

  async function save(apply: boolean) {
    setBusy(true)
    setError(null)
    try {
      const items = kept.map((r) => ({ ...r, amount: null }))
      const saved = list
        ? await api.saveList(list.list_id, visibility, items)
        : await api.createList(costCenter, account, visibility, items)
      onSaved(saved)
      if (apply && onApply) await onApply(saved.total)
      onClose()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!list) return
    setBusy(true)
    try {
      await api.deleteList(list.list_id)
      onSaved(null)
      onClose()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="il-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      {/* Keys stay in the dialog: the sheet's grid listens on the whole page and would take Tab
          and the arrows away from the fields. */}
      <div
        className="il-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={`Item list ${title}`}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Escape') onClose()
        }}
      >
        <header className="il-head">
          <div>
            <div className="su-eyebrow">Item list</div>
            <h3>{title}</h3>
            {list && (
              <div className="ink-3 il-sub">
                {list.mine ? 'Yours' : `By ${list.owner_name}`} · last changed by {list.changed_by} ·{' '}
                {new Date(list.changed_at).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })}
              </div>
            )}
          </div>
          <div className="il-vis">
            {owner && editable ? (
              <div className="su-field il-w-vis">
                Visible to
                <Select<Visibility>
                  label="Visible to"
                  value={visibility}
                  options={(Object.keys(VISIBILITY) as Visibility[]).map((v) => ({
                    value: v,
                    label: VISIBILITY[v].label,
                    hint: VISIBILITY[v].hint,
                  }))}
                  onChange={setVisibility}
                />
              </div>
            ) : (
              <span className="su-pill neutral" title={VISIBILITY[visibility].hint}>
                {VISIBILITY[visibility].label}
              </span>
            )}
            <button type="button" className="su-icon" aria-label="Close" title="Close (Esc)" onClick={onClose}>
              ×
            </button>
          </div>
        </header>
        {!list && !canCreate && (
          <div className="su-note">There is no item list on this line. Those who plan the cost center can start one.</div>
        )}
        <div className="il-body">
          <table className="il-table" ref={table}>
            <colgroup>
              <col className="il-name" />
              <col className="il-supplier" />
              <col className="il-qty" />
              <col className="il-price" />
              <col className="il-amount" />
              <col className="il-comment" />
              <col className="il-x" />
            </colgroup>
            <thead>
              <tr>
                <th>Item</th>
                <th>Supplier</th>
                <th className="num">Qty</th>
                <th className="num">Unit price</th>
                <th className="num">Amount</th>
                <th>Comment</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td>
                    <input maxLength={300} className="su-input" value={r.name} disabled={!editable} placeholder="e.g. Cloud subscription"
                      onChange={(e) => change(i, { name: e.target.value })} onKeyDown={onEnter(i)} />
                  </td>
                  <td>
                    <input maxLength={300} className="su-input" value={r.supplier ?? ''} disabled={!editable}
                      onChange={(e) => change(i, { supplier: e.target.value })} onKeyDown={onEnter(i)} />
                  </td>
                  <td>
                    <input maxLength={LIMIT.number} className="su-input num" value={r.quantity ?? '1'} disabled={!editable} inputMode="decimal"
                      onChange={(e) => change(i, { quantity: e.target.value })} onKeyDown={onEnter(i)} />
                  </td>
                  <td>
                    <input maxLength={LIMIT.number}
                      className={`su-input num${r.unit_price.trim() && amounts[i] === undefined ? ' invalid' : ''}`}
                      value={r.unit_price}
                      disabled={!editable}
                      inputMode="decimal"
                      placeholder="0.00"
                      onChange={(e) => change(i, { unit_price: e.target.value })}
                      onKeyDown={onEnter(i)}
                    />
                  </td>
                  <td className="num il-amount-cell">{format(amounts[i] ?? null)}</td>
                  <td>
                    <input maxLength={LIMIT.longText} className="su-input" value={r.comment ?? ''} disabled={!editable}
                      onChange={(e) => change(i, { comment: e.target.value })} onKeyDown={onEnter(i)} />
                  </td>
                  <td>
                    {editable && (
                      <button type="button" className="nt-link" aria-label="Remove item"
                        onClick={() => setRows((rs) => (rs.length > 1 ? rs.filter((_, j) => j !== i) : [{ ...BLANK }]))}>
                        ×
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {editable && (
            <button type="button" className="nt-link il-add" onClick={() => newRowAfter(rows.length - 1)}>
              + Item
            </button>
          )}
        </div>
        <footer className="il-foot">
          <div className="il-totals">
            <span>
              Total <strong>{format(total)}</strong>
            </span>
            {lineSet && (
              <span className={differs ? 'il-differs' : 'ink-3'}>
                Line’s year value {format(lineValue ?? null)}
                {differs ? ' · differs' : ' · matches'}
              </span>
            )}
          </div>
          {error && <span className="su-note error il-error">{error}</span>}
          <span className="nt-actions">
            {list?.mine &&
              (confirmDelete ? (
                <>
                  <button type="button" className="su-btn ghost danger" disabled={busy} onClick={() => void remove()}>
                    Delete the list
                  </button>
                  <button type="button" className="su-btn ghost" onClick={() => setConfirmDelete(false)}>
                    Keep
                  </button>
                </>
              ) : (
                <button type="button" className="su-btn ghost" onClick={() => setConfirmDelete(true)}>
                  Delete…
                </button>
              ))}
            <button type="button" className="su-btn ghost" onClick={onClose}>
              {editable && dirty ? 'Cancel' : 'Close'}
            </button>
            {editable && (
              <button type="button" className="su-btn" disabled={busy || invalid || !dirty} onClick={() => void save(false)}>
                Save
              </button>
            )}
            {editable && onApply && (
              <button
                type="button"
                className="su-btn primary"
                disabled={busy || invalid}
                title="Save, then write the total as the line’s year value, spread as its badge says"
                onClick={() => void save(true)}
              >
                Save & apply total
              </button>
            )}
          </span>
        </footer>
      </div>
    </div>
  )
}

/** An item as compared for changes: its fields as entered, without the computed amount. */
function clean(r: ItemRow) {
  return [r.name.trim(), (r.supplier ?? '').trim(), (r.quantity ?? '1').trim(), r.unit_price.trim(), (r.comment ?? '').trim()]
}
