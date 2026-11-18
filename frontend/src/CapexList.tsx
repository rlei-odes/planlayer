import { useMemo, useState, type ClipboardEvent, type KeyboardEvent, type ReactNode } from 'react'
import { detail, display, format, fromCents, numberFormat, parse, sum, toCents } from './amounts'
import { type Capex, type CapexChange, type CapexItem, type CapexNew } from './api'
import { monthLabel, parseMonth, scheduleState, STATUS_TEXT } from './capex'
import { noteMarker } from './lineMarks'
import MonthPicker from './MonthPicker'
import Select, { type Option } from './Select'
import { LIMIT } from './limits'

// The investments list (ADR 0028): one row per investment, large projects as group rows summing
// their lines (collapsible) with a row of their own to add a line, and a last row that adds an
// investment as soon as its name is typed.
//
// Name and amount are typed; cost center, type, priority and start are picked from a list. A
// block pasted from a spreadsheet fills the columns from the field it lands in, row by row.
// Enter and the up and down arrows move within a column; Escape takes a typed value back.

type Field = 'name' | 'cost_center' | 'type' | 'priority' | 'start_month' | 'amount'
// An investment, or a row that adds one: on its own (project null) or to a large project.
type Row = { item: CapexItem; project?: undefined } | { item: null; project: number | null }
type Pick = { prop: Field; row: number; anchor: DOMRect }

// The columns that take typed values, in the order a pasted block fills them.
const TYPED: Field[] = ['name', 'cost_center', 'type', 'priority', 'start_month', 'amount']

export type ListActions = {
  /** Add and change investments in one request; the list is reloaded when it is refused. */
  save: (create: CapexNew[], update: CapexChange[]) => Promise<void>
  openMenu: (item: CapexItem, anchor: DOMRect) => void
  openStatus: (item: CapexItem, anchor: DOMRect) => void
  openCashOut: (item: CapexItem) => void
  openNotes: (item: CapexItem) => void
  error: (text: string) => void
}

const draftKey = (project: number | null) => (project === null ? 'top' : String(project))

function draftIn(drafts: Record<string, CapexNew>, row: Row | undefined): CapexNew {
  return row && row.item === null ? (drafts[draftKey(row.project)] ?? {}) : {}
}

/** The fields a user may change on a row. */
function editable(row: Row | undefined, prop: Field, locked: boolean, canAdd: boolean): boolean {
  if (!row || locked) return false
  const i = row.item
  if (i === null) {
    if (row.project !== null) return ['name', 'type', 'start_month', 'amount'].includes(prop)
    return canAdd
  }
  if (!i.can_write) return false
  switch (prop) {
    case 'name':
      return true
    case 'cost_center':
    case 'priority':
      return i.project_id === null
    case 'type':
    case 'start_month':
    case 'amount':
      return i.kind === 'line'
  }
}

function firstWord(raw: string): string {
  return raw.trim().split(/\s+/)[0] ?? ''
}

/** A typed or pasted value as the API's value for the field; an error text when it isn't one. */
function valueOf(prop: Field, raw: string, view: Capex): { value: string | number | null } | { error: string } {
  const text = raw.trim()
  switch (prop) {
    case 'name':
      return text ? { value: text } : { error: 'an investment needs a name' }
    case 'cost_center': {
      const code = firstWord(text)
      const found = view.cost_centers.find((c) => c.code.toLowerCase() === code.toLowerCase())
      return found ? { value: found.code } : { error: `no cost center “${code}” in this round` }
    }
    case 'type': {
      if (!text) return { value: null }
      const code = firstWord(text)
      const found = view.types.find((t) => t.code.toLowerCase() === code.toLowerCase() || t.name.toLowerCase() === text.toLowerCase())
      return found ? { value: found.code } : { error: `no investment type “${text}”` }
    }
    case 'priority': {
      const n = Number(text || '2')
      return [1, 2, 3].includes(n) ? { value: n } : { error: `priority “${text}” is not 1, 2 or 3` }
    }
    case 'start_month': {
      const month = /^\d{4}-\d{2}-01$/.test(text) ? text : parseMonth(text)
      return month === undefined ? { error: `“${text}” is not a month, e.g. 03.2026` } : { value: month }
    }
    case 'amount': {
      const amount = parse(text)
      if (amount === undefined) return { error: `“${text}” is not a number` }
      return amount !== null && amount.startsWith('-') ? { error: 'an amount is not negative' } : { value: amount }
    }
  }
}

