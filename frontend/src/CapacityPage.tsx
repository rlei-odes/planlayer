import { RevoGrid } from '@revolist/react-datagrid'
import type { HyperFunc, VNode } from '@revolist/revogrid'
import { useCallback, useEffect, useEffectEvent, useMemo, useState } from 'react'
import { format, parse, sum } from './amounts'
import {
  api,
  ConflictError,
  STATUS_LABEL,
  type CapacityRow,
  type CapacitySheet,
  type CapacityUnit,
  type HoursEdit,
  type LineSpread,
  type PlanningUnit,
  type ProfileRef,
} from './api'
import { RedoIcon, UndoIcon } from './icons'
import { headerWithMarks, noteMark, type H } from './lineMarks'
import NotesPanel from './NotesPanel'
import Select, { type Option } from './Select'
import { byColumn, isClosed, monthsOf, periodColumns, quarterly, span, type Layout } from './periods'
import { pickUnit } from './units'

// The capacity sheet of a productive cost center (ADR 0013): per activity type, its work
// centers' planned hours and plan rates, and the credit they give the cost center (hours × rate,
// stored on the activity type's account and shown on the cost sheet). Hours are typed per month
// or as a year total, which spreads like last year's hours, evenly, or by a seasonality profile;
// the line's badge chooses. The server decides what is editable. The sheet follows its round
// (ADR 0016): its comparison columns, quarters in a round planned by quarter, and a forecast's
// closed months showing actual hours, read only, with a year total meaning the full year.

const COL_LINE = 'line'
const COL_RATE = 'rate'
const COL_FY = 'fy'
const comparisonProp = (i: number) => `c${i}`
const MONTH_YEAR = new Intl.DateTimeFormat('en', { month: 'long', year: 'numeric', timeZone: 'UTC' })
const SCROLLBAR = 18
const NEW_WORK_CENTER = '__new__'
const INACTIVE = 'inactive'

type GridRow = Record<string, string>
type Message = { kind: 'error' | 'info' | 'ok'; text: string }

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

const lineKey = (r: CapacityRow) => `${r.work_center_code ?? ''}|${r.activity_type_code}`

function editable(row: CapacityRow | undefined, prop: string, layout: Layout): boolean {
  if (!row || row.kind !== 'line' || !row.editable) return false
  if (prop === COL_RATE || prop === COL_FY) return true
  return monthsOf(prop) !== null && !isClosed(prop, layout)
}

function cellKind(row: CapacityRow | undefined, prop: string, layout: Layout, nComp: number): string {
  if (!row) return ''
  const comparison = /^c\d+$/.test(prop)
  const last = prop === comparisonProp(nComp - 1) ? ' pl-cmp-last' : ''
  if (row.kind === 'group') return (comparison ? 'pl-group pl-cmp' : isClosed(prop, layout) ? 'pl-group pl-act' : 'pl-group') + last
  if (row.kind === 'credit') {
    if (prop === COL_LINE) return 'cp-credit'
    return (comparison ? 'pl-num pl-cmp' : isClosed(prop, layout) ? 'pl-num pl-act' : 'pl-num cp-credit') + last
  }
  if (comparison) return `pl-cmp${last}`
  if (prop === COL_LINE) return ''
  if (isClosed(prop, layout)) return 'pl-act'
  const kind = editable(row, prop, layout) ? 'pl-edit' : 'pl-plain'
  return prop === COL_FY ? `${kind} pl-total` : kind
}

/** A comparison column's tooltip: the controller's note, and for actuals how far they go. */
function comparisonTitle(c: CapacitySheet['comparisons'][number]): string | undefined {
  const actuals = c.kind === 'prior_year_actuals' || c.kind === 'actuals'
  const coverage =
    actuals && c.partial && c.actuals_until ? `Actuals up to ${MONTH_YEAR.format(new Date(c.actuals_until))}.` : null
  return [c.note, 'Hours; amounts on the credit rows.', coverage].filter(Boolean).join(' · ')
}

