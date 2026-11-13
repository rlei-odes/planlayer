import { RevoGrid } from '@revolist/react-datagrid'
import type { HyperFunc, VNode } from '@revolist/revogrid'
import { useCallback, useEffect, useEffectEvent, useMemo, useState } from 'react'
import { format, parse } from './amounts'
import {
  api,
  ConflictError,
  STATUS_LABEL,
  type PlanningUnit,
  type Position,
  type PositionIn,
  type WorkforceEdit,
  type WorkforceRow,
  type WorkforceSheet,
} from './api'
import { RedoIcon, UndoIcon } from './icons'
import type { H } from './lineMarks'
import { byColumn, isClosed, monthsOf, periodColumns, span, type Layout } from './periods'
import Select from './Select'
import { pickUnit } from './units'

// The workforce sheet of a cost center (ADR 0020): FTE and headcount per staff type × category,
// as levels at month end. The year shows its last month, a quarter its last month; a value typed
// for the year or a quarter fills its months. In a positions round the sheet is the roll-up of
// the positions listed below it, read only, with the build-up of new and replaced positions.

const COL_LINE = 'line'
const COL_FY = 'fy'
const comparisonProp = (i: number) => `c${i}`
const SCROLLBAR = 18
const MEASURE_LABEL = { fte: 'FTE', headcount: 'Headcount' } as const
const MONTH = new Intl.DateTimeFormat('en', { month: 'short', timeZone: 'UTC' })
const DAY = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })

type GridRow = Record<string, string>
type Message = { kind: 'error' | 'info' | 'ok'; text: string }
// What the grid shows: the sheet's rows, with a heading row per measure when it shows two.
type Shown = { kind: 'section'; measure: WorkforceRow['measure'] } | WorkforceRow

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

/** A level: the value of the last month given. */
const last = (xs: (string | null)[]) => xs[xs.length - 1] ?? null

/** Headcount reads as whole heads where it is whole; FTE keeps two decimals. */
function shownValue(value: string | null, measure: WorkforceRow['measure']): string {
  if (value === null) return ''
  const text = format(value)
  return measure === 'headcount' && text.endsWith('.00') ? text.slice(0, -3) : text
}

function editable(row: Shown | undefined, prop: string, layout: Layout): boolean {
  if (!row || row.kind !== 'line' || !row.editable) return false
  if (prop === COL_FY) return true
  return monthsOf(prop) !== null && !isClosed(prop, layout)
}

function cellKind(row: Shown | undefined, prop: string, layout: Layout, nComp: number): string {
  if (!row) return ''
  if (row.kind === 'section') return 'wf-section'
  const comparison = /^c\d+$/.test(prop)
  const lastCmp = prop === comparisonProp(nComp - 1) ? ' pl-cmp-last' : ''
  if (row.kind === 'group' || row.kind === 'total') {
    const base = row.kind === 'total' ? 'pl-group wf-total' : 'pl-group'
    return (comparison ? `${base} pl-cmp` : isClosed(prop, layout) ? `${base} pl-act` : base) + lastCmp
  }
  if (comparison) return `pl-cmp${lastCmp}`
  if (prop === COL_LINE) return ''
  if (isClosed(prop, layout)) return 'pl-act'
  const kind = editable(row, prop, layout) ? 'pl-edit' : 'pl-plain'
  return prop === COL_FY ? `${kind} pl-total` : kind
}

function lineCell(h: H, row: Shown | undefined, rolledUp: boolean) {
  if (!row) return ''
  if (row.kind === 'section') return h('span', { class: 'wf-section-label' }, MEASURE_LABEL[row.measure])
  if (row.kind === 'total') return h('span', { class: 'pl-acc-name' }, `Total ${MEASURE_LABEL[row.measure]}`)
  if (row.kind === 'group')
    return h('span', { class: 'pl-acc-name' }, [
      row.staff_type_name ?? '',
      row.external ? h('span', { class: 'wf-ext', title: 'External staff' }, 'external') : '',
    ])
  return h('span', { class: 'pl-acc-in', title: rolledUp ? 'Rolled up from the positions below' : undefined }, [
    h('span', { class: 'pl-acc-name' }, [h('span', { class: 'pl-code' }, row.staff_category_code ?? ''), row.staff_category_name ?? '']),
    h('span', { class: 'pl-acc-tags' }, [h('span', { class: `pv-form ${rolledUp ? 'annual' : 'monthly'}` }, rolledUp ? 'Positions' : 'Level')]),
  ])
}

