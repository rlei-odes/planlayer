import { RevoGrid } from '@revolist/react-datagrid'
import type { HyperFunc, VNode } from '@revolist/revogrid'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { format, parse, sum } from './amounts'
import {
  api,
  ConflictError,
  STATUS_LABEL,
  type Account,
  type Edit,
  type EntryForm,
  type Grid,
  type PlanningUnit,
  type Row,
} from './api'
import Select from './Select'

// The planning sheet of one cost center (T-25, ADR 0007, ADR 0010), in the look of the setup
// page's preview: typed-in cells look like inputs, months calculated from the year total are
// tinted, months of full-year-only lines are hatched, comparisons are muted, and every line carries
// its entry form (⚠ when its spread fell back to even). The server decides what is editable.

const COL_ACCOUNT = 'account'
const COL_FY = 'fy'
const comparisonProp = (i: number) => `c${i}`
const monthProp = (m: number) => `m${m}`
const isMonth = (prop: string) => /^m\d+$/.test(prop)
const MONTH_LABEL = new Intl.DateTimeFormat('en', { month: 'short', timeZone: 'UTC' })

const FORM_LABEL: Record<EntryForm, string> = {
  monthly: 'Monthly',
  annual: 'Year only',
  annual_even: 'Year · even',
  annual_profile: 'Year · season',
  annual_reference: 'Year · reference',
}

type GridRow = Record<string, string>
type Message = { kind: 'error' | 'info' | 'ok'; text: string }

function editable(row: Row | undefined, prop: string): boolean {
  if (!row || row.kind !== 'account') return false
  if (prop === COL_FY) return row.total_editable
  return isMonth(prop) && row.months_editable
}

/** The class that gives a cell its look; one per kind of cell. */
function cellKind(row: Row | undefined, prop: string, locked: boolean): string {
  if (!row) return ''
  if (row.kind === 'group') return 'pl-group'
  if (/^c\d+$/.test(prop)) return 'pl-cmp'
  if (prop === COL_FY) return editable(row, prop) ? 'pl-edit pl-total' : 'pl-plain pl-total'
  if (!isMonth(prop)) return ''
  if (!row.months_visible) return 'pl-hidden'
  if (editable(row, prop)) return 'pl-edit'
  return locked ? 'pl-plain' : 'pl-calc'
}

function basisText(row: Row): string {
  switch (row.entry_form) {
    case 'monthly':
      return 'Months are typed in; a year total spreads by last year’s actuals.'
    case 'annual':
      return 'Only the year total is entered; it is stored as 12 equal months.'
    case 'annual_even':
      return 'The year total spreads evenly over the months.'
    case 'annual_profile':
      return `The year total spreads by the profile ${row.basis ?? ''}.`
    case 'annual_reference':
      return row.fallback
        ? 'No reference values: the year total spreads evenly.'
        : `The year total spreads like ${row.basis ?? 'last year’s actuals'}.`
    default:
      return ''
  }
}

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

// Room for a vertical scrollbar inside the grid: classic scrollbars take ~15 px, overlay ones none.
const SCROLLBAR = 18