function spreadText(row: CapacityRow): string {
  switch (row.spread) {
    case 'even':
      return 'spreads evenly over the months'
    case 'profile':
      return `spreads by the ${row.basis ?? 'profile'}`
    default:
      return row.fallback ? 'spreads evenly: this line had no hours last year' : 'spreads like last year’s hours'
  }
}

function badgeLabel(row: CapacityRow): string {
  if (row.spread === 'profile') return 'Hours · season'
  if (row.spread === 'even' || row.fallback) return 'Hours · even'
  return 'Hours · last year'
}

function spreadOf(key: string): LineSpread | null {
  if (key === 'default') return null
  if (key.startsWith('profile:')) return { basis: 'profile', profile_code: key.slice('profile:'.length) }
  return { basis: 'even', profile_code: null }
}

function spreadKey(row: CapacityRow): string {
  const s = row.chosen_spread
  if (!s) return 'default'
  return s.basis === 'profile' ? `profile:${s.profile_code ?? ''}` : s.basis
}

function profileBars(h: H, values: number[]) {
  const max = Math.max(...values, 0) || 1
  return h(
    'span',
    { class: 'pv-bars', 'aria-hidden': 'true' },
    values.map((v) => h('i', { style: { height: `${Math.max(1, (v / max) * 14)}px` } })),
  )
}

/** The first column: the activity type (group), a work center with its spread badge (line), or
 * the account the activity type credits (credit). */
function lineCell(h: H, row: CapacityRow | undefined, profiles: ProfileRef[], quarters: boolean) {
  if (!row) return ''
  if (row.kind === 'group') return h('span', { class: 'pl-acc-name' }, row.activity_type_name)
  if (row.kind === 'credit') {
    return row.account_code
      ? h('span', { class: 'cp-credit-label', title: 'Planned hours × rate, credited to the cost center' }, [
          'Credit to ',
          h('span', { class: 'pl-code' }, row.account_code),
          row.account_name ?? '',
        ])
      : h('span', { class: 'cp-credit-label cp-missing' }, 'No account named for this activity type: nothing is credited')
  }
  const label = badgeLabel(row)
  const profile = row.spread === 'profile' ? profiles.find((p) => p.code === row.profile) : undefined
  const classes = `pv-form monthly${row.chosen_spread ? ' chosen' : ''}`
  const title = `${label}: a year total ${spreadText(row)}.${row.chosen_spread ? ' Chosen for this line.' : ''}`
  const badge = row.editable
    ? h('button', { type: 'button', class: `${classes} pl-spread`, 'data-line': lineKey(row), 'aria-label': `How ${row.work_center_code} spreads: ${label}` }, label)
    : h('span', { class: classes }, label)
  return h('span', { class: 'pl-acc-in', title }, [
    h('span', { class: 'pl-acc-name' }, [
      h('span', { class: 'pl-code' }, row.work_center_code ?? ''),
      row.work_center_name ?? '',
      row.added ? h('span', { class: 'cp-added' }, 'added') : '',
    ]),
    // By quarter, a profile shows as its four quarters; it is still entered by month.
    h('span', { class: 'pl-acc-tags' }, [
      profile ? profileBars(h, quarters ? quarterly(profile.values.map(Number)) : profile.values.map(Number)) : '',
      badge,
      h('span', { class: 'pl-marks' }, [noteMark(h, lineKey(row), row.notes)]),
    ]),
  ])
}

