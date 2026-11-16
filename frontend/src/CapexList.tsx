import { RevoGrid } from '@revolist/react-datagrid'
import type { HyperFunc, VNode } from '@revolist/revogrid'
import { useEffect, useEffectEvent, useMemo, useRef, useState } from 'react'
import { format, parse, sum, toCents } from './amounts'
import { type Capex, type CapexChange, type CapexItem, type CapexNew } from './api'
import { monthLabel, parseMonth, scheduleState, STATUS_TEXT } from './capex'
import type { H } from './lineMarks'
import Select, { type Option } from './Select'

// The investments list (ADR 0028) in the planning grid: one row per investment, large projects as
// group rows summing their lines (collapsible), and a last row that adds an investment as soon as
// its name is typed. Cells are typed or pasted like a spreadsheet; cost center, type and priority
// also open a picker. The server decides what each user may write and confirm.

type Field = 'name' | 'cost_center' | 'type' | 'priority' | 'start_month' | 'amount'
type GridRow = Record<string, string>
type Row = { item: CapexItem | null } // null: the row that adds an investment
type Pick = { prop: Field; row: number; anchor: DOMRect }

const NEW_ROW_TEXT = 'Type a name to add an investment'
// The columns that take typed values, in the order a pasted block fills them.
const TYPED: Field[] = ['name', 'cost_center', 'type', 'priority', 'start_month', 'amount']

export type ListActions = {
  /** Add and change investments in one request; the list is reloaded when it is refused. */
  save: (create: CapexNew[], update: CapexChange[]) => Promise<void>
  openMenu: (item: CapexItem, anchor: DOMRect) => void
  openStatus: (item: CapexItem, anchor: DOMRect) => void
  openCashOut: (item: CapexItem) => void
  openComments: (item: CapexItem) => void
  error: (text: string) => void
}

