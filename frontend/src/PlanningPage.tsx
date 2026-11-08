import { RevoGrid } from '@revolist/react-datagrid'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { format, parse, sum } from './amounts'
import { api, ConflictError, type Edit, type Grid, type PlanningUnit, type Row, type Version } from './api'

// Columns: account, prior-year total, plan year total, 12 months (ADR 0006, T-25).
const COL_ACCOUNT = 'account'
const COL_PY = 'py'
const COL_FY = 'fy'
const monthProp = (m: number) => `m${m}`
const MONTH_LABEL = new Intl.DateTimeFormat('en', { month: 'short' })

type GridRow = Record<string, string>

export default function PlanningPage() {
  const [versions, setVersions] = useState<Version[]>([])
  const [version, setVersion] = useState<string>('')
  const [units, setUnits] = useState<PlanningUnit[]>([])
  const [unit, setUnit] = useState<string>('')
  const [grid, setGrid] = useState<Grid | null>(null)
  const [message, setMessage] = useState<{ kind: 'error' | 'info'; text: string } | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.versions().then((vs) => {
      setVersions(vs)
      if (vs.length > 0) setVersion(vs[0].code)
    }, showError)
  }, [])

  useEffect(() => {
    if (!version) return
    api.costCenters(version).then((us) => {
      setUnits(us)
      setUnit((current) => (us.some((u) => u.code === current) ? current : (us[0]?.code ?? '')))
    }, showError)
  }, [version])

  const load = useCallback(() => {
    if (!version || !unit) return
    api.grid(version, unit).then(setGrid, showError)
  }, [version, unit])

  useEffect(load, [load])

  function showError(e: unknown) {
    setMessage({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
  }

  const columns = useMemo(() => {
    const periods = grid?.periods ?? []
    const readonly = (prop: string) => ({ rowIndex }: { rowIndex: number }) =>
      prop === COL_ACCOUNT || prop === COL_PY || grid?.rows[rowIndex]?.kind !== 'account'
    const cellProperties = (prop: string) => ({ rowIndex }: { rowIndex: number }) => {
      const row = grid?.rows[rowIndex]
      const classes = [prop === COL_ACCOUNT ? 'label' : 'number']
      if (row?.kind === 'group') classes.push('group')
      else if (prop === COL_PY) classes.push('reference')
      return { class: classes.join(' ') }
    }
    const column = (prop: string, name: string, size: number) => ({
      prop, name, size, readonly: readonly(prop), cellProperties: cellProperties(prop),
    })
    return [
      { ...column(COL_ACCOUNT, 'Account', 280), pin: 'colPinStart' as const },
      column(COL_PY, `Actual ${grid ? grid.fiscal_year - 1 : ''}`, 110),
      column(COL_FY, `Plan ${grid?.fiscal_year ?? ''}`, 110),
      ...periods.map((p, m) => column(monthProp(m), MONTH_LABEL.format(new Date(p)), 96)),
    ]
  }, [grid])

  const source = useMemo<GridRow[]>(
    () =>
      (grid?.rows ?? []).map((row) => {
        const out: GridRow = {
          [COL_ACCOUNT]: row.kind === 'group' ? row.name : `${row.code} ${row.name}`,
          [COL_PY]: format(sum(row.prior_year)),
          [COL_FY]: format(sum(row.plan.map((c) => c.value))),
        }
        row.plan.forEach((c, m) => (out[monthProp(m)] = format(c.value)))
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
      if (!row || row.kind !== 'account' || (prop !== COL_FY && !prop.startsWith('m'))) continue
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
      setGrid(await api.edit(grid.version_code, grid.cost_center_code, edits))
    } catch (e) {
      if (e instanceof ConflictError) {
        setMessage({ kind: 'info', text: `${e.message}; the latest values are shown, nothing was saved.` })
        load()
      } else showError(e)
    } finally {
      setSaving(false)
    }
  }

  const current = units.find((u) => u.code === unit)
  return (
    <section className="planning">
      <div className="toolbar">
        <label>
          Version{' '}
          <select value={version} onChange={(e) => setVersion(e.target.value)}>
            {versions.map((v) => (
              <option key={v.code} value={v.code}>
                {v.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Cost center{' '}
          <select value={unit} onChange={(e) => setUnit(e.target.value)}>
            {units.map((u) => (
              <option key={u.code} value={u.code}>
                {u.code} {u.name}
              </option>
            ))}
          </select>
        </label>
        {current?.owner && <span className="muted">Owner: {current.owner}</span>}
        {saving && <span className="muted">Saving…</span>}
      </div>
      <p className="muted">
        Type in a month, or type a year total in “Plan” to spread it by last year&apos;s actuals. Paste from Excel works.
        Empty is not the same as 0.
      </p>
      {message && <div className={message.kind}>{message.text}</div>}
      {versions.length === 0 && <p>No plan version yet. Create one with `planlayer create-version`.</p>}
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