export default function WorkforcePage({
  version,
  unit: chosen,
  onUnit,
  onWidth,
}: {
  version: string
  /** The cost center chosen last, here or on the other sheets. */
  unit: string
  onUnit: (code: string) => void
  onWidth: (px: number | null) => void
}) {
  const [units, setUnits] = useState<PlanningUnit[] | null>(null)
  const [sheet, setSheet] = useState<WorkforceSheet | null>(null)
  const [missing, setMissing] = useState(false)
  const [message, setMessage] = useState<Message | null>(null)
  const [saving, setSaving] = useState(false)
  const [adding, setAdding] = useState(false)
  const [undoStack, setUndoStack] = useState<string[]>([])
  const [redoStack, setRedoStack] = useState<string[]>([])
  const unit = units ? pickUnit(units, chosen) : ''

  const showError = useCallback((e: unknown) => setMessage({ kind: 'error', text: errorText(e) }), [])
  const loadUnits = useCallback(() => {
    api.costCenters(version).then(setUnits, showError)
  }, [version, showError])
  useEffect(loadUnits, [loadUnits])

  useEffect(() => {
    if (unit && unit !== chosen) onUnit(unit)
  }, [chosen, unit, onUnit])

  const load = useCallback(() => {
    if (!unit) return
    api.workforceSheet(version, unit).then(
      (s) => {
        setSheet(s)
        setMissing(false)
      },
      (e: unknown) => {
        // A round without workforce planning has no sheets: say so instead of an error.
        if (errorText(e).includes('no workforce planning')) setMissing(true)
        else showError(e)
      },
    )
  }, [version, unit, showError])
  useEffect(load, [load])

  function applied(updated: WorkforceSheet) {
    setSheet(updated)
    if (updated.batch_id && updated.mode !== 'positions') setUndoStack((s) => [...s, updated.batch_id!])
    setRedoStack([])
    if (sheet && updated.status !== sheet.status) loadUnits()
  }

  const locked = sheet?.lock_reason != null
  const positions = sheet?.mode === 'positions'
  const quarters = sheet?.grain === 'quarter'
  const closed = sheet?.closed_months ?? 0
  const layout = useMemo<Layout>(() => ({ quarters, closed }), [quarters, closed])
  const forecast = sheet?.version_kind === 'forecast'

  const shown = useMemo<Shown[]>(() => {
    const rows = sheet?.rows ?? []
    const measures = [...new Set(rows.map((r) => r.measure))]
    if (measures.length < 2) return rows
    return rows.flatMap((r, i) => (i === 0 || rows[i - 1].measure !== r.measure ? [{ kind: 'section' as const, measure: r.measure }, r] : [r]))
  }, [sheet])

  const columns = useMemo(() => {
    const periods = sheet?.periods ?? []
    const nComp = sheet?.comparisons.length ?? 0
    const column = (prop: string, name: string, size: number, headerClass = '', title?: string) => ({
      prop,
      name,
      size,
      columnProperties: () => ({ class: `pl-head ${headerClass}${title ? ' pl-head-noted' : ''}`, title }),
      readonly: ({ rowIndex }: { rowIndex: number }) => !editable(shown[rowIndex], prop, layout),
      cellProperties: ({ rowIndex }: { rowIndex: number }) => ({
        class: [prop === COL_LINE ? 'pl-acc' : 'pl-num', cellKind(shown[rowIndex], prop, layout, nComp)].join(' '),
      }),
    })
    const end = periods.length === 12 ? MONTH.format(new Date(periods[11])) : 'year end'
    const yearTitle = `The level at the end of ${end}. A value typed here fills every ${closed ? 'open ' : ''}month.`
    return [
      {
        ...column(COL_LINE, 'Staff type · category', 380, 'pl-head-acc'),
        pin: 'colPinStart' as const,
        cellTemplate: (h: HyperFunc<VNode>, { rowIndex }: { rowIndex: number }) =>
          lineCell(h as unknown as H, shown[rowIndex], positions),
      },
      ...(sheet?.comparisons ?? []).map((c, i) =>
        column(comparisonProp(i), c.label, 104, 'pl-head-cmp', [c.note, `End of ${end}`].filter(Boolean).join(' · ')),
      ),
      column(COL_FY, `${forecast ? 'Forecast' : 'Plan'} ${sheet?.fiscal_year ?? ''}`, 112, 'pl-head-plan', yearTitle),
      ...periodColumns(periods, layout).map((c) => column(c.prop, c.label, layout.quarters ? 100 : 76, c.closed ? 'pl-head-act' : '')),
    ]
  }, [sheet, shown, layout, forecast, positions, closed])

  const sheetWidth = columns.reduce((n, c) => n + c.size, 0) + 2 + SCROLLBAR
  const hasSheet = sheet !== null
  useEffect(() => {
    onWidth(hasSheet ? sheetWidth : null)
    return () => onWidth(null)
  }, [hasSheet, sheetWidth, onWidth])

  const source = useMemo<GridRow[]>(
    () =>
      shown.map((row) => {
        if (row.kind === 'section') return { [COL_LINE]: MEASURE_LABEL[row.measure] }
        const values = row.plan.map((c) => c.value)
        const out: GridRow = {
          [COL_LINE]: row.staff_category_name ?? row.staff_type_name ?? '',
          [COL_FY]: shownValue(last(values), row.measure),
        }
        row.comparisons.forEach((months, i) => (out[comparisonProp(i)] = shownValue(last(months), row.measure)))
        for (const [prop, value] of Object.entries(byColumn(values, layout, last))) out[prop] = shownValue(value, row.measure)
        return out
      }),
    [shown, layout],
  )

  async function save(changes: { rowIndex: number; prop: string; raw: unknown }[]) {
    if (!sheet) return
    const edits: WorkforceEdit[] = []
    const refused: string[] = []
    for (const { rowIndex, prop, raw } of changes) {
      const row = shown[rowIndex]
      if (!row || row.kind !== 'line' || !editable(row, prop, layout)) continue
      const value = parse(String(raw ?? ''))
      if (value === undefined || (value !== null && value.startsWith('-'))) {
        refused.push(`${row.staff_category_name}: “${String(raw)}” is not ${row.measure === 'fte' ? 'an FTE' : 'a headcount'}`)
        continue
      }
      const n = prop === COL_FY ? null : Number(prop.slice(1))
      edits.push({
        staff_type_code: row.staff_type_code!,
        staff_category_code: row.staff_category_code!,
        measure: row.measure,
        month: prop.startsWith('m') ? n : null,
        quarter: prop.startsWith('q') ? n : null,
        value,
        expected: row.plan.map((c) => c.row_version),
      })
    }
    setMessage(refused.length ? { kind: 'error', text: refused.slice(0, 3).join(' · ') } : null)
    if (edits.length === 0) return
    setSaving(true)
    try {
      applied(await api.workforceEdit(version, sheet.cost_center_code, edits))
    } catch (e) {
      if (e instanceof ConflictError) {
        setMessage({ kind: 'info', text: `${e.message}; the latest values are shown, nothing was saved.` })
        load()
      } else showError(e)
    } finally {
      setSaving(false)
    }
  }

  async function step(direction: 'undo' | 'redo') {
    const stack = direction === 'undo' ? undoStack : redoStack
    const batch = stack[stack.length - 1]
    if (!sheet || !batch || saving || locked || positions) return
    const take = direction === 'undo' ? setUndoStack : setRedoStack
    const give = direction === 'undo' ? setRedoStack : setUndoStack
    setSaving(true)
    try {
      const updated = await api.workforceUndo(version, sheet.cost_center_code, batch)
      setSheet(updated)
      take((s) => s.slice(0, -1))
      if (updated.batch_id) give((s) => [...s, updated.batch_id!])
      setMessage(null)
    } catch (e) {
      if (e instanceof ConflictError) {
        take((s) => s.slice(0, -1))
        setMessage({ kind: 'info', text: `Can’t ${direction}: someone changed those cells since. The latest values are shown.` })
        load()
      } else showError(e)
    } finally {
      setSaving(false)
    }
  }

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
      setMessage({ kind: 'ok', text: `${sheet.cost_center_code} submitted for approval: its workforce and costs.` })
      load()
      loadUnits()
    } catch (e) {
      showError(e)
    }
  }

  async function act(task: () => Promise<WorkforceSheet>, done: string): Promise<boolean> {
    setSaving(true)
    try {
      applied(await task())
      setMessage({ kind: 'ok', text: done })
      return true
    } catch (e) {
      showError(e)
      return false
    } finally {
      setSaving(false)
    }
  }

  if (units === null) return message ? <div className="su-note error">{message.text}</div> : null
  if (missing) {
    return (
      <div className="su-card empty-state">
        <h3>No workforce planning in this round</h3>
        <p className="su-hint">
          The controller turns it on in the round’s setup (step 6): FTE, headcount, both, or positions.
        </p>
      </div>
    )
  }
  if (units.length === 0) {
    return (
      <div className="su-card empty-state">
        <h3>No cost centers to show</h3>
        <p className="su-hint">You don’t plan or read any cost center of this round.</p>
      </div>
    )
  }

  const current = units.find((u) => u.code === unit)
  const open = sheet !== null && !locked
  const canSubmit = open && !!current?.can_write
  const modeText = sheet
    ? {
        fte: 'FTE per staff type and category',
        headcount: 'Headcount per staff type and category',
        both: 'FTE and headcount per staff type and category',
        positions: 'Positions, rolled up into FTE and headcount',
      }[sheet.mode]
    : ''

  return (
    <div className={`su pl${positions ? ' wf-scroll' : ''}`} style={sheet ? { maxWidth: Math.max(sheetWidth, 1100) } : undefined}>
      <div className="pl-head">
        <div className="pl-title">
          <div className="su-eyebrow">Workforce{sheet ? ` · ${sheet.version_name}` : ''}</div>
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
            {modeText}
            {sheet && ` · month-end levels, fiscal year ${sheet.fiscal_year}`}
            {sheet && forecast && closed > 0 && ` · forecast ${closed}+${12 - closed}`}
            {sheet && quarters && ' · by quarter'}
            {saving && <span className="pl-saving"> · Saving…</span>}
          </div>
        </div>
        <div className="pl-actions">
          {current && <span className={`su-pill ${current.status}`}>{STATUS_LABEL[current.status]}</span>}
          {sheet?.deadline && <span className="su-pill neutral">Due {sheet.deadline}</span>}
          {open && !positions && (
            <span className="pl-undo" role="group" aria-label="Undo and redo">
              <button
                type="button"
                className="su-icon"
                disabled={undoStack.length === 0 || saving}
                onClick={() => void step('undo')}
                aria-label="Undo"
                title="Undo your last change (Ctrl+Z)"
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
          {open && !positions && (
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
          onAdd={async (t, c) => {
            if (await act(() => api.addWorkforceLine(version, sheet.cost_center_code, { staff_type_code: t, staff_category_code: c }), 'Line added.'))
              setAdding(false)
          }}
        />
      )}

      {sheet && sheet.rows.length === 0 ? (
        <div className="su-card empty-state">
          <h3>{positions ? 'No positions yet' : 'No lines yet'}</h3>
          <p className="su-hint">
            {positions
              ? 'Add the positions below, or take the people from HR’s staff list as a start.'
              : open
                ? 'Lines come from last year’s actuals. Add a line for a staff type and category to plan.'
                : 'There is nothing to show here.'}
          </p>
        </div>
      ) : (
        sheet && (
          // With the positions below, the page scrolls and the grid is as tall as its rows.
          <div className="pl-sheet" style={positions ? { height: Math.min(shown.length * 32 + 38 + SCROLLBAR + 50, 640) } : undefined}>
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
              {!positions && (
                <span className="pv-key">
                  <i className="k-edit" /> entered
                </span>
              )}
              {positions && (
                <span className="pv-key">
                  <i className="k-calc" /> from the positions
                </span>
              )}
              {closed > 0 && (
                <span className="pv-key">
                  <i className="k-act" /> actuals (closed months)
                </span>
              )}
              <span className="pv-key">
                <i className="k-cmp" /> comparison
              </span>
              <span className="pl-help">
                Levels at month end: the year and each quarter show their last month.{' '}
                {open && !positions && `A value typed for the year${quarters ? ' or a quarter' : ''} fills its ${closed ? 'open ' : ''}months. Ctrl+Z undoes.`}
                {positions && 'A position counts in a month when it is active on the month’s last day.'}
              </span>
            </div>
          </div>
        )
      )}

      {sheet && positions && (
        <Positions
          sheet={sheet}
          open={open}
          saving={saving}
          onTakeHr={() => act(() => api.staffFromHr(version, sheet.cost_center_code), 'People from HR added as positions.')}
          onSave={(p, id) =>
            act(
              () =>
                id === null
                  ? api.addPosition(version, sheet.cost_center_code, p)
                  : api.changePosition(version, sheet.cost_center_code, id, p),
              id === null ? `${p.title} added.` : `${p.title} saved.`,
            )
          }
          onDelete={(p) => act(() => api.deletePosition(version, sheet.cost_center_code, p.position_id), `${p.title} deleted.`)}
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
  sheet: WorkforceSheet
  onCancel: () => void
  onAdd: (staffType: string, category: string) => Promise<void>
}) {
  const [staffType, setStaffType] = useState(sheet.staff_types[0]?.code ?? '')
  const [category, setCategory] = useState(sheet.staff_categories[0]?.code ?? '')
  return (
    <form
      className="su-card cp-add"
      onSubmit={(e) => {
        e.preventDefault()
        if (staffType && category) void onAdd(staffType, category)
      }}
    >
      <div className="su-row">
        <div className="su-field cp-w-type">
          Staff type
          <Select
            label="Staff type"
            value={staffType}
            onChange={setStaffType}
            options={sheet.staff_types.map((t) => ({ value: t.code, label: t.name, hint: t.external ? 'external' : t.code }))}
          />
        </div>
        <div className="su-field cp-w-type">
          Category
          <Select
            label="Category"
            value={category}
            onChange={setCategory}
            searchable
            options={sheet.staff_categories.map((c) => ({ value: c.code, label: c.name, hint: c.code }))}
          />
        </div>
        <span className="cp-grow" />
        <button type="button" className="su-btn ghost" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="su-btn primary" disabled={!staffType || !category}>
          Add line
        </button>
      </div>
      {sheet.staff_categories.length === 0 && (
        <p className="su-hint">No staff categories yet: the controller adds them under Master data.</p>
      )}
    </form>
  )
}

// --- Positions ----------------------------------------------------------------------------------

type Draft = {
  code: string
  title: string
  person_name: string
  staff_type_code: string
  staff_category_code: string
  entry_date: string
  leave_date: string
  contract_percent: string
  is_plan: boolean
  is_replacement: boolean
  description: string
}

function draftOf(p: Position | null, sheet: WorkforceSheet): Draft {
  return {
    code: p?.code ?? '',
    title: p?.title ?? '',
    person_name: p?.person_name ?? '',
    staff_type_code: p?.staff_type_code ?? sheet.staff_types[0]?.code ?? '',
    staff_category_code: p?.staff_category_code ?? sheet.staff_categories[0]?.code ?? '',
    entry_date: p?.entry_date ?? '',
    leave_date: p?.leave_date ?? '',
    contract_percent: p?.contract_percent ? String(Number(p.contract_percent)) : p ? '' : '100',
    is_plan: p?.is_plan ?? true,
    is_replacement: p?.is_replacement ?? false,
    description: p?.description ?? '',
  }
}

function positionIn(d: Draft): PositionIn {
  const blank = (s: string) => (s.trim() ? s.trim() : null)
  return {
    code: blank(d.code),
    title: d.title.trim(),
    person_name: blank(d.person_name),
    staff_type_code: d.staff_type_code,
    staff_category_code: d.staff_category_code,
    entry_date: blank(d.entry_date),
    leave_date: blank(d.leave_date),
    contract_percent: blank(d.contract_percent),
    is_plan: d.is_plan,
    is_replacement: d.is_plan && d.is_replacement,
    description: blank(d.description),
  }
}

function Positions({
  sheet,
  open,
  saving,
  onTakeHr,
  onSave,
  onDelete,
}: {
  sheet: WorkforceSheet
  open: boolean
  saving: boolean
  onTakeHr: () => Promise<boolean>
  onSave: (p: PositionIn, id: number | null) => Promise<boolean>
  onDelete: (p: Position) => Promise<boolean>
}) {
  // The position being edited: its id, or "new"; null while none is.
  const [editing, setEditing] = useState<number | 'new' | null>(null)
  const [draft, setDraft] = useState<Draft>(() => draftOf(null, sheet))
  const [filter, setFilter] = useState<'all' | 'plan' | 'staff'>('all')
  const [confirm, setConfirm] = useState<number | null>(null)
  const types = Object.fromEntries(sheet.staff_types.map((t) => [t.code, t]))
  const categories = Object.fromEntries(sheet.staff_categories.map((c) => [c.code, c.name]))
  const b = sheet.build_up
  const months = sheet.periods.map((p) => MONTH.format(new Date(p)))
  const shown = sheet.positions.filter((p) => filter === 'all' || (filter === 'plan') === p.is_plan)
  const external = types[draft.staff_type_code]?.external ?? false
  const percent = draft.contract_percent.trim()
  const valid = draft.title.trim() && draft.staff_type_code && draft.staff_category_code && (external || percent)

  function edit(p: Position | null) {
    setDraft(draftOf(p, sheet))
    setEditing(p ? p.position_id : 'new')
  }

  const plan = sheet.positions.filter((p) => p.is_plan)
  const tiles: { label: string; value: number; hint: string }[] = [
    { label: 'Positions', value: sheet.positions.length, hint: 'listed for the year' },
    { label: 'New', value: b ? b.new.reduce((a, n) => a + n, 0) : 0, hint: 'plan positions built up' },
    { label: 'Replacements', value: b ? b.replacements.reduce((a, n) => a + n, 0) : 0, hint: 'plan positions replacing a leaver' },
    { label: 'Leaving', value: b ? b.leaving.reduce((a, n) => a + n, 0) : 0, hint: 'no longer counted by year end' },
  ]

  return (
    <>
      <section className="su-card wf-buildup">
        <div className="fw-card-head">
          <h3>Build-up</h3>
          <span className="su-hint">
            {plan.length} plan position{plan.length === 1 ? '' : 's'} · headcount changes month to month by entering minus leaving
          </span>
        </div>
        <div className="wf-tiles">
          {tiles.map((t) => (
            <div key={t.label} className="wf-tile">
              <strong>{t.value}</strong>
              <span>{t.label}</span>
              <small>{t.hint}</small>
            </div>
          ))}
        </div>
        {b && (
          <div className="tbl-scroll">
            <table className="tbl wf-months">
              <thead>
                <tr>
                  <th />
                  {months.map((m, i) => (
                    <th key={i} className="num">
                      {m}
                    </th>
                  ))}
                  <th className="num">Year</th>
                </tr>
              </thead>
              <tbody>
                {(
                  [
                    ['Entering', b.entering],
                    ['of them new', b.new],
                    ['of them replacements', b.replacements],
                    ['Leaving', b.leaving],
                  ] as const
                ).map(([label, values]) => (
                  <tr key={label} className={label.startsWith('of') ? 'wf-sub' : undefined}>
                    <td>{label}</td>
                    {values.map((n, i) => (
                      <td key={i} className={`num${n ? '' : ' muted'}`}>
                        {n || '·'}
                      </td>
                    ))}
                    <td className="num">
                      <strong>{values.reduce((a, n) => a + n, 0)}</strong>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="su-card wf-positions">
        <div className="fw-card-head">
          <h3>Positions</h3>
          <div className="seg" role="group" aria-label="Show">
            {(
              [
                ['all', 'All', sheet.positions.length],
                ['staff', 'Staff', sheet.positions.length - plan.length],
                ['plan', 'Plan positions', plan.length],
              ] as const
            ).map(([value, label, count]) => (
              <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>
                {label} <span className="count">{count}</span>
              </button>
            ))}
          </div>
          <span className="cp-grow" />
          {open && sheet.hr_available > 0 && (
            <button type="button" className="su-btn" disabled={saving} onClick={() => void onTakeHr()}>
              Add staff from HR ({sheet.hr_available})
            </button>
          )}
          {open && (
            <button type="button" className="su-btn primary" onClick={() => edit(null)}>
              <span className="pl-plus">+</span> Add position
            </button>
          )}
        </div>

        {editing !== null && (
          <form
            className="wf-editor"
            onSubmit={(e) => {
              e.preventDefault()
              if (!valid) return
              void onSave(positionIn(draft), editing === 'new' ? null : editing).then((ok) => ok && setEditing(null))
            }}
          >
            <div className="wf-grid">
              <label className="su-field wf-title">
                Position (title)
                <input className="su-input" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} required autoFocus />
              </label>
              <label className="su-field">
                Name
                <input
                  className="su-input"
                  value={draft.person_name}
                  placeholder={draft.is_plan ? 'vacant' : ''}
                  onChange={(e) => setDraft({ ...draft, person_name: e.target.value })}
                />
              </label>
              <label className="su-field wf-code">
                Number
                <input className="su-input" value={draft.code} placeholder="optional" onChange={(e) => setDraft({ ...draft, code: e.target.value })} />
              </label>
              <div className="su-field">
                Staff type
                <Select
                  label="Staff type"
                  value={draft.staff_type_code}
                  onChange={(v) => setDraft({ ...draft, staff_type_code: v })}
                  options={sheet.staff_types.map((t) => ({ value: t.code, label: t.name, hint: t.external ? 'external' : undefined }))}
                />
              </div>
              <div className="su-field">
                Category
                <Select
                  label="Category"
                  value={draft.staff_category_code}
                  searchable
                  onChange={(v) => setDraft({ ...draft, staff_category_code: v })}
                  options={sheet.staff_categories.map((c) => ({ value: c.code, label: c.name }))}
                />
              </div>
              <label className="su-field wf-pct">
                Contract %{external ? ' (optional)' : ''}
                <input
                  className="su-input"
                  inputMode="decimal"
                  value={draft.contract_percent}
                  onChange={(e) => setDraft({ ...draft, contract_percent: e.target.value })}
                  required={!external}
                />
              </label>
              <label className="su-field">
                Entry date
                <input className="su-input" type="date" value={draft.entry_date} onChange={(e) => setDraft({ ...draft, entry_date: e.target.value })} />
              </label>
              <label className="su-field">
                Leave date
                <input className="su-input" type="date" value={draft.leave_date} onChange={(e) => setDraft({ ...draft, leave_date: e.target.value })} />
              </label>
              <div className="su-field wf-flags">
                <label className="md-check">
                  <input
                    type="checkbox"
                    checked={draft.is_plan}
                    onChange={(e) => setDraft({ ...draft, is_plan: e.target.checked, is_replacement: e.target.checked && draft.is_replacement })}
                  />
                  Plan position (still to fill)
                </label>
                <label className={`md-check${draft.is_plan ? '' : ' wf-off'}`}>
                  <input
                    type="checkbox"
                    disabled={!draft.is_plan}
                    checked={draft.is_plan && draft.is_replacement}
                    onChange={(e) => setDraft({ ...draft, is_replacement: e.target.checked })}
                  />
                  Replaces a leaver
                </label>
              </div>
              <label className="su-field wf-desc">
                Description
                <textarea
                  className="su-input"
                  rows={2}
                  value={draft.description}
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                />
              </label>
            </div>
            <div className="su-row wf-editor-foot">
              <span className="su-hint">
                Blank dates mean “before the year” and “stays”. A position counts in a month when active on its last day.
              </span>
              <span className="cp-grow" />
              <button type="button" className="su-btn ghost" onClick={() => setEditing(null)}>
                Cancel
              </button>
              <button type="submit" className="su-btn primary" disabled={!valid || saving}>
                {editing === 'new' ? 'Add position' : 'Save position'}
              </button>
            </div>
          </form>
        )}

        {sheet.positions.length === 0 ? (
          <p className="su-hint wf-empty">
            No positions yet.{' '}
            {open && (sheet.hr_available > 0 ? 'Start from HR’s staff list, or add them one by one.' : 'Add them one by one.')}
          </p>
        ) : (
          <div className="tbl-scroll">
            <table className="tbl wf-table">
              <thead>
                <tr>
                  <th>Position</th>
                  <th>Name</th>
                  <th>Type · category</th>
                  <th>Entry</th>
                  <th>Leave</th>
                  <th className="num">Contract</th>
                  <th>Counted</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {shown.map((p) => (
                  <tr key={p.position_id} className={editing === p.position_id ? 'fw-current' : undefined}>
                    <td>
                      <span className="wf-pos">
                        {p.code && <span className="code">{p.code}</span>}
                        <strong>{p.title}</strong>
                        {p.is_plan && (
                          <span className={`su-pill ${p.is_replacement ? 'submitted' : 'in_progress'}`}>
                            {p.is_replacement ? 'Replacement' : 'New'}
                          </span>
                        )}
                      </span>
                      {p.description && <span className="wf-note">{p.description}</span>}
                    </td>
                    <td className={p.person_name ? undefined : 'muted'}>{p.person_name ?? 'vacant'}</td>
                    <td>
                      {types[p.staff_type_code]?.name ?? p.staff_type_code}
                      <span className="muted"> · {categories[p.staff_category_code] ?? p.staff_category_code}</span>
                    </td>
                    <td className="wf-date">{p.entry_date ? DAY.format(new Date(p.entry_date)) : <span className="muted">before</span>}</td>
                    <td className="wf-date">{p.leave_date ? DAY.format(new Date(p.leave_date)) : <span className="muted">stays</span>}</td>
                    <td className="num">{p.contract_percent ? `${Number(p.contract_percent)} %` : <span className="muted">–</span>}</td>
                    <td>
                      <span
                        className="wf-dots"
                        aria-label={p.counted.every(Boolean) ? 'All year' : `${p.counted.filter(Boolean).length} months`}
                        title={
                          p.counted.some(Boolean)
                            ? `Counted ${p.counted.every(Boolean) ? 'all year' : runOf(p.counted, sheet.periods)}`
                            : 'Not counted in this year'
                        }
                      >
                        {p.counted.map((c, i) => (
                          <i key={i} className={c ? 'on' : undefined} />
                        ))}
                      </span>
                    </td>
                    <td className="right">
                      {open && (
                        <span className="btn-row">
                          <button type="button" className="su-btn ghost sm" onClick={() => edit(p)}>
                            Edit
                          </button>
                          {confirm === p.position_id ? (
                            <button
                              type="button"
                              className="su-btn sm danger"
                              onClick={() => void onDelete(p).then(() => setConfirm(null))}
                              onBlur={() => setConfirm(null)}
                            >
                              Delete?
                            </button>
                          ) : (
                            <button type="button" className="su-btn ghost sm danger" onClick={() => setConfirm(p.position_id)}>
                              Delete
                            </button>
                          )}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  )
}

/** "Mar–Dec 2026": the months a position counts in, first to last. */
function runOf(counted: boolean[], periods: string[]): string {
  const first = counted.indexOf(true)
  const lastMonth = counted.lastIndexOf(true)
  return span(periods, first, lastMonth)
}
