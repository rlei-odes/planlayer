import { RevoGrid } from '@revolist/react-datagrid'
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

// Columns: account, entry form, one year total per comparison, plan year total, 12 months
// (ADR 0006, ADR 0007, T-25).
const COL_ACCOUNT = 'account'
const COL_FORM = 'form'
const COL_FY = 'fy'
const comparisonProp = (i: number) => `c${i}`
const monthProp = (m: number) => `m${m}`
const isMonth = (prop: string) => /^m\d+$/.test(prop)
const MONTH_LABEL = new Intl.DateTimeFormat('en', { month: 'short' })

const FORM_LABEL: Record<EntryForm, string> = {
  monthly: 'Monthly',
  annual: 'Year',
  annual_even: 'Year, even',
  annual_profile: 'Year, profile',
  annual_reference: 'Year, reference',
}

type GridRow = Record<string, string>

function editable(row: Row | undefined, prop: string): boolean {
  if (!row || row.kind !== 'account') return false
  if (prop === COL_FY) return row.total_editable
  return isMonth(prop) && row.months_editable
}

export default function PlanningPage({ version }: { version: string }) {
  const [units, setUnits] = useState<PlanningUnit[]>([])
  const [unit, setUnit] = useState<string>('')
  const [grid, setGrid] = useState<Grid | null>(null)
  const [accounts, setAccounts] = useState<Account[]>([])
  const [adding, setAdding] = useState<string>('')
  const [message, setMessage] = useState<{ kind: 'error' | 'info'; text: string } | null>(null)
  const [saving, setSaving] = useState(false)

  const showError = useCallback((e: unknown) => {
    setMessage({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
  }, [])

  const loadUnits = useCallback(() => {
    api.costCenters(version).then((us) => {
      setUnits(us)
      setUnit((current) => (us.some((u) => u.code === current) ? current : (us[0]?.code ?? '')))
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

  const columns = useMemo(() => {
    const periods = grid?.periods ?? []
    const cellProperties =
      (prop: string) =>
      ({ rowIndex }: { rowIndex: number }) => {
        const row = grid?.rows[rowIndex]
        const classes = [prop === COL_ACCOUNT || prop === COL_FORM ? 'label' : 'number']
        if (row?.kind === 'group') classes.push('group')
        else if (/^c\d+$/.test(prop)) classes.push('reference')
        else if ((prop === COL_FY || isMonth(prop)) && !editable(row, prop)) classes.push('readonly')
        if (prop === COL_FORM && row?.fallback) classes.push('fallback')
        const title =
          prop === COL_FORM && row?.basis
            ? `Year total spreads by ${row.fallback ? 'even (no reference values)' : row.basis}`
            : undefined
        return { class: classes.join(' '), title }
      }
    const column = (prop: string, name: string, size: number) => ({
      prop,
      name,
      size,
      readonly: ({ rowIndex }: { rowIndex: number }) => !editable(grid?.rows[rowIndex], prop),
      cellProperties: cellProperties(prop),
    })
    return [
      { ...column(COL_ACCOUNT, 'Account', 260), pin: 'colPinStart' as const },
      column(COL_FORM, 'Entry', 110),
      ...(grid?.comparisons ?? []).map((c, i) => column(comparisonProp(i), c.label, 124)),
      column(COL_FY, `Plan ${grid?.fiscal_year ?? ''}`, 124),
      ...periods.map((p, m) => column(monthProp(m), MONTH_LABEL.format(new Date(p)), 96)),
    ]
  }, [grid])

  const source = useMemo<GridRow[]>(
    () =>
      (grid?.rows ?? []).map((row) => {
        const out: GridRow = {
          [COL_ACCOUNT]: row.kind === 'group' ? row.name : `${row.code} ${row.name}`,
          [COL_FORM]: row.entry_form ? FORM_LABEL[row.entry_form] + (row.fallback ? ' ⚠' : '') : '',
          [COL_FY]: format(sum(row.plan.map((c) => c.value))),
        }
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
        refused.push(`${row.code}: "${String(raw)}" is not a number`)
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
      setMessage({ kind: 'info', text: `${grid.cost_center_code} submitted for approval.` })
      load()
      loadUnits()
    } catch (e) {
      showError(e)
    }
  }

  async function addAccount() {
    if (!grid || !adding) return
    try {
      setGrid(await api.addAccount(version, grid.cost_center_code, adding))
      setAdding('')
    } catch (e) {
      showError(e)
    }
  }

  const current = units.find((u) => u.code === unit)
  const onSheet = new Set(grid?.rows.map((r) => r.code))
  const open = grid !== null && grid.lock_reason === null
  return (
    <section className="planning">
      <div className="toolbar">
        <label>
          Cost center{' '}
          <select value={unit} onChange={(e) => setUnit(e.target.value)}>
            {units.map((u) => (
              <option key={u.code} value={u.code}>
                {u.code} {u.name} · {STATUS_LABEL[u.status]}
              </option>
            ))}
          </select>
        </label>
        {current?.owner && <span className="muted">Owner: {current.owner}</span>}
        {grid && <span className={`badge ${grid.status}`}>{STATUS_LABEL[grid.status]}</span>}
        {grid?.deadline && <span className="muted">Due {grid.deadline}</span>}
        {open && (
          <button type="button" onClick={() => void submit()}>
            Submit for approval
          </button>
        )}
        {open && (
          <span>
            <select value={adding} onChange={(e) => setAdding(e.target.value)} aria-label="Account to add">
              <option value="">Add account…</option>
              {accounts
                .filter((a) => !onSheet.has(a.code))
                .map((a) => (
                  <option key={a.code} value={a.code}>
                    {a.code} {a.name}
                  </option>
                ))}
            </select>{' '}
            <button type="button" disabled={!adding} onClick={() => void addAccount()}>
              Add
            </button>
          </span>
        )}
        {saving && <span className="muted">Saving…</span>}
      </div>
      {grid?.lock_reason && <div className="info">Read only: {grid.lock_reason}.</div>}
      {open && (
        <p className="muted">
          Type in a month, or type a year total in “Plan” to spread it as the entry form says. Paste from Excel works.
          Empty is not the same as 0. ⚠ marks lines without reference values, which spread evenly.
        </p>
      )}
      {message && <div className={message.kind}>{message.text}</div>}
      {units.length === 0 && <p>No cost center is enabled in this round.</p>}
      {grid && (
        <RevoGrid
          className="grid"
          columns={columns}
          source={source}
          range
          resize
          applyOnClose
          theme="compact"
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
      )}
    </section>
  )
}
