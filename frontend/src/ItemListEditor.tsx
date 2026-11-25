import {
  useLayoutEffect,
  useRef,
  useState,
  type DragEvent as ReactDragEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react'
import { format, sum, times, toCents } from './amounts'
import { api, type ItemList, type ItemRow, type Visibility } from './api'
import { GripIcon } from './icons'
import { VISIBILITY } from './itemLists'
import Select from './Select'
import { LIMIT } from './limits'

// An item list in a large overlay (ADR 0017): the items behind a cost center × account's value.
// Each item is quantity × unit price; typing only a name and a unit price is enough. The server
// computes amounts and the total. From a sheet, "Save & apply total" writes the total as the
// line's year value, once; the list and the plan aren't linked afterwards.
//
// Items keep the order they are saved in. A category is a heading row: it groups the items below
// it up to the next category and shows their subtotal; items above the first category belong to
// none. Rows move by their handle or with Alt+↑/↓; a category moves with its items, and only
// between categories.

type Kind = NonNullable<ItemRow['kind']>

/** A row as edited: every field a string, and a key of its own that stays with it as rows move. */
type Row = { key: number; kind: Kind; name: string; supplier: string; quantity: string; unit_price: string; comment: string }

/** The rows a drag or a move takes: one item, or a category with its items. */
type Span = { start: number; end: number }

const DRAG_TYPE = 'application/x-planlayer-item-row'

let lastKey = 0

function blank(kind: Kind = 'item'): Row {
  return { key: ++lastKey, kind, name: '', supplier: '', quantity: '1', unit_price: '', comment: '' }
}

function fromEntry(e: ItemRow): Row {
  return {
    key: ++lastKey,
    kind: e.kind ?? 'item',
    name: e.name,
    supplier: e.supplier ?? '',
    quantity: e.quantity ?? '1',
    unit_price: e.unit_price ?? '',
    comment: e.comment ?? '',
  }
}

function toEntry(r: Row): ItemRow {
  return r.kind === 'heading'
    ? { kind: 'heading', name: r.name }
    : { kind: 'item', name: r.name, supplier: r.supplier, quantity: r.quantity, unit_price: r.unit_price, comment: r.comment }
}

const isBlank = (r: Row) => !r.name.trim() && !r.supplier.trim() && !r.unit_price.trim() && !r.comment.trim()

/** The end (exclusive) of the rows that move with row i: itself, and a category's items. */
function spanEnd(rows: Row[], i: number): number {
  if (rows[i].kind === 'item') return i + 1
  let j = i + 1
  while (j < rows.length && rows[j].kind === 'item') j++
  return j
}

/** A gap is the place before row `gap` (rows.length: after the last). An item goes into any gap;
 * a category only before another category or at the end, so it never splits a group. */
function canDrop(rows: Row[], span: Span, gap: number): boolean {
  return rows[span.start].kind === 'item' || gap === rows.length || rows[gap].kind === 'heading'
}

/** Dropping into a gap at or inside the moved rows leaves them where they are. */
const stays = (span: Span, gap: number) => gap >= span.start && gap <= span.end

function move(rows: Row[], { start, end }: Span, gap: number): Row[] {
  if (gap >= start && gap <= end) return rows
  const rest = [...rows.slice(0, start), ...rows.slice(end)]
  const at = gap > end ? gap - (end - start) : gap
  return [...rest.slice(0, at), ...rows.slice(start, end), ...rest.slice(at)]
}

/** Where Alt+↑/↓ takes row i: past the row above or below; a category past the category above
 * or the next category with its items. */
function stepGap(rows: Row[], i: number, up: boolean): number | null {
  const end = spanEnd(rows, i)
  if (rows[i].kind === 'item') return up ? (i > 0 ? i - 1 : null) : end < rows.length ? i + 2 : null
  if (up) {
    for (let j = i - 1; j >= 0; j--) if (rows[j].kind === 'heading') return j
    return null
  }
  return end < rows.length ? spanEnd(rows, end) : null
}

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
  const [rows, setRows] = useState<Row[]>(() => (list && list.entries.length ? list.entries.map(fromEntry) : [blank()]))
  const [visibility, setVisibility] = useState<Visibility>(list?.visibility ?? 'private')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const editable = list ? list.can_edit : canCreate
  const table = useRef<HTMLTableElement>(null)
  const body = useRef<HTMLTableSectionElement>(null)

  // After rows are added or moved, the cursor goes to the given row and field.
  const focusAt = useRef<{ key: number; col: number } | null>(null)
  useLayoutEffect(() => {
    const at = focusAt.current
    if (at === null) return
    focusAt.current = null
    const inputs = table.current?.querySelector(`tr[data-key="${at.key}"]`)?.querySelectorAll('input')
    inputs?.[Math.min(at.col, inputs.length - 1)]?.focus()
  }, [rows])

  function insert(at: number, kind: Kind) {
    const row = blank(kind)
    focusAt.current = { key: row.key, col: 0 }
    setRows((rs) => [...rs.slice(0, at), row, ...rs.slice(at)])
  }

  /** A category at the end; a blank item left there becomes its first item. */
  function addCategory() {
    const last = rows[rows.length - 1]
    insert(last && last.kind === 'item' && isBlank(last) ? rows.length - 1 : rows.length, 'heading')
  }

  function remove(i: number) {
    setRows((rs) => (rs.length > 1 ? rs.filter((_, j) => j !== i) : [blank()]))
  }

  function change(i: number, patch: Partial<Row>) {
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  }

  // Enter adds an item below, as in a spreadsheet; Ctrl+Enter starts a category at the row;
  // Alt+↑/↓ moves the row, and the cursor stays in its field.
  const onKeys = (i: number) => (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.nativeEvent.isComposing) return
    if (e.key === 'Enter') {
      e.preventDefault()
      if (e.ctrlKey || e.metaKey) insert(i, 'heading')
      else insert(i + 1, 'item')
    } else if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
      e.preventDefault()
      const gap = stepGap(rows, i, e.key === 'ArrowUp')
      if (gap === null) return
      const inputs = [...(e.currentTarget.closest('tr')?.querySelectorAll('input') ?? [])]
      focusAt.current = { key: rows[i].key, col: inputs.indexOf(e.currentTarget) }
      setRows(move(rows, { start: i, end: spanEnd(rows, i) }, gap))
    }
  }

  // Dragging by the handle: the rows stay put while dragging, a marker shows where the dragged
  // rows will go, and they move once on drop.
  const drag = useRef<Span | null>(null)
  const [dragged, setDragged] = useState<Span | null>(null)
  const [gap, setGap] = useState<number | null>(null)

  const startDrag = (i: number) => (e: ReactDragEvent<HTMLSpanElement>) => {
    const span = { start: i, end: spanEnd(rows, i) }
    drag.current = span
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData(DRAG_TYPE, String(i))
    const tr = e.currentTarget.closest('tr')
    if (tr) {
      const box = tr.getBoundingClientRect()
      e.dataTransfer.setDragImage(tr, e.clientX - box.left, e.clientY - box.top)
    }
    // Dimmed only after the browser took its picture of the row.
    requestAnimationFrame(() => setDragged(span))
  }

  function endDrag() {
    drag.current = null
    setDragged(null)
    setGap(null)
  }

  /** The gap nearest the pointer that the dragged rows may go into. */
  function gapAt(clientY: number): number | null {
    const span = drag.current
    const trs = body.current ? [...body.current.rows] : []
    if (span === null || trs.length === 0) return null
    let best: number | null = null
    let distance = Infinity
    for (let g = 0; g <= trs.length; g++) {
      if (!canDrop(rows, span, g)) continue
      const y = g < trs.length ? trs[g].getBoundingClientRect().top : trs[trs.length - 1].getBoundingClientRect().bottom
      if (Math.abs(clientY - y) < distance) {
        best = g
        distance = Math.abs(clientY - y)
      }
    }
    return best
  }

  function onDragOver(e: ReactDragEvent<HTMLTableSectionElement>) {
    if (drag.current === null) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    setGap(gapAt(e.clientY))
  }

  function onDrop(e: ReactDragEvent<HTMLTableSectionElement>) {
    const span = drag.current
    if (span === null) return
    e.preventDefault()
    const g = gapAt(e.clientY)
    if (g !== null) setRows((rs) => move(rs, span, g))
    endDrag()
  }

  const owner = list ? list.mine : true

  const amounts = rows.map((r) => (r.kind === 'item' ? times(r.quantity || '1', r.unit_price) : undefined))
  // Each category's subtotal: the items below it, up to the next category.
  const subtotals = rows.map((r, i) =>
    r.kind === 'heading' ? (sum(amounts.slice(i + 1, spanEnd(rows, i)).map((a) => a ?? null)) ?? '0.00') : null,
  )
  const firstHeading = rows.findIndex((r) => r.kind === 'heading')
  const kept = rows.filter((r) => (r.kind === 'heading' ? r.name.trim() : r.name.trim() || r.unit_price.trim()))
  const invalid = rows.some(
    (r, i) => r.kind === 'item' && (r.name.trim() || r.unit_price.trim()) && (amounts[i] === undefined || !r.name.trim()),
  )
  const total = sum(amounts.map((a) => a ?? null)) ?? '0.00'
  // A line without a year value yet has nothing to compare with: no message, just apply.
  const lineSet = lineValue !== undefined && lineValue !== null && toCents(lineValue) !== 0n
  const differs = lineSet && lineValue !== total
  const dirty =
    !list ||
    visibility !== list.visibility ||
    JSON.stringify(kept.map(clean)) !== JSON.stringify(list.entries.map(fromEntry).map(clean))
  // The marker: before the gap's row, or below the last row; none where nothing would move.
  const marker = gap !== null && dragged !== null && !stays(dragged, gap) ? gap : null

  async function save(apply: boolean) {
    setBusy(true)
    setError(null)
    try {
      const items = kept.map(toEntry)
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

  async function deleteList() {
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

  function rowClass(r: Row, i: number): string {
    return [
      r.kind === 'heading' ? 'il-heading' : firstHeading !== -1 && i > firstHeading ? 'il-in-group' : '',
      dragged !== null && i >= dragged.start && i < dragged.end ? 'il-dragging' : '',
      marker === i ? 'il-drop-before' : '',
      marker === rows.length && i === rows.length - 1 ? 'il-drop-after' : '',
    ]
      .filter(Boolean)
      .join(' ')
  }

  function handleCells(r: Row, i: number) {
    const heading = r.kind === 'heading'
    return (
      <>
        <td>
          {editable && (
            <button
              type="button"
              className="nt-link il-remove"
              aria-label={heading ? 'Remove category' : 'Remove item'}
              title={heading ? 'Remove the category; its items stay' : 'Remove the item'}
              onClick={() => remove(i)}
            >
              ×
            </button>
          )}
        </td>
        <td className="il-grip-cell">
          {editable && (
            <span
              className="il-grip"
              draggable
              title={heading ? 'Drag to move the category with its items (Alt+↑/↓)' : 'Drag to move (Alt+↑/↓)'}
              onDragStart={startDrag(i)}
              onDragEnd={endDrag}
            >
              <GripIcon />
            </span>
          )}
        </td>
      </>
    )
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
              <col className="il-grip-col" />
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
                <th />
              </tr>
            </thead>
            <tbody ref={body} onDragOver={onDragOver} onDrop={onDrop}
              onDragLeave={(e) => !e.currentTarget.contains(e.relatedTarget as Node | null) && setGap(null)}>
              {rows.map((r, i) =>
                r.kind === 'heading' ? (
                  <tr key={r.key} data-key={r.key} className={rowClass(r, i)}>
                    <td colSpan={4}>
                      <input maxLength={300} className="su-input" value={r.name} disabled={!editable}
                        placeholder="Category, e.g. ERP" aria-label="Category"
                        onChange={(e) => change(i, { name: e.target.value })} onKeyDown={onKeys(i)} />
                    </td>
                    <td className="num il-amount-cell" title="Subtotal of the category">
                      {format(subtotals[i])}
                    </td>
                    <td />
                    {handleCells(r, i)}
                  </tr>
                ) : (
                  <tr key={r.key} data-key={r.key} className={rowClass(r, i)}>
                    <td>
                      <input maxLength={300} className="su-input" value={r.name} disabled={!editable} placeholder="e.g. Cloud subscription"
                        onChange={(e) => change(i, { name: e.target.value })} onKeyDown={onKeys(i)} />
                    </td>
                    <td>
                      <input maxLength={300} className="su-input" value={r.supplier} disabled={!editable}
                        onChange={(e) => change(i, { supplier: e.target.value })} onKeyDown={onKeys(i)} />
                    </td>
                    <td>
                      <input maxLength={LIMIT.number} className="su-input num" value={r.quantity} disabled={!editable} inputMode="decimal"
                        onChange={(e) => change(i, { quantity: e.target.value })} onKeyDown={onKeys(i)} />
                    </td>
                    <td>
                      <input maxLength={LIMIT.number}
                        className={`su-input num${r.unit_price.trim() && amounts[i] === undefined ? ' invalid' : ''}`}
                        value={r.unit_price}
                        disabled={!editable}
                        inputMode="decimal"
                        placeholder="0.00"
                        onChange={(e) => change(i, { unit_price: e.target.value })}
                        onKeyDown={onKeys(i)}
                      />
                    </td>
                    <td className="num il-amount-cell">{format(amounts[i] ?? null)}</td>
                    <td>
                      <input maxLength={LIMIT.longText} className="su-input" value={r.comment} disabled={!editable}
                        onChange={(e) => change(i, { comment: e.target.value })} onKeyDown={onKeys(i)} />
                    </td>
                    {handleCells(r, i)}
                  </tr>
                ),
              )}
            </tbody>
          </table>
          {editable && (
            <div className="il-adds">
              <button type="button" className="nt-link" onClick={() => insert(rows.length, 'item')}>
                + Item
              </button>
              <button type="button" className="nt-link" title="Groups the items below it, with their subtotal" onClick={addCategory}>
                + Category
              </button>
              <span className="ink-3 il-keys">
                Enter: new item · Ctrl+Enter: new category here · Alt+↑/↓ or drag: move
              </span>
            </div>
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
                  <button type="button" className="su-btn ghost danger" disabled={busy} onClick={() => void deleteList()}>
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

/** A row as compared for changes: its fields as entered, without the computed amount. */
function clean(r: Row) {
  return [r.kind, r.name.trim(), r.supplier.trim(), r.quantity.trim(), r.unit_price.trim(), r.comment.trim()]
}