export default function CapacityPage({
  version,
  unit: chosen,
  onUnit,
  controller,
  onWidth,
  focus,
  onFocused,
}: {
  version: string
  /** The cost center chosen last, here or on the planning page. */
  unit: string
  onUnit: (code: string) => void
  controller: boolean
  onWidth: (px: number | null) => void
  /** A line whose notes to open (from Home): "work center|activity type". */
  focus?: { key: string; thread: number } | null
  onFocused?: () => void
}) {
  const [units, setUnits] = useState<CapacityUnit[] | null>(null)
  const [planning, setPlanning] = useState<PlanningUnit[]>([])
  const [sheet, setSheet] = useState<CapacitySheet | null>(null)
  const [profiles, setProfiles] = useState<ProfileRef[]>([])
  const [message, setMessage] = useState<Message | null>(null)
  const [saving, setSaving] = useState(false)
  const [menu, setMenu] = useState<{ key: string; anchor: DOMRect } | null>(null)
  // The line whose notes are open beside the sheet (ADR 0017), by its line key.
  const [notesFor, setNotesFor] = useState<{ key: string; thread: number | null } | null>(null)
  const [adding, setAdding] = useState(false)
  // Undo (ADR 0010): the change batches of this visit to the sheet, newest last. Undoing a batch
  // writes a new one, which goes on the other stack, so redo is undoing the undo.
  const [undoStack, setUndoStack] = useState<string[]>([])
  const [redoStack, setRedoStack] = useState<string[]>([])
  const unit = units ? pickUnit(units, chosen) : ''

  const showError = useCallback((e: unknown) => setMessage({ kind: 'error', text: errorText(e) }), [])

  const loadUnits = useCallback(() => {
    api.capacityUnits(version).then(setUnits, showError)
    api.costCenters(version).then(setPlanning, showError)
  }, [version, showError])

  useEffect(() => {
    loadUnits()
    api.profiles().then(setProfiles, showError)
  }, [loadUnits, showError])

  // Nothing chosen yet (a first visit): the cost center this page starts on becomes the choice.
  // The cost center shown here is the one the planning page shows next.
  useEffect(() => {
    if (unit && unit !== chosen) onUnit(unit)
  }, [chosen, unit, onUnit])

  /** A change was saved: show it and make it undoable. */
  function applied(updated: CapacitySheet) {
    setSheet(updated)
    if (updated.batch_id) setUndoStack((s) => [...s, updated.batch_id!])
    setRedoStack([])
  }

  const load = useCallback(() => {
    if (!unit) return
    api.capacitySheet(version, unit).then(setSheet, showError)
  }, [version, unit, showError])

  useEffect(load, [load])

  const locked = sheet?.lock_reason != null
  const quarters = sheet?.grain === 'quarter'
  const closed = sheet?.closed_months ?? 0
  const layout = useMemo<Layout>(() => ({ quarters, closed }), [quarters, closed])
  const forecast = sheet?.version_kind === 'forecast'

  const columns = useMemo(() => {
    const periods = sheet?.periods ?? []
    const nComp = sheet?.comparisons.length ?? 0
    const column = (prop: string, name: string, size: number, headerClass = '', title?: string) => ({
      prop,
      name,
      size,
      columnProperties: () => ({ class: `pl-head ${headerClass}${title ? ' pl-head-noted' : ''}`, title }),
      readonly: ({ rowIndex }: { rowIndex: number }) => !editable(sheet?.rows[rowIndex], prop, layout),
      cellProperties: ({ rowIndex }: { rowIndex: number }) => ({
        class: [prop === COL_LINE ? 'pl-acc' : 'pl-num', cellKind(sheet?.rows[rowIndex], prop, layout, nComp)].join(' '),
      }),
    })
    const yearTitle =
      layout.closed > 0 && periods.length === 12
        ? `Actual hours ${span(periods, 0, layout.closed - 1)} + planned hours ${span(periods, layout.closed, 11)}. A year total typed here is the full year: the open months take what the actual hours leave.`
        : undefined
    return [
      {
        ...column(COL_LINE, 'Work center', 390, 'pl-head-acc'),
        pin: 'colPinStart' as const,
        columnTemplate: (h: HyperFunc<VNode>) => headerWithMarks(h as unknown as H, 'Work center', false),
        cellTemplate: (h: HyperFunc<VNode>, { rowIndex }: { rowIndex: number }) =>
          lineCell(h as unknown as H, sheet?.rows[rowIndex], profiles, layout.quarters),
      },
      ...(sheet?.comparisons ?? []).map((c, i) => column(comparisonProp(i), c.label, 112, 'pl-head-cmp', comparisonTitle(c))),
      column(COL_RATE, 'Rate / h', 96),
      column(COL_FY, `${forecast ? 'Forecast' : 'Plan'} ${sheet?.fiscal_year ?? ''}`, 124, 'pl-head-plan', yearTitle),
      ...periodColumns(periods, layout).map((c) =>
        column(c.prop, c.label, layout.quarters ? 112 : 92, c.closed ? 'pl-head-act' : ''),
      ),
    ]
  }, [sheet, profiles, layout, forecast])

  const sheetWidth = columns.reduce((n, c) => n + c.size, 0) + 2 + SCROLLBAR
  const hasSheet = sheet !== null
  useEffect(() => {
    onWidth(hasSheet ? sheetWidth : null)
    return () => onWidth(null)
  }, [hasSheet, sheetWidth, onWidth])

  const source = useMemo<GridRow[]>(
    () =>
      (sheet?.rows ?? []).map((row) => {
        const out: GridRow = {
          [COL_LINE]: row.work_center_name ?? row.activity_type_name,
          [COL_RATE]: row.rate ?? '',
          [COL_FY]: format(sum(row.plan.map((c) => c.value))),
        }
        row.comparisons.forEach((values, i) => (out[comparisonProp(i)] = format(sum(values))))
        const periods = byColumn(
          row.plan.map((c) => c.value),
          layout,
          (xs) => sum(xs),
        )
        for (const [prop, value] of Object.entries(periods)) out[prop] = format(value)
        return out
      }),
    [sheet, layout],
  )

  async function save(changes: { rowIndex: number; prop: string; raw: unknown }[]) {
    if (!sheet) return
    const edits: HoursEdit[] = []
    const rates: { row: CapacityRow; rate: string }[] = []
    const refused: string[] = []
    for (const { rowIndex, prop, raw } of changes) {
      const row = sheet.rows[rowIndex]
      if (!row || !editable(row, prop, layout)) continue
      const value = parse(String(raw ?? ''))
      if (value === undefined || (prop === COL_RATE && value === null)) {
        refused.push(`${row.work_center_code}: “${String(raw)}” is not ${prop === COL_RATE ? 'a rate' : 'a number of hours'}`)
        continue
      }
      if (prop === COL_RATE) {
        rates.push({ row, rate: value! })
        continue
      }
      const n = prop === COL_FY ? null : Number(prop.slice(1))
      edits.push({
        work_center_code: row.work_center_code!,
        activity_type_code: row.activity_type_code,
        month: prop.startsWith('m') ? n : null,
        quarter: prop.startsWith('q') ? n : null,
        value,
        expected: row.plan.map((c) => c.row_version),
      })
    }
    setMessage(refused.length ? { kind: 'error', text: refused.slice(0, 3).join(' · ') } : null)
    if (edits.length === 0 && rates.length === 0) return
    setSaving(true)
    try {
      let updated = sheet
      if (edits.length) {
        updated = await api.capacityEdit(version, sheet.cost_center_code, edits)
        applied(updated)
      }
      for (const { row, rate } of rates) {
        updated = await api.changeLine(version, sheet.cost_center_code, row.work_center_code!, row.activity_type_code, { rate })
        applied(updated)
      }
      if (updated.status !== sheet.status) loadUnits()
    } catch (e) {
      if (e instanceof ConflictError) {
        setMessage({ kind: 'info', text: `${e.message}; the latest values are shown, nothing was saved.` })
        load()
      } else showError(e)
    } finally {
      setSaving(false)
    }
  }

  async function chooseFromMenu(key: string, choice: string) {
    if (!sheet) return
    const row = sheet.rows.find((r) => r.kind === 'line' && lineKey(r) === key)
    if (!row) return
    try {
      const change = choice === INACTIVE ? { active: false } : { spread: spreadOf(choice) }
      const updated = await api.changeLine(version, sheet.cost_center_code, row.work_center_code!, row.activity_type_code, change)
      applied(updated)
      const after = updated.rows.find((r) => r.kind === 'line' && lineKey(r) === key)
      setMessage({
        kind: 'ok',
        text:
          choice === INACTIVE
            ? `${row.work_center_code} is inactive in this round: it left the sheet and credits nothing.`
            : `${row.work_center_code}: a year total typed from now on ${after ? spreadText(after) : 'spreads as chosen'}. Months already planned keep their values.`,
      })
    } catch (e) {
      showError(e)
    }
  }

  async function activate(workCenter: string, activityType: string) {
    if (!sheet) return
    try {
      applied(await api.changeLine(version, sheet.cost_center_code, workCenter, activityType, { active: true }))
      setMessage(null)
    } catch (e) {
      showError(e)
    }
  }

  async function step(direction: 'undo' | 'redo') {
    const stack = direction === 'undo' ? undoStack : redoStack
    const batch = stack[stack.length - 1]
    if (!sheet || !batch || saving || locked) return
    const take = direction === 'undo' ? setUndoStack : setRedoStack
    const give = direction === 'undo' ? setRedoStack : setUndoStack
    setSaving(true)
    try {
      const updated = await api.capacityUndo(version, sheet.cost_center_code, batch)
      setSheet(updated)
      take((s) => s.slice(0, -1))
      if (updated.batch_id) give((s) => [...s, updated.batch_id!])
      setMessage(null)
    } catch (e) {
      if (e instanceof ConflictError) {
        // Someone else changed it since: this step can't be taken any more.
        take((s) => s.slice(0, -1))
        setMessage({
          kind: 'info',
          text: `Can’t ${direction}: ${e.conflicts.length} cell(s) or line(s) were changed by someone else since. The latest values are shown, nothing was changed.`,
        })
        load()
      } else showError(e)
    } finally {
      setSaving(false)
    }
  }

  // Ctrl+Z / Cmd+Z undoes, Ctrl+Y or Ctrl+Shift+Z redoes, unless a text field (the cell editor
  // included) has the focus: there the keys undo typing, as usual.
  const onKey = useEffectEvent((e: KeyboardEvent) => {
    const target = e.target as HTMLElement | null
    if (!(e.ctrlKey || e.metaKey) || target?.closest('input, textarea, [contenteditable="true"]')) return
    const key = e.key.toLowerCase()
    if (key === 'z' && !e.shiftKey) {
      e.preventDefault()
      void step('undo')
    } else if (key === 'y' || (key === 'z' && e.shiftKey)) {
      e.preventDefault()
      void step('redo')
    }
  })
  useEffect(() => {
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  async function submit() {
    if (!sheet) return
    try {
      await api.act(version, { action: 'submit', cost_centers: [sheet.cost_center_code] })
      setMessage({ kind: 'ok', text: `${sheet.cost_center_code} submitted for approval: its capacity and costs.` })
      load()
      loadUnits()
    } catch (e) {
      showError(e)
    }
  }

  /** Write the gridnote's total as the line's hours for the year, as if typed into the year
   * column. */
  async function insertYear(key: string, total: string) {
    if (!sheet) return
    const rowIndex = sheet.rows.findIndex((r) => r.kind === 'line' && lineKey(r) === key)
    const row = sheet.rows[rowIndex]
    if (!row || !editable(row, COL_FY, layout)) throw new Error('this line’s hours can’t be changed here')
    await save([{ rowIndex, prop: COL_FY, raw: total }])
  }

  /** Pressing a line's notes marker or badge opens its notes or menu; handled on mouse down,
   * before the grid selects and redraws the cell under the pointer. */
  function openMenu(e: React.MouseEvent) {
    if (e.button !== 0) return
    if ((e.target as HTMLElement).closest('[data-notes], [data-line]')) e.preventDefault()
    const notes = (e.target as HTMLElement).closest<HTMLElement>('[data-notes]')
    if (notes?.dataset.notes) {
      e.stopPropagation()
      setNotesFor({ key: notes.dataset.notes, thread: null })
      onFocused?.()
      return
    }
    const badge = (e.target as HTMLElement).closest<HTMLElement>('[data-line]')
    if (!badge?.dataset.line) return
    e.stopPropagation()
    setMenu({ key: badge.dataset.line, anchor: badge.getBoundingClientRect() })
  }

  if (units === null) return message ? <div className="su-note error">{message.text}</div> : null
  if (units.length === 0) {
    return (
      <div className="su-card empty-state">
        <h3>No productive cost centers</h3>
        <p className="su-hint">
          {controller
            ? 'Mark the cost centers that output activity as productive on the Setup page (step 6); they get a capacity sheet here.'
            : 'None of this round’s productive cost centers is yours to plan or read. The controller marks cost centers as productive and names capacity planners.'}
        </p>
      </div>
    )
  }

  const current = units.find((u) => u.code === unit)
  const open = sheet !== null && !locked
  // The notes shown beside the sheet: those opened here, or the line Home opened it for.
  const notesOpen =
    notesFor ?? (focus && sheet?.cost_center_code === unit ? { key: focus.key, thread: focus.thread } : null)
  const notesRow = notesOpen ? sheet?.rows.find((r) => r.kind === 'line' && lineKey(r) === notesOpen.key) : undefined
  const canSubmit = open && planning.some((p) => p.code === unit && p.can_write)
  const menuRow = menu && sheet?.rows.find((r) => r.kind === 'line' && lineKey(r) === menu.key)
  const menuOptions: Option[] = [
    ...(menuRow?.added
      ? [{ value: 'default', label: 'Evenly', hint: 'default for a new work center' }]
      : [
          { value: 'default', label: 'Like last year’s hours', hint: 'default' },
          { value: 'even', label: 'Evenly' },
        ]),
    ...profiles.map((p) => ({ value: `profile:${p.code}`, label: p.name, hint: 'profile' })),
    ...(controller ? [{ value: INACTIVE, label: 'Set inactive in this round', hint: 'line' }] : []),
  ]

  return (
    <div className="su pl" style={sheet ? { maxWidth: sheetWidth } : undefined}>
      <div className="pl-head">
        <div className="pl-title">
          <div className="su-eyebrow">Capacity{sheet ? ` · ${sheet.version_name}` : ''}</div>
          <Select
            variant="title"
            label="Cost center"
            value={unit}
            onChange={(code) => {
              onUnit(code)
              setMessage(null)
              setAdding(false)
              setUndoStack([])
              setRedoStack([])
            }}
            searchable
            options={units.map((u) => ({
              value: u.code,
              label: `${u.code} ${u.name}`,
              hint: STATUS_LABEL[u.status] + (u.can_write ? '' : ' · read only'),
              dot: u.status,
            }))}
          />
          <div className="pl-sub">
            Hours its work centers run, and what they credit the cost center
            {sheet && ` · fiscal year ${sheet.fiscal_year}`}
            {sheet && forecast && closed > 0 && ` · forecast ${closed}+${12 - closed}`}
            {sheet && quarters && ' · by quarter'}
            {saving && <span className="pl-saving"> · Saving…</span>}
          </div>
        </div>
        <div className="pl-actions">
          {current && <span className={`su-pill ${current.status}`}>{STATUS_LABEL[current.status]}</span>}
          {sheet?.deadline && <span className="su-pill neutral">Due {sheet.deadline}</span>}
          {open && (
            <span className="pl-undo" role="group" aria-label="Undo and redo">
              <button
                type="button"
                className="su-icon"
                disabled={undoStack.length === 0 || saving}
                onClick={() => void step('undo')}
                aria-label="Undo"
                title={`Undo your last change (Ctrl+Z)${undoStack.length ? ` · ${undoStack.length} step${undoStack.length === 1 ? '' : 's'}` : ''}`}
              >
                <UndoIcon />
              </button>
              <button
                type="button"
                className="su-icon"
                disabled={redoStack.length === 0 || saving}
                onClick={() => void step('redo')}
                aria-label="Redo"
                title="Redo (Ctrl+Y)"
              >
                <RedoIcon />
              </button>
            </span>
          )}
          {open && (
            <button type="button" className="su-btn" aria-expanded={adding} onClick={() => setAdding((a) => !a)}>
              <span className="pl-plus">+</span> Add line
            </button>
          )}
          {canSubmit && (
            <button type="button" className="su-btn primary" onClick={() => void submit()}>
              Submit for approval
            </button>
          )}
        </div>
      </div>

      {sheet?.lock_reason && (
        <div className="su-note pl-note">
          <strong>Read only:</strong> {sheet.lock_reason}.
        </div>
      )}
      {message && <div className={`su-note ${message.kind} pl-note`}>{message.text}</div>}
      {adding && sheet && (
        <AddLine
          sheet={sheet}
          onCancel={() => setAdding(false)}
          onAdd={async (line) => {
            try {
              applied(await api.addLine(version, sheet.cost_center_code, line))
              setAdding(false)
              setMessage({ kind: 'ok', text: `${line.work_center_code} added to the sheet.` })
            } catch (e) {
              showError(e)
            }
          }}
        />
      )}

      {sheet && sheet.rows.length === 0 ? (
        <div className="su-card empty-state">
          <h3>No work centers yet</h3>
          <p className="su-hint">
            The cost center’s work centers come from master data with their rates.{' '}
            {open ? 'Add a line for a work center, or a new one you plan to buy.' : 'There is nothing to plan here.'}
          </p>
        </div>
      ) : (
        sheet && (
          <div className="pl-sheet" onMouseDownCapture={openMenu}>
            <RevoGrid
              className="pl-revo"
              columns={columns}
              source={source}
              range
              resize
              applyOnClose
              theme="default"
              rowSize={32}
              hideAttribution
              useClipboard={{ rangeFill: true }}
              onBeforeedit={(e) => {
                e.preventDefault()
                void save([{ rowIndex: e.detail.rowIndex, prop: String(e.detail.prop), raw: e.detail.val }])
              }}
              onBeforerangeedit={(e) => {
                e.preventDefault()
                const changes = Object.entries(e.detail.data).flatMap(([rowIndex, values]) =>
                  Object.entries(values as GridRow).map(([prop, raw]) => ({ rowIndex: Number(rowIndex), prop, raw })),
                )
                void save(changes)
              }}
            />
            <div className="pl-foot">
              <span className="pv-key">
                <i className="k-edit" /> entered
              </span>
              {closed > 0 && (
                <span className="pv-key">
                  <i className="k-act" /> actual hours (closed months)
                </span>
              )}
              <span className="pv-key">
                <i className="k-cmp" /> comparison
              </span>
              <span className="pv-key">
                <i className="k-credit" /> credit to the cost center
              </span>
              {open && (
                <span className="pl-help">
                  {closed > 0
                    ? 'Hours per open month, or a year total in “Forecast”: the full year, of which the open months take what the actual hours leave.'
                    : `Hours per ${quarters ? 'quarter' : 'month'}, or a year total in “Plan” that spreads as the line’s badge says.`}{' '}
                  Rates are per hour. The credits appear on the cost center’s planning sheet. Ctrl+Z undoes.
                </span>
              )}
            </div>
          </div>
        )
      )}

      {sheet && sheet.inactive.length > 0 && (
        <div className="su-card cp-inactive">
          <h3>Inactive in this round</h3>
          <ul>
            {sheet.inactive.map((l) => (
              <li key={`${l.work_center_code}|${l.activity_type_code}`}>
                <span className="pl-code">{l.work_center_code}</span> {l.work_center_name} · {l.activity_type_name}
                {controller && open && (
                  <button type="button" className="su-btn ghost sm" onClick={() => void activate(l.work_center_code, l.activity_type_code)}>
                    Activate
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {notesOpen && sheet && notesRow && (
        <NotesPanel
          key={`${sheet.cost_center_code}/${notesOpen.key}`}
          version={version}
          line={{
            module: 'capacity',
            cost_center: sheet.cost_center_code,
            work_center: notesRow.work_center_code,
            activity_type: notesRow.activity_type_code,
          }}
          title={`${notesRow.work_center_code} ${notesRow.work_center_name ?? ''} · ${notesRow.activity_type_name}`}
          canInsert={editable(notesRow, COL_FY, layout)}
          focusThread={notesOpen.thread}
          onInsertTotal={(total) => insertYear(notesOpen.key, total)}
          onChanged={load}
          onClose={() => {
            setNotesFor(null)
            onFocused?.()
          }}
        />
      )}
      {menu && menuRow && (
        <Select
          label={`How ${menuRow.work_center_code} spreads its year total`}
          anchor={menu.anchor}
          onClose={() => setMenu(null)}
          value={spreadKey(menuRow)}
          options={menuOptions}
          onChange={(choice) => void chooseFromMenu(menu.key, choice)}
        />
      )}
    </div>
  )
}

function AddLine({
  sheet,
  onCancel,
  onAdd,
}: {
  sheet: CapacitySheet
  onCancel: () => void
  onAdd: (line: { work_center_code: string; activity_type_code: string; rate: string; new_work_center_name: string | null }) => Promise<void>
}) {
  const [workCenter, setWorkCenter] = useState(sheet.work_centers[0]?.code ?? NEW_WORK_CENTER)
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [activityType, setActivityType] = useState(sheet.activity_types[0]?.code ?? '')
  const [rate, setRate] = useState('')
  const isNew = workCenter === NEW_WORK_CENTER
  const rateValue = parse(rate)
  const valid = (isNew ? code.trim() && name.trim() : workCenter) && activityType && rateValue !== undefined && rateValue !== null

  return (
    <form
      className="su-card cp-add"
      onSubmit={(e) => {
        e.preventDefault()
        if (!valid) return
        void onAdd({
          work_center_code: isNew ? code.trim() : workCenter,
          activity_type_code: activityType,
          rate: rateValue!,
          new_work_center_name: isNew ? name.trim() : null,
        })
      }}
    >
      <div className="su-row">
        <div className="su-field cp-w-wc">
          Work center
          <Select
            label="Work center"
            value={workCenter}
            onChange={setWorkCenter}
            options={[
              ...sheet.work_centers.map((w) => ({ value: w.code, label: w.name, hint: w.code })),
              { value: NEW_WORK_CENTER, label: 'New work center…', hint: 'e.g. a machine to buy' },
            ]}
          />
        </div>
        {isNew && (
          <>
            <label className="su-field">
              Code
              <input className="su-input md-w-code" value={code} onChange={(e) => setCode(e.target.value)} />
            </label>
            <label className="su-field cp-grow">
              Name
              <input className="su-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Laser cutter (planned)" />
            </label>
          </>
        )}
        <div className="su-field cp-w-type">
          Activity type
          <Select
            label="Activity type"
            value={activityType}
            onChange={setActivityType}
            options={sheet.activity_types.map((t) => ({ value: t.code, label: t.name, hint: t.code }))}
          />
        </div>
        <label className="su-field">
          Rate per hour
          <input className="su-input cp-w-rate" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />
        </label>
        <span className="cp-grow" />
        <button type="button" className="su-btn ghost" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="su-btn primary" disabled={!valid}>
          Add line
        </button>
      </div>
    </form>
  )
}
