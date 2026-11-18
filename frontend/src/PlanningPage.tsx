import { RevoGrid } from '@revolist/react-datagrid'
import type { HyperFunc, VNode } from '@revolist/revogrid'
import { useCallback, useEffect, useEffectEvent, useMemo, useState } from 'react'
import { display, format, numberFormat, parse, sum, toCents } from './amounts'
import { useScale } from './numberContext'
import { SheetScale } from './NumberFormat'
import {
  api,
  ConflictError,
  STATUS_LABEL,
  type Account,
  type Edit,
  type Grid,
  type ItemList,
  type LineSpread,
  type PlanningUnit,
  type ProfileRef,
  type Row,
} from './api'
import { RedoIcon, UndoIcon } from './icons'
import ItemListEditor from './ItemListEditor'
import { headerWithMarks, listMark, noteMark, type H } from './lineMarks'
import NotesPanel from './NotesPanel'
import PageHeader from './PageHeader'
import Select, { type Option } from './Select'
import { isClosed, monthProp, monthsOf, periodColumns, quarterProp, quarterly, span, type Layout } from './periods'
import { pickUnit } from './units'

// The planning sheet of one cost center (T-25, ADR 0007, ADR 0010), in the look of the setup
// page's preview: typed-in cells look like inputs, months calculated from the year total are
// tinted, months of full-year-only lines are hatched, comparisons are muted, and every line carries
// its entry form (⚠ when its spread fell back to even). The server decides what is editable.
// Clicking a line's badge chooses how that line spreads its year totals (ADR 0007).
// A round planned by quarter shows Q1–Q4 instead of months; a forecast's closed months show
// actuals, read only, and its year column is actuals to date plus the forecast (ADR 0015).

const COL_ACCOUNT = 'account'
const COL_FY = 'fy'
const comparisonProp = (i: number) => `c${i}`

type GridRow = Record<string, string>
type Message = { kind: 'error' | 'info' | 'ok'; text: string }

function editable(row: Row | undefined, prop: string, layout: Layout): boolean {
  if (!row || row.kind !== 'account') return false
  if (prop === COL_FY) return row.total_editable
  return monthsOf(prop) !== null && row.months_editable && !isClosed(prop, layout)
}

/** The class that gives a cell its look; one per kind of cell. */
function cellKind(row: Row | undefined, prop: string, locked: boolean, layout: Layout): string {
  if (!row) return ''
  if (row.kind === 'group') return isClosed(prop, layout) ? 'pl-group pl-act' : 'pl-group'
  // A credit (capacity, forwarding) is green across the line, as on the capacity sheet.
  if (row.credit && !/^c\d+$/.test(prop) && !isClosed(prop, layout)) return 'pl-credit'
  if (/^c\d+$/.test(prop)) return 'pl-cmp'
  if (prop === COL_FY) return editable(row, prop, layout) ? 'pl-edit pl-total' : 'pl-plain pl-total'
  if (monthsOf(prop) === null) return ''
  if (isClosed(prop, layout)) return 'pl-act'
  if (!row.months_visible) return 'pl-hidden'
  if (editable(row, prop, layout)) return 'pl-edit'
  return locked ? 'pl-plain' : 'pl-calc'
}



const METHOD_TEXT = { full: 'all that is left on its source', fixed: 'a fixed amount', rate: 'a rate per unit' } as const

/** What a forwarded line is: the forwarding, and who is on the other side (ADR 0018). */
function forwardedText(f: NonNullable<Row['forwarded']>): string {
  const other =
    f.side === 'charge'
      ? `charged by ${f.source_code} ${f.source_name}`
      : `passed on to ${f.receivers} cost center${f.receivers === 1 ? '' : 's'}, shown negative`
  return `Cost forwarding ${f.code} ${f.name} (${METHOD_TEXT[f.method]}): ${other}. Set by the controller; read only.`
}

/** A year-only line's months aren't shown, so how it spreads isn't chosen on the sheet. */
function spreadChoosable(row: Row): boolean {
  return row.kind === 'account' && row.entry_form !== 'annual' && row.total_editable
}

/** How the line is entered, then what a year total typed there does: "Monthly · actuals". A
 * line whose reference has no values spreads evenly, and the badge says so (with ⚠). */
