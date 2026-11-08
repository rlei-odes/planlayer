import { RevoGrid } from '@revolist/react-datagrid'
import { useMemo, useState } from 'react'
import { COL_LABEL, COLUMN_TITLES, type PlanModel } from './model'

type Source = Record<string, string | number>

export default function RevoView({ model, onErrors }: { model: PlanModel; onErrors: (e: string[]) => void }) {
  const [version, setVersion] = useState(0)

  const columns = useMemo(
    () =>
      COLUMN_TITLES.map((name, i) => ({
        prop: `c${i}`,
        name,
        size: i === COL_LABEL ? 280 : 104,
        pin: i === COL_LABEL ? ('colPinStart' as const) : undefined,
        readonly: ({ rowIndex }: { rowIndex: number }) => !model.editable(model.rows[rowIndex], i),
        cellProperties: ({ rowIndex }: { rowIndex: number }) => {
          const row = model.rows[rowIndex]
          const cls = [i === COL_LABEL ? 'label' : 'num']
          if (row.locked) cls.push('locked')
          else if (row.kind === 'group') cls.push('group')
          else if (!model.editable(row, i)) cls.push('ro')
          return { class: cls.join(' ') }
        },
      })),
    [model],
  )

  // Rebuilt from the model after every change; the grid only displays.
  const source = useMemo<Source[]>(
    () =>
      model.rows.map((row, r) => {
        const out: Source = { _r: r }
        COLUMN_TITLES.forEach((_, c) => (out[`c${c}`] = model.text(row, c)))
        return out
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [model, version],
  )

  const colOf = (prop: string | number) => Number(String(prop).slice(1))

  return (
    <RevoGrid
      style={{ height: 'calc(100vh - 150px)' }}
      columns={columns}
      source={source}
      range
      resize
      // Save the typed value when the editor closes (click away, Tab), not only on Enter.
      applyOnClose
      theme="compact"
      onBeforeedit={(e) => {
        e.preventDefault()
        const { rowIndex, prop, val } = e.detail
        const err = model.set(model.rows[rowIndex], colOf(prop), String(val ?? ''))
        onErrors(err ? [err] : [])
        setVersion(model.version)
      }}
      onBeforerangeedit={(e) => {
        // Paste and fill-drag both arrive here as a block of changed rows.
        e.preventDefault()
        const errors: string[] = []
        for (const [r, changes] of Object.entries(e.detail.data)) {
          for (const [prop, val] of Object.entries(changes as Source)) {
            if (prop === '_r') continue
            const err = model.set(model.rows[Number(r)], colOf(prop), String(val ?? ''))
            if (err) errors.push(err)
          }
        }
        onErrors(errors)
        setVersion(model.version)
      }}
    />
  )
}