/** A pasted text as rows of cells, when it is more than one cell. */
function block(text: string): string[][] | null {
  if (!/[\t\n]/.test(text.replace(/\r?\n$/, ''))) return null
  const rows = text.replace(/\r/g, '').split('\n').map((line) => line.split('\t'))
  while (rows.length > 1 && rows[rows.length - 1].every((v) => v === '')) rows.pop()
  return rows
}

/** A typed field: committed when it loses focus with a changed value. */
function CellInput({
  value,
  onCommit,
  onPasteBlock,
  col,
  row,
  label,
  placeholder,
  numeric,
  clearOnCommit,
  display,
}: {
  value: string
  onCommit: (raw: string) => void
  onPasteBlock: (rows: string[][]) => void
  col: Field
  row: number
  label: string
  placeholder?: string
  numeric?: boolean
  /** An add row's field: empty again once its value is taken. */
  clearOnCommit?: boolean
  /** Shown while the field isn't being edited (an amount in the user's format); editing shows
   * the exact value. */
  display?: string
}) {
  const [text, setText] = useState(value)
  const [editing, setEditing] = useState(false)
  // A new value from the server replaces what is shown.
  const [shown, setShown] = useState(value)
  if (shown !== value) {
    setShown(value)
    setText(value)
  }
  return (
    <input maxLength={numeric ? LIMIT.number : LIMIT.name}
      className={`cx-field${numeric ? ' num' : ''}`}
      data-col={col}
      data-row={row}
      aria-label={label}
      placeholder={placeholder}
      value={editing || display === undefined || text !== value ? text : display}
      onChange={(e) => setText(e.target.value)}
      onFocus={() => setEditing(true)}
      onBlur={() => {
        setEditing(false)
        if (text === value) return
        onCommit(text)
        if (clearOnCommit) setText(value)
      }}
      onKeyDown={(e) => {
        if (e.key !== 'Escape') return
        const input = e.currentTarget
        setText(value)
        // Taken back first: the blur that follows sees nothing changed.
        requestAnimationFrame(() => input.blur())
      }}
      onPaste={(e: ClipboardEvent<HTMLInputElement>) => {
        const rows = block(e.clipboardData.getData('text'))
        if (!rows) return
        e.preventDefault()
        setText(value)
        onPasteBlock(rows)
      }}
    />
  )
}

/** A picked field: its value and a caret, opening the list on a click, Enter or Space. */
function PickButton({
  content,
  onOpen,
  col,
  row,
  label,
  center,
}: {
  content: ReactNode
  onOpen: (anchor: DOMRect) => void
  col: Field
  row: number
  label: string
  center?: boolean
}) {
  return (
    <button
      type="button"
      className={`cx-field cx-pick${center ? ' cx-pick-mid' : ''}`}
      data-col={col}
      data-row={row}
      aria-label={label}
      aria-haspopup="listbox"
      onClick={(e) => onOpen(e.currentTarget.getBoundingClientRect())}
    >
      <span className="cx-pick-text">{content}</span>
      <span className="cx-caret" aria-hidden="true" />
    </button>
  )
}