function badgeLabel(row: Row): string {
  if (row.not_planned) return 'Not planned'
  if (row.capacity) return 'From capacity'
  if (row.personnel) return 'Personnel costs'
  if (row.forwarded) return row.forwarded.side === 'credit' ? 'Passed on' : 'Charged'
  if (!row.entry_form) return ''
  if (row.entry_form === 'annual') return 'Year only'
  const entry = row.entry_form === 'monthly' ? 'Monthly' : 'Year'
  return `${entry} · ${spreadWord(row)}`
}

function spreadWord(row: Row): string {
  if (row.fallback) return 'even'
  switch (row.spread) {
    case 'profile':
      return 'season'
    case 'reference':
      return row.reference_version ?? 'actuals'
    default:
      return 'even'
  }
}

const MONTH_YEAR = new Intl.DateTimeFormat('en', { month: 'long', year: 'numeric', timeZone: 'UTC' })

/** A comparison column's tooltip: the controller's note, and for actuals how far they go. */
function comparisonTitle(c: Grid['comparisons'][number]): string | undefined {
  const actuals = c.kind === 'prior_year_actuals' || c.kind === 'actuals'
  const coverage = !actuals
    ? null
    : c.actuals_until === null
      ? 'No actuals for this year yet.'
      : c.partial
        ? `Actuals up to ${MONTH_YEAR.format(new Date(c.actuals_until))}: the months after are blank, not zero, and the year is partial.`
        : null
  return [c.note, coverage].filter(Boolean).join(' · ') || undefined
}

function spreadText(row: Row): string {
  switch (row.spread) {
    case 'even':
      return 'spreads evenly over the months'
    case 'profile':
      return `spreads by the ${row.basis ?? 'profile'}`
    case 'reference':
      return row.fallback
        ? `spreads evenly: ${row.reference_version ? `${row.basis ?? row.reference_version} has no values` : 'there are no prior-year actuals'} for this line`
        : `spreads like ${row.basis ?? 'last year’s actuals'}`
    default:
      return ''
  }
}

function basisText(row: Row): string {
  if (row.not_planned === 'round')
    return 'Not planned in this round. Shown for its comparison values, which count in the totals.'
  if (row.not_planned === 'set')
    return 'Not among this cost center’s accounts in this round. Shown for its comparison values, which count in the totals.'
  if (row.capacity) return 'Credited by capacity planning: planned hours × rate of the work centers. Change it on the Capacity page.'
  if (row.personnel)
    return 'Written by personnel costs from the positions’ pay and the round’s rules. HR and the cost center’s owner change it on the Personnel costs page.'
  if (row.forwarded) return forwardedText(row.forwarded)
  const text =
    row.entry_form === 'monthly'
      ? `Months are entered; a year total ${spreadText(row)}.`
      : row.entry_form === 'annual'
        ? 'Only the year total is entered; it is stored as 12 equal months.'
        : `The year total ${spreadText(row)}.`
  const chosen = row.chosen_spread ? ' Chosen for this cost center.' : ''
  return text + chosen + (spreadChoosable(row) ? ' Click to change.' : '')
}

const DEFAULT_SPREAD = 'default'

function spreadKey(s: LineSpread | null): string {
  if (!s) return DEFAULT_SPREAD
  return s.basis === 'profile' ? `profile:${s.profile_code ?? ''}` : s.basis
}

function spreadOf(key: string): LineSpread | null {
  if (key === DEFAULT_SPREAD) return null
  if (key.startsWith('profile:')) return { basis: 'profile', profile_code: key.slice('profile:'.length) }
  return { basis: key === 'even' ? 'even' : 'reference', profile_code: null }
}

function spreadOptions(profiles: ProfileRef[]): Option[] {
  return [
    { value: DEFAULT_SPREAD, label: 'As the round is set up', hint: 'entry form' },
    { value: 'even', label: 'Evenly' },
    { value: 'reference', label: 'Like last year’s actuals' },
    ...profiles.map((p) => ({ value: `profile:${p.code}`, label: p.name, hint: 'profile' })),
  ]
}

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

// Room for a vertical scrollbar inside the grid: classic scrollbars take ~15 px, overlay ones none.
const SCROLLBAR = 18