/** The fields a user may type into on a row. */
function editable(row: Row | undefined, prop: string, locked: boolean, canAdd: boolean): boolean {
  if (!row || locked) return false
  const i = row.item
  if (i === null) return TYPED.includes(prop as Field) && canAdd
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
  return false
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
      const month = parseMonth(text)
      return month === undefined ? { error: `“${text}” is not a month, e.g. 03.2026` } : { value: month }
    }
    case 'amount': {
      const amount = parse(text)
      if (amount === undefined) return { error: `“${text}” is not a number` }
      return amount !== null && amount.startsWith('-') ? { error: 'an amount is not negative' } : { value: amount }
    }
  }
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
  // What is typed or picked on the add row before its name: the investment is added with it.
  const [draft, setDraft] = useState<CapexNew>({})
  const sheet = useRef<HTMLDivElement>(null)
  const cashOut = view.settings.cash_out
  const canAdd = !locked && view.cost_centers.some((c) => c.can_write)
  const typeNames = useMemo(() => new Map(view.types.map((t) => [t.code, t.name])), [view.types])
  const ccNames = useMemo(() => new Map(view.cost_centers.map((c) => [c.code, c.name])), [view.cost_centers])

  const rows = useMemo<Row[]>(() => {
    const shown = items.filter((i) => i.project_id === null || !collapsed.has(i.project_id))
    return [...shown.map((item) => ({ item })), ...(canAdd ? [{ item: null }] : [])]
  }, [items, collapsed, canAdd])

  const source = useMemo<GridRow[]>(
    () =>
      rows.map(({ item: i }) =>
        i === null
          ? {
              sel: '',
              no: '',
              name: '',
              cost_center: draft.cost_center ?? defaultCostCenter ?? '',
              type: draft.type ?? '',
              priority: draft.priority ? String(draft.priority) : '',
              start_month: monthLabel(draft.start_month),
              amount: format(draft.amount ?? null),
              cash: '',
              status: '',
            }
          : {
              sel: '',
              no: String(i.item_id),
              name: i.name,
              cost_center: `${i.cost_center_code} ${i.cost_center_name}`,
              type: i.type_code ? `${i.type_code} ${typeNames.get(i.type_code) ?? ''}`.trim() : '',
              priority: String(i.priority),
              start_month: monthLabel(i.start_month),
              amount: format(i.amount),
              cash: '',
              status: STATUS_TEXT[i.status],
            },
      ),
    [rows, typeNames, draft, defaultCostCenter],
  )

  const lines = items.filter((i) => i.kind === 'line')
  const total = sum(lines.filter((i) => i.status !== 'declined').map((i) => i.amount))
  const allIds = items.filter((i) => i.project_id === null).map((i) => i.item_id)
  const allSelected = allIds.length > 0 && allIds.every((id) => selected.has(id))

  const draftCc = draft.cost_center ?? defaultCostCenter
  const columns = useMemo(() => {
    const cell =
      (prop: string, extra = '') =>
      ({ rowIndex }: { rowIndex: number }) => {
        const row = rows[rowIndex]
        const i = row?.item
        const classes = ['cx-cell', extra]
        if (i === null) classes.push('cx-new')
        else if (i?.kind === 'project') classes.push('cx-project')
        else if (i?.project_id !== null) classes.push('cx-in-project')
        if (editable(row, prop, locked, canAdd)) classes.push('pl-edit')
        if (i && i.status === 'declined') classes.push('cx-declined')
        if (i && selected.has(i.project_id ?? i.item_id)) classes.push('cx-selected')
        return { class: classes.filter(Boolean).join(' ') }
      }
    const column = (prop: string, name: string, size: number, extra = '', headClass = '') => ({
      prop,
      name,
      size,
      columnProperties: () => ({ class: `pl-head cx-head ${headClass}` }),
      readonly: ({ rowIndex }: { rowIndex: number }) => !editable(rows[rowIndex], prop, locked, canAdd),
      cellProperties: cell(prop, extra),
    })
    const template = (render: (h: H, i: CapexItem | null, rowIndex: number) => unknown) => ({
      cellTemplate: (h: HyperFunc<VNode>, { rowIndex }: { rowIndex: number }) => render(h as unknown as H, rows[rowIndex]?.item ?? null, rowIndex),
    })
    const picker = (h: H, prop: Field, rowIndex: number, text: string, muted = false) => {
      const row = rows[rowIndex]
      return h('span', { class: `cx-pick${muted ? ' muted' : ''}` }, [
        h('span', { class: 'cx-pick-text' }, text),
        editable(row, prop, locked, canAdd)
          ? h('button', { type: 'button', class: 'cx-caret', 'data-pick': `${prop}:${rowIndex}`, 'aria-label': `Choose ${prop.replace('_', ' ')}` }, '')
          : '',
      ])
    }
    return [
      {
        ...column('sel', '', 36, 'cx-sel-cell', 'cx-sel-head'),
        readonly: true,
        columnTemplate: (h: HyperFunc<VNode>) =>
          (h as unknown as H)('button', { type: 'button', class: `cx-check${allSelected ? ' on' : ''}`, 'data-select-all': '1', 'aria-label': 'Select all shown' }, ''),
        ...template((h, i) =>
          !i || i.project_id !== null || !(i.can_confirm || i.can_write)
            ? ''
            : h('button', { type: 'button', class: `cx-check${selected.has(i.item_id) ? ' on' : ''}`, 'data-select': String(i.item_id), 'aria-label': `Select ${i.name}` }, ''),
        ),
      },
      {
        ...column('name', 'Investment', 330, 'pl-acc', 'pl-head-acc'),
        ...template((h, i, rowIndex) => {
          if (i === null) return h('span', { class: 'cx-new-text' }, NEW_ROW_TEXT)
          const lines = i.kind === 'project'
          const toggle = lines
            ? h('button', { type: 'button', class: 'cx-toggle', 'data-toggle': String(i.item_id), 'aria-label': collapsed.has(i.item_id) ? 'Show lines' : 'Hide lines' }, collapsed.has(i.item_id) ? '▸' : '▾')
            : ''
          const marks = [
            i.above_threshold
              ? h('span', { class: 'cx-warn', title: `Above ${format(view.settings.project_threshold ?? null)}: break it down into a large project` }, '⚠')
              : '',
            h(
              'button',
              {
                type: 'button',
                class: `pl-mark note ${i.comments ? 'on' : 'empty'}`,
                'data-comments': String(rowIndex),
                title: i.comments ? `${i.comments} comment${i.comments === 1 ? '' : 's'}` : 'Comment',
                'aria-label': 'Comments',
              },
              i.comments ? String(i.comments) : '',
            ),
            h('button', { type: 'button', class: 'cx-more', 'data-menu': String(rowIndex), 'aria-label': `Actions for ${i.name}`, title: 'Actions' }, '⋯'),
          ]
          return h('span', { class: 'pl-acc-in' }, [
            h('span', { class: 'pl-acc-name' }, [
              toggle,
              h('span', { class: 'pl-code' }, String(i.item_id)),
              lines ? h('span', { class: 'cx-tag' }, `Project · ${i.lines}`) : '',
              i.name,
            ]),
            h('span', { class: 'pl-marks cx-marks' }, marks),
          ])
        }),
      },
      {
        ...column('cost_center', 'Cost center', 180),
        ...template((h, i, rowIndex) =>
          i === null
            ? picker(h, 'cost_center', rowIndex, draftCc ? `${draftCc} ${ccNames.get(draftCc) ?? ''}` : '', !draft.cost_center)
            : picker(h, 'cost_center', rowIndex, `${i.cost_center_code} ${i.cost_center_name}`, i.project_id !== null),
        ),
      },
      {
        ...column('type', 'Type', 160),
        ...template((h, i, rowIndex) =>
          i === null
            ? picker(h, 'type', rowIndex, draft.type ? (typeNames.get(draft.type) ?? draft.type) : '')
            : i.kind === 'project'
              ? ''
              : picker(h, 'type', rowIndex, i.type_code ? (typeNames.get(i.type_code) ?? i.type_code) : ''),
        ),
      },
      {
        ...column('priority', 'Priority', 84, 'cx-center', 'cx-head-center'),
        ...template((h, i, rowIndex) => {
          if (i === null) return picker(h, 'priority', rowIndex, draft.priority ? String(draft.priority) : '')
          const pill = h('span', { class: `cx-prio p${i.priority}${i.project_id !== null ? ' muted' : ''}` }, String(i.priority))
          return editable(rows[rowIndex], 'priority', locked, canAdd)
            ? h('span', { class: 'cx-pick' }, [pill, h('button', { type: 'button', class: 'cx-caret', 'data-pick': `priority:${rowIndex}`, 'aria-label': 'Choose priority' }, '')])
            : pill
        }),
      },
      {
        ...column('start_month', 'Start', 104, 'pl-num'),
        ...template((h, i) =>
          i === null ? monthLabel(draft.start_month) : i.kind === 'project' ? h('span', { class: 'cx-sum' }, monthLabel(i.start_month)) : monthLabel(i.start_month),
        ),
      },
      {
        ...column('amount', 'Amount', 132, 'pl-num'),
        ...template((h, i) =>
          i === null ? format(draft.amount ?? null) : i.kind === 'project' ? h('span', { class: 'cx-sum' }, format(i.amount)) : format(i.amount),
        ),
      },
      ...(cashOut
        ? [
            {
              ...column('cash', 'Cash out', 124, 'cx-center', 'cx-head-center'),
              readonly: true,
              ...template((h, i, rowIndex) => {
                if (!i) return ''
                const state = scheduleState(i)
                if (state === null) return ''
                const rest = format(String(Number(toCents(i.amount ?? '0') - toCents(i.scheduled)) / 100))
                const label = state === 'full' ? 'Scheduled' : state === 'none' ? 'Not scheduled' : `Rest ${rest}`
                const props = { class: `cx-cash ${state}`, title: i.kind === 'project' ? 'The lines’ cash out' : 'Cash-out schedule' }
                return i.kind === 'project'
                  ? h('span', props, label)
                  : h('button', { ...props, type: 'button', 'data-cash': String(rowIndex) }, label)
              }),
            },
          ]
        : []),
      {
        ...column('status', 'Confirmation', 124, 'cx-center', 'cx-head-center'),
        readonly: true,
        ...template((h, i, rowIndex) => {
          if (!i) return ''
          if (i.project_id !== null) return h('span', { class: 'cx-with' }, 'with project')
          const title = i.status_by ? `${STATUS_TEXT[i.status]} by ${i.status_by}` : STATUS_TEXT[i.status]
          return i.can_confirm && !locked
            ? h('button', { type: 'button', class: `cx-status ${i.status} can`, 'data-status': String(rowIndex), title }, `${STATUS_TEXT[i.status]} ▾`)
            : h('span', { class: `cx-status ${i.status}`, title }, STATUS_TEXT[i.status])
        }),
      },
    ]
  }, [rows, locked, canAdd, selected, allSelected, collapsed, cashOut, typeNames, ccNames, draft, draftCc, view.settings.project_threshold])

  // The grid reports an edit again when its editor closes (applyOnClose); the same edit within a
  // moment is the same edit, sent once.
  const lastCommit = useRef({ key: '', at: 0 })

  /** Typed, pasted or picked values: changes of existing rows and investments to add, one request. */
  function commit(changes: { rowIndex: number; prop: string; raw: string }[]) {
    const key = JSON.stringify(changes.map((c) => [rows[c.rowIndex]?.item?.item_id ?? 'new', c.prop, c.raw]))
    const now = Date.now()
    if (lastCommit.current.key === key && now - lastCommit.current.at < 1500) return
    lastCommit.current = { key, at: now }
    const update = new Map<number, CapexChange>()
    const created = new Map<number, CapexNew>()
    const refused: string[] = []
    for (const { rowIndex, prop, raw } of changes) {
      const row = rows[rowIndex]
      if (!TYPED.includes(prop as Field) || !editable(row, prop, locked, canAdd)) continue
      const parsed = valueOf(prop as Field, raw, view)
      if ('error' in parsed) {
        refused.push(parsed.error)
        continue
      }
      const i = row.item
      if (i === null) {
        const n = created.get(rowIndex) ?? { ...draft }
        created.set(rowIndex, { ...n, [prop]: parsed.value })
      } else {
        const c = update.get(i.item_id) ?? { item_id: i.item_id, expected: i.row_version, values: {} }
        c.values = { ...c.values, [prop]: parsed.value }
        update.set(i.item_id, c)
      }
    }
    // The add row keeps what it has until its name is typed; then it is added.
    const create = [...created.values()].map((n) => ({ ...n, cost_center: n.cost_center ?? defaultCostCenter }))
    const named = create.filter((n) => n.name)
    if (create.length && !named.length) setDraft(create[0])
    if (named.some((n) => !n.cost_center)) {
      refused.push('choose the cost center first')
      return actions.error(refused.join(' · '))
    }
    if (named.length) setDraft({})
    if (refused.length) actions.error(refused.slice(0, 3).join(' · '))
    if (named.length || update.size) void actions.save(named, [...update.values()])
  }

  /** A pasted block fills the columns from the focused cell rightwards, row by row; rows past
   * the list's end add investments. Handled before the grid applies it, as one request. */
  function onPaste(e: Event) {
    const grid = (e.target as HTMLElement).closest('revo-grid') as (HTMLElement & { getFocused: () => Promise<{ cell: { y: number }; column?: { prop: string } } | null> }) | null
    const parsed = (e as CustomEvent<{ parsed: string[][] }>).detail.parsed
    while (parsed.length > 1 && parsed[parsed.length - 1].every((v) => v === '')) parsed.pop()
    if (!grid) return
    e.preventDefault()
    void grid.getFocused().then((focused) => {
      if (!focused?.column) return
      const start = TYPED.indexOf(focused.column.prop as Field)
      if (start < 0) return
      const changes: { rowIndex: number; prop: string; raw: string }[] = []
      const creates: CapexNew[] = []
      parsed.forEach((values, r) => {
        const rowIndex = focused.cell.y + r
        const isNew = rowIndex >= rows.length - (canAdd ? 1 : 0)
        if (!isNew) {
          values.forEach((raw, c) => TYPED[start + c] && changes.push({ rowIndex, prop: TYPED[start + c], raw }))
          return
        }
        if (!canAdd) return
        const n: CapexNew = { cost_center: defaultCostCenter }
        for (const [c, raw] of values.entries()) {
          const prop = TYPED[start + c]
          if (!prop) continue
          const v = valueOf(prop, raw, view)
          if ('error' in v) return actions.error(`row ${r + 1}: ${v.error}`)
          Object.assign(n, { [prop]: v.value })
        }
        if (!n.name) return actions.error(`row ${r + 1}: a new investment starts with its name`)
        if (!n.cost_center) return actions.error(`row ${r + 1}: no cost center`)
        creates.push(n)
      })
      const update = new Map<number, CapexChange>()
      for (const { rowIndex, prop, raw } of changes) {
        const i = rows[rowIndex]?.item
        if (!i || !editable(rows[rowIndex], prop, locked, canAdd)) continue
        const v = valueOf(prop as Field, raw, view)
        if ('error' in v) return actions.error(`${i.name}: ${v.error}`)
        const c = update.get(i.item_id) ?? { item_id: i.item_id, expected: i.row_version, values: {} }
        c.values = { ...c.values, [prop]: v.value }
        update.set(i.item_id, c)
      }
      if (creates.length || update.size) void actions.save(creates, [...update.values()])
    })
  }

  // The grid's clipboard component sends the event; it bubbles to the sheet.
  const pasted = useEffectEvent(onPaste)
  useEffect(() => {
    const el = sheet.current
    if (!el) return
    const listener = (e: Event) => pasted(e)
    el.addEventListener('beforepasteapply', listener)
    return () => el.removeEventListener('beforepasteapply', listener)
  }, [])

  /** Markers, carets and checkboxes are handled on mouse down, before the grid selects the cell
   * and redraws it (as on the planning sheet). */
  function onPress(e: React.MouseEvent) {
    if (e.button !== 0) return
    const target = (e.target as HTMLElement).closest<HTMLElement>(
      '[data-pick], [data-menu], [data-status], [data-cash], [data-comments], [data-select], [data-select-all], [data-toggle]',
    )
    if (!target) return
    e.preventDefault()
    e.stopPropagation()
    const d = target.dataset
    const rect = target.getBoundingClientRect()
    const at = (s?: string) => rows[Number(s)]?.item ?? null
    if (d.pick) {
      const [prop, row] = d.pick.split(':')
      setPick({ prop: prop as Field, row: Number(row), anchor: (target.closest('.rgCell') ?? target).getBoundingClientRect() })
    } else if (d.menu && at(d.menu)) actions.openMenu(at(d.menu)!, rect)
    else if (d.status && at(d.status)) actions.openStatus(at(d.status)!, rect)
    else if (d.cash && at(d.cash)) actions.openCashOut(at(d.cash)!)
    else if (d.comments && at(d.comments)) actions.openComments(at(d.comments)!)
    else if (d.toggle) {
      const id = Number(d.toggle)
      setCollapsed((c) => {
        const next = new Set(c)
        if (!next.delete(id)) next.add(id)
        return next
      })
    } else if (d.select) {
      const id = Number(d.select)
      const next = new Set(selected)
      if (!next.delete(id)) next.add(id)
      onSelect(next)
    } else if (d.selectAll) onSelect(allSelected ? new Set() : new Set(allIds))
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
  const picked = pick ? rows[pick.row]?.item : undefined
  const pickValue = !pick ? '' : !picked ? String(draft[pick.prop] ?? '') : pick.prop === 'cost_center' ? picked.cost_center_code : pick.prop === 'type' ? (picked.type_code ?? '') : String(picked.priority)

  return (
    <div className="pl-sheet cx-sheet" ref={sheet} onMouseDownCapture={onPress}>
      <RevoGrid
        className="pl-revo cx-revo"
        columns={columns}
        source={source}
        range
        resize
        applyOnClose
        theme="default"
        rowSize={34}
        hideAttribution
        useClipboard={{ rangeFill: true }}
        onBeforeedit={(e) => {
          e.preventDefault()
          commit([{ rowIndex: e.detail.rowIndex, prop: String(e.detail.prop), raw: String(e.detail.val ?? '') }])
        }}
        onBeforerangeedit={(e) => {
          // Fill-drag and Delete arrive as one block; a paste is handled before (onPaste).
          e.preventDefault()
          commit(
            Object.entries(e.detail.data).flatMap(([rowIndex, values]) =>
              Object.entries(values as GridRow).map(([prop, raw]) => ({ rowIndex: Number(rowIndex), prop, raw: String(raw ?? '') })),
            ),
          )
        }}
      />
      <div className="pl-foot">
        <span>
          {lines.length} investment{lines.length === 1 ? '' : 's'} · <strong>{format(total) || '0.00'}</strong> planned
        </span>
        <span className="pv-key">
          <i className="k-edit" /> entered
        </span>
        <span className="pv-key">
          <i className="k-calc" /> large project: sum of its lines
        </span>
        {!locked && <span className="pl-help">Type, paste from Excel, or pick from the list. Dates as 03.2026 or Mar 2026.</span>}
      </div>
      {pick && (
        <Select
          label={`Choose ${pick.prop.replace('_', ' ')}`}
          anchor={pick.anchor}
          searchable={pick.prop === 'cost_center'}
          value={pickValue}
          options={pickOptions(pick.prop)}
          onClose={() => setPick(null)}
          onChange={(value) => {
            setPick(null)
            commit([{ rowIndex: pick.row, prop: pick.prop, raw: value }])
          }}
        />
      )}
    </div>
  )
}