export default function CapexList({
  view,
  items,
  locked,
  selected,
  onSelect,
  defaultCostCenter,
  actions,
}: {
  view: Capex
  /** The investments shown (filtered), projects followed by their lines. */
  items: CapexItem[]
  locked: boolean
  selected: Set<number>
  onSelect: (ids: Set<number>) => void
  /** The cost center a new investment starts on. */
  defaultCostCenter: string | null
  actions: ListActions
}) {
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())
  const [pick, setPick] = useState<Pick | null>(null)
  // What is typed or picked on an add row before its name: the investment is added with it.
  const [drafts, setDrafts] = useState<Record<string, CapexNew>>({})
  const cashOut = view.settings.cash_out
  const canAdd = !locked && view.cost_centers.some((c) => c.can_write)
  const typeNames = useMemo(() => new Map(view.types.map((t) => [t.code, t.name])), [view.types])
  const ccNames = useMemo(() => new Map(view.cost_centers.map((c) => [c.code, c.name])), [view.cost_centers])

  const rows = useMemo<Row[]>(() => {
    const out: Row[] = []
    for (const item of items) {
      if (item.project_id !== null) continue
      out.push({ item })
      if (item.kind !== 'project' || collapsed.has(item.item_id)) continue
      for (const line of items) if (line.project_id === item.item_id) out.push({ item: line })
      if (item.can_write && !locked) out.push({ item: null, project: item.item_id })
    }
    if (canAdd) out.push({ item: null, project: null })
    return out
  }, [items, collapsed, canAdd, locked])

  const lines = items.filter((i) => i.kind === 'line')
  const total = sum(lines.filter((i) => i.status !== 'declined').map((i) => i.amount))
  const allIds = items.filter((i) => i.project_id === null).map((i) => i.item_id)
  const allSelected = allIds.length > 0 && allIds.every((id) => selected.has(id))
  const can = (row: Row | undefined, prop: Field) => editable(row, prop, locked, canAdd)

  /** A new investment from an add row: on its own, or a line of the row's project. */
  function created(row: Row, values: CapexNew): CapexNew {
    if (row.item !== null) return values
    if (row.project !== null) return { ...values, kind: 'line', project_id: row.project, cost_center: undefined, priority: undefined }
    return { ...values, cost_center: values.cost_center ?? defaultCostCenter }
  }

  /** A typed or picked value: a change of an investment, or a value of an add row, which adds
   * the investment once it has a name. */
  function commit(rowIndex: number, prop: Field, raw: string) {
    const row = rows[rowIndex]
    if (!can(row, prop)) return
    const parsed = valueOf(prop, raw, view)
    if ('error' in parsed) return actions.error(parsed.error)
    const i = row.item
    if (i !== null) {
      void actions.save([], [{ item_id: i.item_id, expected: i.row_version, values: { [prop]: parsed.value } }])
      return
    }
    const values: CapexNew = { ...draftIn(drafts, row), [prop]: parsed.value }
    const key = draftKey(row.project)
    if (!values.name) {
      setDrafts({ ...drafts, [key]: values })
      return
    }
    const n = created(row, values)
    if (row.project === null && !n.cost_center) return actions.error('choose the cost center first')
    const next = { ...drafts }
    delete next[key]
    setDrafts(next)
    void actions.save([n], [])
  }

  /** A pasted block fills the columns from the field it lands in, rightwards, row by row. Rows onto
   * an add row, or past the list's end, add investments (a project's add row adds its lines). One
   * request. */
  function paste(startRow: number, startProp: Field, parsed: string[][]) {
    const start = TYPED.indexOf(startProp)
    const first = rows[startRow]
    const update: CapexChange[] = []
    const creates: CapexNew[] = []
    for (const [r, values] of parsed.entries()) {
      // From an add row, every pasted row is added there.
      const row: Row = first?.item === null ? first : (rows[startRow + r] ?? { item: null, project: null })
      const n: CapexNew = {}
      for (const [c, raw] of values.entries()) {
        const prop = TYPED[start + c]
        if (!prop || !can(row, prop)) continue
        const v = valueOf(prop, raw, view)
        if ('error' in v) return actions.error(`row ${r + 1}: ${v.error}`)
        Object.assign(n, { [prop]: v.value })
      }
      if (row.item !== null) {
        if (Object.keys(n).length) update.push({ item_id: row.item.item_id, expected: row.item.row_version, values: n })
        continue
      }
      if (!canAdd) continue
      const added = created(row, { ...draftIn(drafts, row), ...n })
      if (!added.name) return actions.error(`row ${r + 1}: a new investment starts with its name`)
      if (row.project === null && !added.cost_center) return actions.error(`row ${r + 1}: no cost center`)
      creates.push(added)
    }
    if (first?.item === null) {
      const next = { ...drafts }
      delete next[draftKey(first.project)]
      setDrafts(next)
    }
    if (creates.length || update.length) void actions.save(creates, update)
  }

  /** Enter and the arrows move to the same column's field in the row below or above. */
  function onKey(e: KeyboardEvent<HTMLTableElement>) {
    const el = e.target as HTMLElement
    const { col, row } = el.dataset
    if (!col || row === undefined) return
    const down = e.key === 'ArrowDown' || (e.key === 'Enter' && el instanceof HTMLInputElement)
    if (!down && e.key !== 'ArrowUp') return
    const table = e.currentTarget
    for (let r = Number(row) + (down ? 1 : -1); r >= 0 && r < rows.length; r += down ? 1 : -1) {
      const next = table.querySelector<HTMLElement>(`[data-col="${col}"][data-row="${r}"]`)
      if (next) {
        e.preventDefault()
        next.focus()
        if (next instanceof HTMLInputElement) next.select()
        return
      }
    }
    if (e.key === 'Enter') el.blur()
  }

  function toggle(id: number) {
    setCollapsed((c) => {
      const next = new Set(c)
      if (!next.delete(id)) next.add(id)
      return next
    })
  }

  function select(id: number) {
    const next = new Set(selected)
    if (!next.delete(id)) next.add(id)
    onSelect(next)
  }

  const open = (prop: Field, rowIndex: number) => (anchor: DOMRect) => setPick({ prop, row: rowIndex, anchor })

  /** The investment's name, its large-project marks and its row's actions. */
  function nameCell(row: Row, rowIndex: number) {
    const i = row.item
    if (i === null)
      return (
        <div className="cx-name cx-indent">
          <span className="pl-code" />
          <CellInput
            value=""
            col="name"
            row={rowIndex}
            label={row.project !== null ? 'Name of a new line' : 'Name of a new investment'}
            placeholder={row.project !== null ? 'Type a name to add a line' : 'Type a name to add an investment'}
            clearOnCommit
            onCommit={(raw) => commit(rowIndex, 'name', raw)}
            onPasteBlock={(b) => paste(rowIndex, 'name', b)}
          />
          <span className="cx-marks" />
        </div>
      )
    const mark = noteMarker(i.notes)
    return (
      <div className={`cx-name${i.kind === 'project' ? '' : ' cx-indent'}`}>
        {i.kind === 'project' && (
          <button type="button" className="cx-toggle" onClick={() => toggle(i.item_id)} aria-label={collapsed.has(i.item_id) ? 'Show lines' : 'Hide lines'}>
            {collapsed.has(i.item_id) ? '▸' : '▾'}
          </button>
        )}
        <span className="pl-code">{i.item_id}</span>
        {can(row, 'name') ? (
          <CellInput
            key={i.row_version}
            value={i.name}
            col="name"
            row={rowIndex}
            label={`Name of ${i.name}`}
            onCommit={(raw) => commit(rowIndex, 'name', raw)}
            onPasteBlock={(b) => paste(rowIndex, 'name', b)}
          />
        ) : (
          <span className="cx-text">{i.name}</span>
        )}
        <span className="cx-marks">
          {i.above_threshold && (
            <span className="cx-warn" title={`Above ${detail(view.settings.project_threshold ?? null)}: break it down into a large project`}>
              ⚠
            </span>
          )}
          <button type="button" className={mark.className} onClick={() => actions.openNotes(i)} title={mark.title} aria-label={mark.title}>
            {mark.count}
          </button>
          <button type="button" className="cx-more" onClick={(e) => actions.openMenu(i, e.currentTarget.getBoundingClientRect())} aria-label={`Actions for ${i.name}`} title="Actions">
            ⋯
          </button>
        </span>
      </div>
    )
  }

  /** A picked field, or its value as text where it can't be changed on this row. */
  function pickCell(row: Row, rowIndex: number, prop: Field, content: ReactNode, label: string, center = false) {
    return can(row, prop) ? (
      <PickButton content={content} onOpen={open(prop, rowIndex)} col={prop} row={rowIndex} label={label} center={center} />
    ) : (
      <span className="cx-text muted">{content}</span>
    )
  }

  function cells(row: Row, rowIndex: number) {
    const i = row.item
    const d = draftIn(drafts, row)
    const name = i?.name ?? 'the new investment'
    const prio = (p: number | null | undefined, muted = false) => (p ? <span className={`cx-prio p${p}${muted ? ' muted' : ''}`}>{p}</span> : '')
    const cc = i ? `${i.cost_center_code} ${i.cost_center_name}` : row.project === null ? (d.cost_center ?? defaultCostCenter) : null
    const type = i ? i.type_code : d.type
    return (
      <>
        <td className="cx-sel">
          {i && i.project_id === null && (i.can_confirm || i.can_write) && (
            <button type="button" className={`cx-check${selected.has(i.item_id) ? ' on' : ''}`} onClick={() => select(i.item_id)} aria-label={`Select ${i.name}`} />
          )}
        </td>
        <td>{nameCell(row, rowIndex)}</td>
        <td>
          {(i || row.project === null) &&
            pickCell(row, rowIndex, 'cost_center', i ? cc : cc ? `${cc} ${ccNames.get(cc) ?? ''}` : '', `Cost center of ${name}`)}
        </td>
        <td>
          {i?.kind === 'project' ? <span className="cx-tag">Large project</span> : pickCell(row, rowIndex, 'type', type ? (typeNames.get(type) ?? type) : '', `Type of ${name}`)}
        </td>
        <td className="cx-center">
          {(i || row.project === null) && pickCell(row, rowIndex, 'priority', prio(i ? i.priority : d.priority, !!i && i.project_id !== null), `Priority of ${name}`, true)}
        </td>
        <td>
          {i?.kind === 'project' ? (
            <span className="cx-sum">{monthLabel(i.start_month)}</span>
          ) : (
            pickCell(row, rowIndex, 'start_month', monthLabel(i ? i.start_month : d.start_month), `Start of ${name}`)
          )}
        </td>
        <td className="num">
          {i?.kind === 'project' ? (
            <span className="cx-sum">{display(i.amount, numberFormat())}</span>
          ) : can(row, 'amount') ? (
            <CellInput
              key={i?.row_version}
              value={format(i ? i.amount : (d.amount ?? null))}
              display={display(i ? i.amount : (d.amount ?? null), numberFormat())}
              col="amount"
              row={rowIndex}
              label={`Amount of ${name}`}
              numeric
              onCommit={(raw) => commit(rowIndex, 'amount', raw)}
              onPasteBlock={(b) => paste(rowIndex, 'amount', b)}
            />
          ) : (
            display(i?.amount ?? null, numberFormat())
          )}
        </td>
        {cashOut && <td className="cx-center">{i && cashCell(i)}</td>}
        <td className="cx-center">{i && statusCell(i)}</td>
      </>
    )
  }

  function cashCell(i: CapexItem) {
    const state = scheduleState(i)
    if (state === null) return null
    const rest = detail(fromCents(toCents(i.amount ?? '0') - toCents(i.scheduled)))
    const label = state === 'full' ? 'Scheduled' : state === 'none' ? 'Not scheduled' : `Rest ${rest}`
    return i.kind === 'project' ? (
      <span className={`cx-cash ${state}`} title="The lines’ cash out">
        {label}
      </span>
    ) : (
      <button type="button" className={`cx-cash ${state}`} title="Cash-out schedule" onClick={() => actions.openCashOut(i)}>
        {label}
      </button>
    )
  }

  function statusCell(i: CapexItem) {
    if (i.project_id !== null) return <span className="cx-with">with project</span>
    const title = i.status_by ? `${STATUS_TEXT[i.status]} by ${i.status_by}` : STATUS_TEXT[i.status]
    return i.can_confirm && !locked ? (
      <button type="button" className={`cx-status ${i.status} can`} title={title} onClick={(e) => actions.openStatus(i, e.currentTarget.getBoundingClientRect())}>
        {STATUS_TEXT[i.status]} ▾
      </button>
    ) : (
      <span className={`cx-status ${i.status}`} title={title}>
        {STATUS_TEXT[i.status]}
      </span>
    )
  }

  const pickOptions = (prop: Field): Option[] => {
    switch (prop) {
      case 'cost_center':
        return view.cost_centers.filter((c) => c.can_write).map((c) => ({ value: c.code, label: `${c.code} ${c.name}` }))
      case 'type':
        return [
          { value: '', label: 'No type' },
          ...view.types.filter((t) => t.active).map((t) => ({ value: t.code, label: t.name, hint: [t.code, t.asset_account, t.useful_life ? `${t.useful_life} years` : ''].filter(Boolean).join(' · ') })),
        ]
      case 'priority':
        return [
          { value: '1', label: '1', hint: 'must' },
          { value: '2', label: '2', hint: 'should' },
          { value: '3', label: '3', hint: 'could' },
        ]
    }
    return []
  }
  const pickRow = pick ? rows[pick.row] : undefined
  const picked = pickRow?.item ?? null
  const pickDraft = draftIn(drafts, pickRow)
  const pickValue = !pick
    ? ''
    : pick.prop === 'cost_center'
      ? (picked?.cost_center_code ?? pickDraft.cost_center ?? '')
      : pick.prop === 'type'
        ? ((picked ? picked.type_code : pickDraft.type) ?? '')
        : pick.prop === 'priority'
          ? String(picked?.priority ?? pickDraft.priority ?? '')
          : ((picked ? picked.start_month : pickDraft.start_month) ?? '')
  const close = () => setPick(null)

  return (
    <div className="pl-sheet cx-sheet">
      <div className="cx-scroll">
        <table className="tbl cx-table" onKeyDown={onKey}>
          <colgroup>
            <col className="cx-w-sel" />
            <col />
            <col className="cx-w-cc" />
            <col className="cx-w-type" />
            <col className="cx-w-prio" />
            <col className="cx-w-start" />
            <col className="cx-w-amount" />
            {cashOut && <col className="cx-w-cash" />}
            <col className="cx-w-status" />
          </colgroup>
          <thead>
            <tr>
              <th className="cx-sel">
                {allIds.length > 0 && (
                  <button type="button" className={`cx-check${allSelected ? ' on' : ''}`} onClick={() => onSelect(allSelected ? new Set() : new Set(allIds))} aria-label="Select all shown" />
                )}
              </th>
              <th>Investment</th>
              <th>Cost center</th>
              <th>Type</th>
              <th className="cx-center">Priority</th>
              <th>Start</th>
              <th className="num">Amount</th>
              {cashOut && <th className="cx-center">Cash out</th>}
              <th className="cx-center">Confirmation</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => {
              const i = row.item
              const classes = [
                i === null ? 'cx-new' : i.kind === 'project' ? 'cx-project' : '',
                i && i.status === 'declined' ? 'cx-declined' : '',
                i && selected.has(i.project_id ?? i.item_id) ? 'cx-selected' : '',
              ]
              return (
                <tr key={i ? i.item_id : `new-${row.project ?? 'top'}`} className={classes.filter(Boolean).join(' ') || undefined}>
                  {cells(row, rowIndex)}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="pl-foot">
        <span>
          {lines.length} investment{lines.length === 1 ? '' : 's'} · <strong>{display(total ?? '0')}</strong> planned
        </span>
        <span className="pv-key">
          <i className="k-edit" /> entered
        </span>
        <span className="pv-key">
          <i className="k-calc" /> large project: sum of its lines
        </span>
        {!locked && <span className="pl-help">Type, or paste rows from Excel. Enter and the arrows move up and down a column.</span>}
      </div>
      {pick && pick.prop === 'start_month' && (
        <MonthPicker
          label="Start"
          value={pickValue || null}
          fallbackYear={view.round.fiscal_year}
          anchor={pick.anchor}
          onClose={close}
          onChange={(value) => {
            close()
            commit(pick.row, 'start_month', value ?? '')
          }}
        />
      )}
      {pick && pick.prop !== 'start_month' && (
        <Select
          label={`Choose ${pick.prop.replace('_', ' ')}`}
          anchor={pick.anchor}
          searchable={pick.prop === 'cost_center'}
          value={pickValue}
          options={pickOptions(pick.prop)}
          onClose={close}
          onChange={(value) => {
            close()
            commit(pick.row, pick.prop, value)
          }}
        />
      )}
    </div>
  )
}