export default function PlanningPage({
  version,
  unit: chosen,
  onUnit,
  onWidth,
  focus,
  onFocused,
}: {
  version: string
  /** The cost center chosen last, here or on the capacity page. */
  unit: string
  onUnit: (code: string) => void
  /** Reports the sheet's width, so the app widens its content area for wide sheets. */
  onWidth: (px: number | null) => void
  /** A line whose notes to open once the sheet is loaded (from Home). */
  focus?: { account: string; thread: number } | null
  onFocused?: () => void
}) {
  const { scale } = useScale()
  const [units, setUnits] = useState<PlanningUnit[] | null>(null)
  const unit = units ? pickUnit(units, chosen) : ''
  const [grid, setGrid] = useState<Grid | null>(null)
  const [accounts, setAccounts] = useState<Account[]>([])
  const [message, setMessage] = useState<Message | null>(null)
  const [saving, setSaving] = useState(false)
  // Undo (E-05): the change batches of this visit to the sheet, newest last. Undoing a batch
  // writes a new one, which goes on the other stack, so redo is undoing the undo.
  const [undoStack, setUndoStack] = useState<string[]>([])
  const [redoStack, setRedoStack] = useState<string[]>([])
  const [profiles, setProfiles] = useState<ProfileRef[]>([])
  // The open spread menu: the line and the badge it opens at.
  const [spreadMenu, setSpreadMenu] = useState<{ code: string; anchor: DOMRect } | null>(null)
  // The line whose notes are open beside the sheet, and the line whose item list is open (ADR 0017).
  const [notesFor, setNotesFor] = useState<{ code: string; thread: number | null } | null>(null)
  const [listFor, setListFor] = useState<{ code: string; list: ItemList | null } | null>(null)

  const showError = useCallback((e: unknown) => setMessage({ kind: 'error', text: errorText(e) }), [])

  const loadUnits = useCallback(() => {
    api.costCenters(version).then(setUnits, showError)
  }, [version, showError])

  useEffect(() => {
    loadUnits()
    api.profiles().then(setProfiles, showError)
  }, [version, loadUnits, showError])

  // What can be added depends on the cost center's account set (ADR 0014).
  useEffect(() => {
    if (unit) api.accounts(version, unit).then(setAccounts, showError)
  }, [version, unit, showError])

  // The cost center shown here is the one the capacity page shows next, where it is productive.
  useEffect(() => {
    if (unit && unit !== chosen) onUnit(unit)
  }, [chosen, unit, onUnit])

  const load = useCallback(() => {
    if (!unit) return
    api.grid(version, unit).then(setGrid, showError)
  }, [version, unit, showError])

  useEffect(load, [load])

  const locked = grid?.lock_reason != null
  const quarters = grid?.grain === 'quarter'
  const closed = grid?.closed_months ?? 0
  const layout = useMemo<Layout>(() => ({ quarters, closed }), [quarters, closed])
  const forecast = grid?.version_kind === 'forecast'

  const columns = useMemo(() => {
    const periods = grid?.periods ?? []
    const nComp = grid?.comparisons.length ?? 0
    const cellProperties =
      (prop: string) =>
      ({ rowIndex }: { rowIndex: number }) => {
        const row = grid?.rows[rowIndex]
        const classes = [prop === COL_ACCOUNT ? 'pl-acc' : 'pl-num', cellKind(row, prop, locked, layout)]
        if (prop === comparisonProp(nComp - 1)) classes.push('pl-cmp-last')
        return { class: classes.join(' ') }
      }
    // Cells hold the exact amount, which the editor opens with and a copy takes; they show it
    // in the user's scale and cents.
    const shown = numberFormat()
    const exact = scale === 'full' && shown.decimals
    const column = (prop: string, name: string, size: number, headerClass = '', title?: string) => ({
      prop,
      name,
      size,
      columnProperties: () => ({ class: `pl-head ${headerClass}${title ? ' pl-head-noted' : ''}`, title }),
      readonly: ({ rowIndex }: { rowIndex: number }) => !editable(grid?.rows[rowIndex], prop, layout),
      cellProperties: cellProperties(prop),
      ...(exact ? {} : { cellTemplate: (_h: unknown, { value }: { value?: unknown }) => display(parse(String(value ?? '')) ?? null, shown) }),
    })
    const periodCols = periodColumns(periods, layout).map((c) =>
      column(c.prop, c.label, layout.quarters ? 112 : 92, c.closed ? 'pl-head-act' : ''),
    )
    const yearTitle =
      layout.closed > 0 && periods.length === 12
        ? `Actuals ${span(periods, 0, layout.closed - 1)} + forecast ${span(periods, layout.closed, 11)}. A year total typed here is the full year: the open months take what the actuals leave.`
        : undefined
    return [
      {
        ...column(COL_ACCOUNT, 'Account', 390, 'pl-head-acc'),
        pin: 'colPinStart' as const,
        columnTemplate: (h: HyperFunc<VNode>) => headerWithMarks(h as unknown as H, 'Account', true),
        cellTemplate: (h: HyperFunc<VNode>, { rowIndex }: { rowIndex: number }) =>
          accountCell(h as unknown as H, grid?.rows[rowIndex], profiles, layout.quarters),
      },
      ...(grid?.comparisons ?? []).map((c, i) =>
        column(comparisonProp(i), c.label, 112, 'pl-head-cmp', comparisonTitle(c)),
      ),
      column(COL_FY, `${forecast ? 'Forecast' : 'Plan'} ${grid?.fiscal_year ?? ''}`, 124, 'pl-head-plan', yearTitle),
      ...periodCols,
    ]
  }, [grid, locked, profiles, layout, forecast, scale])

  // The page is as wide as its columns (plus the sheet's border), so nothing stretches past them.
  const sheetWidth = columns.reduce((n, c) => n + c.size, 0) + 2 + SCROLLBAR
  const hasGrid = grid !== null
  useEffect(() => {
    onWidth(hasGrid ? sheetWidth : null)
    return () => onWidth(null)
  }, [hasGrid, sheetWidth, onWidth])

  const source = useMemo<GridRow[]>(
    () =>
      (grid?.rows ?? []).map((row) => {
        const out: GridRow = { [COL_ACCOUNT]: row.name, [COL_FY]: format(sum(row.plan.map((c) => c.value))) }
        row.comparisons.forEach((values, i) => (out[comparisonProp(i)] = format(sum(values))))
        const shown = (m: number) => row.months_visible || m < closed
        if (quarters) {
          for (let q = 0; q < 4; q++) {
            const months = row.plan.slice(3 * q, 3 * q + 3)
            out[quarterProp(q)] = shown(3 * q) ? format(sum(months.map((c) => c.value))) : ''
          }
        } else row.plan.forEach((c, m) => (out[monthProp(m)] = shown(m) ? format(c.value) : ''))
        return out
      }),
    [grid, quarters, closed],
  )

  async function save(changes: { rowIndex: number; prop: string; raw: unknown }[]) {
    if (!grid) return
    const edits: Edit[] = []
    const refused: string[] = []
    for (const { rowIndex, prop, raw } of changes) {
      const row: Row | undefined = grid.rows[rowIndex]
      if (!row || !editable(row, prop, layout)) continue
      const value = parse(String(raw ?? ''))
      if (value === undefined) {
        refused.push(`${row.code}: “${String(raw)}” is not a number`)
        continue
      }
      const n = prop === COL_FY ? null : Number(prop.slice(1))
      edits.push({
        account_code: row.code,
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
      const updated = await api.edit(grid.version_code, grid.cost_center_code, edits)
      setGrid(updated)
      if (updated.batch_id) setUndoStack((s) => [...s, updated.batch_id!])
      setRedoStack([])
      if (updated.status !== grid.status) loadUnits()
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
    if (!grid || !batch || saving || locked) return
    const take = direction === 'undo' ? setUndoStack : setRedoStack
    const give = direction === 'undo' ? setRedoStack : setUndoStack
    setSaving(true)
    try {
      const updated = await api.undo(grid.version_code, grid.cost_center_code, batch)
      setGrid(updated)
      take((s) => s.slice(0, -1))
      if (updated.batch_id) give((s) => [...s, updated.batch_id!])
      setMessage(null)
    } catch (e) {
      if (e instanceof ConflictError) {
        // Someone else changed those cells: this step can't be taken any more.
        take((s) => s.slice(0, -1))
        setMessage({
          kind: 'info',
          text: `Can’t ${direction}: ${e.conflicts.length} cell(s) were changed by someone else since. The latest values are shown, nothing was changed.`,
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
    if (!grid) return
    try {
      await api.act(version, { action: 'submit', cost_centers: [grid.cost_center_code] })
      setMessage({ kind: 'ok', text: `${grid.cost_center_code} submitted for approval.` })
      load()
      loadUnits()
    } catch (e) {
      showError(e)
    }
  }

  async function chooseSpread(code: string, key: string) {
    if (!grid) return
    const chosen = spreadOf(key)
    try {
      const updated = await api.setSpread(version, grid.cost_center_code, code, chosen)
      setGrid(updated)
      const row = updated.rows.find((r) => r.kind === 'account' && r.code === code)
      setMessage({
        kind: 'ok',
        text: `${code}: a year total typed from now on ${row ? spreadText(row) : 'spreads as chosen'}. Months already planned keep their values.`,
      })
    } catch (e) {
      showError(e)
    }
  }

  /** Pressing a line's badge opens its spread menu; its notes or list marker, the line's notes or
   * item list (all rendered by the grid). Handled on mouse down, before the grid sees the press:
   * the grid would select the cell and redraw it, replacing the marker under the pointer so that
   * no click ever reaches it. */
  function onSheetPress(e: React.MouseEvent) {
    if (e.button !== 0) return
    const target = e.target as HTMLElement
    if (target.closest('[data-notes], [data-list], [data-spread]')) e.preventDefault()
    const notes = target.closest<HTMLElement>('[data-notes]')
    if (notes?.dataset.notes) {
      e.stopPropagation()
      setNotesFor({ code: notes.dataset.notes, thread: null })
      onFocused?.()
      return
    }
    const list = target.closest<HTMLElement>('[data-list]')
    if (list?.dataset.list && grid) {
      e.stopPropagation()
      const code = list.dataset.list
      api.lineList(grid.cost_center_code, code).then((found) => setListFor({ code, list: found }), showError)
      return
    }
    const badge = target.closest<HTMLElement>('[data-spread]')
    if (!badge?.dataset.spread) return
    e.stopPropagation()
    setSpreadMenu({ code: badge.dataset.spread, anchor: badge.getBoundingClientRect() })
  }

  /** Write a total from the gridnote or an item list as the line's year value, as if typed into
   * the year column: spread by the line's badge, audited and undoable. */
  async function insertYear(code: string, total: string) {
    if (!grid) return
    const rowIndex = grid.rows.findIndex((r) => r.kind === 'account' && r.code === code)
    const row = grid.rows[rowIndex]
    if (!row || !editable(row, COL_FY, layout)) throw new Error(`${code}’s year value can’t be changed here`)
    await save([{ rowIndex, prop: COL_FY, raw: total }])
  }

  async function addAccount(code: string) {
    if (!grid) return
    try {
      setGrid(await api.addAccount(version, grid.cost_center_code, code))
      setMessage(null)
    } catch (e) {
      showError(e)
    }
  }

  if (units === null) return message ? <div className="su-note error">{message.text}</div> : null
  if (units.length === 0) {
    return (
      <div className="su-card empty-state">
        <h3>No cost centers to plan</h3>
        <p className="su-hint">
          None of this round’s cost centers is yours to plan or read. Owners plan their cost centers; the controller
          can add you as a deputy on the Responsibilities page.
        </p>
      </div>
    )
  }

  const current = units.find((u) => u.code === unit)
  // The notes shown beside the sheet: those opened here, or the line Home opened it for.
  const notesOpen =
    notesFor ?? (focus && grid?.cost_center_code === unit ? { code: focus.account, thread: focus.thread } : null)
  const onSheet = new Set(grid?.rows.map((r) => r.code))
  const open = grid !== null && !locked
  const accountRows = grid?.rows.filter((r) => r.kind === 'account').length ?? 0
  const addable = accounts.filter((a) => !onSheet.has(a.code))
  const addAccountSelect = (
    <Select
      variant="action"
      label="Add an account to this sheet"
      display="Add account"
      value=""
      icon={<span className="pl-plus">+</span>}
      options={addable.map((a) => ({ value: a.code, label: `${a.code} ${a.name}` }))}
      onChange={(code) => void addAccount(code)}
      disabled={addable.length === 0}
    />
  )

  return (
    <div className="su pl" style={grid ? { maxWidth: sheetWidth } : undefined}>
      <PageHeader
        eyebrow={`Planning${grid ? ` · ${grid.version_name}` : ''}`}
        title={
          <Select
            variant="title"
            label="Cost center"
            value={unit}
            onChange={(code) => {
              onUnit(code)
              setMessage(null)
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
        }
        sub={
          <>
            {current?.owner ? `Owner ${current.owner}` : 'No owner'}
            {grid && ` · fiscal year ${grid.fiscal_year}`}
            {grid && forecast && closed > 0 && ` · forecast ${closed}+${12 - closed}`}
            {grid && quarters && ' · by quarter'}
            {saving && <span className="pg-saving"> · Saving…</span>}
          </>
        }
        actions={
          <>
            {grid && accountRows > 0 && <SheetScale />}
            {grid && <span className={`su-pill ${grid.status}`}>{STATUS_LABEL[grid.status]}</span>}
            {grid?.deadline && <span className="su-pill neutral">Due {grid.deadline}</span>}
            {open && accountRows > 0 && (
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
            {open && accountRows > 0 && addAccountSelect}
            {open && (
              <button type="button" className="su-btn primary" onClick={() => void submit()}>
                Submit for approval
              </button>
            )}
          </>
        }
      />

      {grid?.lock_reason && (
        <div className="su-note pl-note">
          <strong>Read only:</strong> {grid.lock_reason}.
        </div>
      )}
      {message && <div className={`su-note ${message.kind} pl-note`}>{message.text}</div>}

      {grid && accountRows === 0 ? (
        <div className="su-card empty-state">
          <h3>No accounts on this sheet yet</h3>
          <p className="su-hint">
            A sheet shows the accounts with actuals or plan values for this cost center.{' '}
            {open ? 'Add the accounts you want to plan.' : 'There is nothing to plan here.'}
          </p>
          {open && addAccountSelect}
        </div>
      ) : (
        grid && (
          <div className="pl-sheet" ref={trimPastes} onMouseDownCapture={onSheetPress}>
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
              // One copied value pasted over a selection fills every selected cell, as in Excel.
              useClipboard={{ rangeFill: true }}
              onBeforeedit={(e) => {
                e.preventDefault()
                void save([{ rowIndex: e.detail.rowIndex, prop: String(e.detail.prop), raw: e.detail.val }])
              }}
              onBeforerangeedit={(e) => {
                // Paste and fill-drag arrive as one block: saved together, all or nothing.
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
                  <i className="k-act" /> actuals (closed months)
                </span>
              )}
              <span className="pv-key">
                <i className="k-calc" /> calculated from the year total
              </span>
              <span className="pv-key">
                <i className="k-hidden" /> months not shown
              </span>
              <span className="pv-key">
                <i className="k-cmp" /> comparison
              </span>
              {open && (
                <span className="pl-help">
                  Type in a cell and press Enter.{' '}
                  {closed > 0
                    ? 'A year total in “Forecast” is the full year: the open months take what the actuals leave, spread as the line’s badge says.'
                    : `A year total in “Plan”${quarters ? ' or a quarter' : ''} spreads as the line’s badge says.`}{' '}
                  Click the badge to change it. Paste from Excel works; Ctrl+Z undoes. Empty is not 0.
                </span>
              )}
            </div>
          </div>
        )
      )}
      {notesOpen && grid && (
        <NotesPanel
          key={`${grid.cost_center_code}/${notesOpen.code}`}
          version={version}
          line={{ module: 'cost_center', cost_center: grid.cost_center_code, account: notesOpen.code }}
          title={`${notesOpen.code} ${grid.rows.find((r) => r.kind === 'account' && r.code === notesOpen.code)?.name ?? ''}`}
          canInsert={editable(grid.rows.find((r) => r.kind === 'account' && r.code === notesOpen.code), COL_FY, layout)}
          focusThread={notesOpen.thread}
          onInsertTotal={(total) => insertYear(notesOpen.code, total)}
          onChanged={load}
          onClose={() => {
            setNotesFor(null)
            onFocused?.()
          }}
        />
      )}
      {listFor && grid && (
        <ItemListEditor
          list={listFor.list}
          costCenter={grid.cost_center_code}
          account={listFor.code}
          title={`${grid.cost_center_code} ${grid.cost_center_name} · ${listFor.code} ${grid.rows.find((r) => r.kind === 'account' && r.code === listFor.code)?.name ?? ''}`}
          lineValue={sum(grid.rows.find((r) => r.kind === 'account' && r.code === listFor.code)?.plan.map((c) => c.value) ?? [])}
          canCreate={current?.can_write ?? false}
          onApply={
            editable(grid.rows.find((r) => r.kind === 'account' && r.code === listFor.code), COL_FY, layout)
              ? (total) => insertYear(listFor.code, total)
              : undefined
          }
          onSaved={load}
          onClose={() => setListFor(null)}
        />
      )}
      {spreadMenu && grid && (
        <Select
          label={`How ${spreadMenu.code} spreads its year total`}
          anchor={spreadMenu.anchor}
          onClose={() => setSpreadMenu(null)}
          value={spreadKey(grid.rows.find((r) => r.kind === 'account' && r.code === spreadMenu.code)?.chosen_spread ?? null)}
          options={spreadOptions(profiles)}
          onChange={(key) => void chooseSpread(spreadMenu.code, key)}
        />
      )}
    </div>
  )
}

/** Spreadsheets end copied text with a newline. RevoGrid would paste that empty last line as a
 * row and blank the cell below the block, so trailing empty rows are dropped before it applies a
 * paste. The event comes from the grid's clipboard component and bubbles to the sheet. */
function trimTrailingRows(e: Event) {
  const rows = (e as CustomEvent<{ parsed: string[][] }>).detail.parsed
  while (rows.length > 1 && rows[rows.length - 1].every((v) => v === '')) rows.pop()
}

function trimPastes(el: HTMLDivElement | null) {
  el?.addEventListener('beforepasteapply', trimTrailingRows) // the same listener is added once
}


/** A profile's shape as tiny bars, as in the setup page's preview. */
function profileBars(h: H, values: number[]) {
  const max = Math.max(...values, 0) || 1
  return h(
    'span',
    { class: 'pv-bars', 'aria-hidden': 'true' },
    values.map((v) => h('i', { style: { height: `${Math.max(1, (v / max) * 14)}px` } })),
  )
}

/** The line's item list total differs from its year value; a line without a year value yet
 * (blank or zero) has nothing to differ from. */
function listDiffers(row: Row): boolean {
  if (!row.item_list) return false
  const year = sum(row.plan.map((c) => c.value))
  return year !== null && toCents(year) !== 0n && year !== row.item_list.total
}

/** The account cell: code, name and the line's entry form, which opens the spread menu where
 * the line's spread can be chosen; a line spread by a profile shows its shape. Group rows show
 * their name only. */
function accountCell(h: H, row: Row | undefined, profiles: ProfileRef[], quarters: boolean) {
  if (!row) return ''
  if (row.kind === 'group') return h('span', { class: 'pl-acc-name' }, row.name)
  const form = row.entry_form
  // A line meant to follow a reference that has none spreads evenly and says so: "even". Its
  // tooltip tells why; a warning would contradict the badge.
  const label = badgeLabel(row)
  const classes = row.not_planned
    ? 'pv-form off'
    : row.capacity || row.forwarded?.side === 'credit'
    ? 'pv-form capacity'
    : row.forwarded || row.personnel
    ? 'pv-form charged'
    : `pv-form${form === 'monthly' ? ' monthly' : ''}${row.chosen_spread ? ' chosen' : ''}`
  const profile = row.spread === 'profile' ? profiles.find((p) => p.code === row.profile) : undefined
  const badge = !form
    ? ''
    : spreadChoosable(row)
      ? h(
          'button',
          { type: 'button', class: `${classes} pl-spread`, 'data-spread': row.code, 'aria-label': `How ${row.code} spreads: ${label}` },
          label,
        )
      : h('span', { class: classes }, label)
  return h('span', { class: 'pl-acc-in', title: form ? `${label}: ${basisText(row)}` : undefined }, [
    h('span', { class: 'pl-acc-name' }, [h('span', { class: 'pl-code' }, row.code), row.name]),
    // By quarter, a profile shows as its four quarters; it is still entered by month.
    h('span', { class: 'pl-acc-tags' }, [
      profile ? profileBars(h, quarters ? quarterly(profile.values.map(Number)) : profile.values.map(Number)) : '',
      badge,
      h('span', { class: 'pl-marks' }, [
        noteMark(h, row.code, row.notes),
        listMark(h, row.code, !!row.item_list, listDiffers(row)),
      ]),
    ]),
  ])
}