export default function PlanningPage({
  version,
  onWidth,
}: {
  version: string
  /** Reports the sheet's width, so the app widens its content area for wide sheets. */
  onWidth: (px: number | null) => void
}) {
  const [units, setUnits] = useState<PlanningUnit[] | null>(null)
  const [unit, setUnit] = useState<string>('')
  const [grid, setGrid] = useState<Grid | null>(null)
  const [accounts, setAccounts] = useState<Account[]>([])
  const [message, setMessage] = useState<Message | null>(null)
  const [saving, setSaving] = useState(false)

  const showError = useCallback((e: unknown) => setMessage({ kind: 'error', text: errorText(e) }), [])

  const loadUnits = useCallback(() => {
    api.costCenters(version).then((us) => {
      setUnits(us)
      // Start on the first cost center this user may edit, else the first one they may read.
      setUnit((current) =>
        us.some((u) => u.code === current) ? current : ((us.find((u) => u.can_write) ?? us[0])?.code ?? ''),
      )
    }, showError)
  }, [version, showError])

  useEffect(() => {
    loadUnits()
    api.accounts(version).then(setAccounts, showError)
  }, [version, loadUnits, showError])

  const load = useCallback(() => {
    if (!unit) return
    api.grid(version, unit).then(setGrid, showError)
  }, [version, unit, showError])

  useEffect(load, [load])

  const locked = grid?.lock_reason != null

  const columns = useMemo(() => {
    const periods = grid?.periods ?? []
    const nComp = grid?.comparisons.length ?? 0
    const cellProperties =
      (prop: string) =>
      ({ rowIndex }: { rowIndex: number }) => {
        const row = grid?.rows[rowIndex]
        const classes = [prop === COL_ACCOUNT ? 'pl-acc' : 'pl-num', cellKind(row, prop, locked)]
        if (prop === comparisonProp(nComp - 1)) classes.push('pl-cmp-last')
        return { class: classes.join(' ') }
      }
    const column = (prop: string, name: string, size: number, headerClass = '') => ({
      prop,
      name,
      size,
      columnProperties: () => ({ class: `pl-head ${headerClass}` }),
      readonly: ({ rowIndex }: { rowIndex: number }) => !editable(grid?.rows[rowIndex], prop),
      cellProperties: cellProperties(prop),
    })
    return [
      {
        ...column(COL_ACCOUNT, 'Account', 340, 'pl-head-acc'),
        pin: 'colPinStart' as const,
        cellTemplate: (h: HyperFunc<VNode>, { rowIndex }: { rowIndex: number }) =>
          accountCell(h as unknown as H, grid?.rows[rowIndex]),
      },
      ...(grid?.comparisons ?? []).map((c, i) => column(comparisonProp(i), c.label, 112, 'pl-head-cmp')),
      column(COL_FY, `Plan ${grid?.fiscal_year ?? ''}`, 116, 'pl-head-plan'),
      ...periods.map((p, m) => column(monthProp(m), MONTH_LABEL.format(new Date(p)), 92)),
    ]
  }, [grid, locked])

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
        row.plan.forEach((c, m) => (out[monthProp(m)] = row.months_visible ? format(c.value) : ''))
        return out
      }),
    [grid],
  )

  async function save(changes: { rowIndex: number; prop: string; raw: unknown }[]) {
    if (!grid) return
    const edits: Edit[] = []
    const refused: string[] = []
    for (const { rowIndex, prop, raw } of changes) {
      const row: Row | undefined = grid.rows[rowIndex]
      if (!row || !editable(row, prop)) continue
      const value = parse(String(raw ?? ''))
      if (value === undefined) {
        refused.push(`${row.code}: “${String(raw)}” is not a number`)
        continue
      }
      edits.push({
        account_code: row.code,
        month: prop === COL_FY ? null : Number(prop.slice(1)),
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
      <div className="pl-head">
        <div className="pl-title">
          <div className="su-eyebrow">Planning{grid ? ` · ${grid.version_name}` : ''}</div>
          <Select
            variant="title"
            label="Cost center"
            value={unit}
            onChange={(code) => {
              setUnit(code)
              setMessage(null)
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
            {current?.owner ? `Owner ${current.owner}` : 'No owner'}
            {grid && ` · fiscal year ${grid.fiscal_year}`}
            {saving && <span className="pl-saving"> · Saving…</span>}
          </div>
        </div>
        <div className="pl-actions">
          {grid && <span className={`su-pill ${grid.status}`}>{STATUS_LABEL[grid.status]}</span>}
          {grid?.deadline && <span className="su-pill neutral">Due {grid.deadline}</span>}
          {open && accountRows > 0 && addAccountSelect}
          {open && (
            <button type="button" className="su-btn primary" onClick={() => void submit()}>
              Submit for approval
            </button>
          )}
        </div>
      </div>

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
          <div className="pl-sheet">
            <RevoGrid
              className="pl-revo"
              columns={columns}
              source={source}
              range
              resize
              applyOnClose
              theme="default"
              rowSize={32}
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
                <i className="k-edit" /> typed in
              </span>
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
                  Type in a cell and press Enter. A year total in “Plan” spreads as the entry form says. Paste from
                  Excel works. Empty is not 0.
                </span>
              )}
            </div>
          </div>
        )
      )}
    </div>
  )
}

// RevoGrid's hyperscript function, called with plain props and mixed children.
type H = (tag: string, props: Record<string, unknown> | null, children?: unknown) => unknown

/** The account cell: code, name and the line's entry form; group rows show their name only. */
function accountCell(h: H, row: Row | undefined) {
  if (!row) return ''
  if (row.kind === 'group') return h('span', { class: 'pl-acc-name' }, row.name)
  const form = row.entry_form
  return h('span', { class: 'pl-acc-in', title: form ? `${FORM_LABEL[form]}: ${basisText(row)}` : undefined }, [
    h('span', { class: 'pl-acc-name' }, [h('span', { class: 'pl-code' }, row.code), row.name]),
    form
      ? h(
          'span',
          { class: `pv-form${form === 'monthly' ? ' monthly' : ''}${row.fallback ? ' fallback' : ''}` },
          FORM_LABEL[form] + (row.fallback ? ' ⚠' : ''),
        )
      : '',
  ])
}
