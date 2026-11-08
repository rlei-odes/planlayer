import '@glideapps/glide-data-grid/dist/index.css'
import {
  DataEditor,
  GridCellKind,
  type EditableGridCell,
  type GridCell,
  type GridColumn,
  type Item,
} from '@glideapps/glide-data-grid'
import { useCallback, useMemo, useState } from 'react'
import { COL_LABEL, COLUMN_TITLES, type PlanModel } from './model'

export default function GlideView({ model, onErrors }: { model: PlanModel; onErrors: (e: string[]) => void }) {
  const [version, setVersion] = useState(0)
  const refresh = () => setVersion(model.version)

  const columns: GridColumn[] = useMemo(
    () => COLUMN_TITLES.map((title, i) => ({ title, id: String(i), width: i === COL_LABEL ? 280 : 104 })),
    [],
  )

  const getCellContent = useCallback(
    ([col, rowIndex]: Item): GridCell => {
      const row = model.rows[rowIndex]
      const text = model.text(row, col)
      const editable = model.editable(row, col)
      const bold = row.kind === 'group'
      return {
        kind: GridCellKind.Text,
        // A percentage line edits its rate, not the computed amount.
        data: row.kind === 'percent' && col !== COL_LABEL ? String(model.rates.get(row.key) ?? '') : text,
        displayData: text,
        allowOverlay: editable,
        readonly: !editable,
        contentAlign: col === COL_LABEL ? 'left' : 'right',
        themeOverride: row.locked
          ? { textDark: '#888', bgCell: '#f1f1f1' }
          : bold
            ? { baseFontStyle: '600 13px', bgCell: '#eef3fb' }
            : !editable
              ? { bgCell: '#fafafa' }
              : undefined,
      }
    },
    // version: a new callback identity makes Glide redraw after every change (edit, paste, fill).
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [model, version],
  )

  const onCellsEdited = useCallback(
    (items: readonly { location: Item; value: EditableGridCell }[]) => {
      const errors: string[] = []
      for (const { location: [col, rowIndex], value } of items) {
        if (value.kind !== GridCellKind.Text) continue
        const err = model.set(model.rows[rowIndex], col, value.data)
        if (err) errors.push(err)
      }
      onErrors(errors)
      refresh()
      return true
    },
    [model, onErrors],
  )

  return (
    <DataEditor
      width="100%"
      height="calc(100vh - 150px)"
      columns={columns}
      rows={model.rows.length}
      getCellContent={getCellContent}
      onCellsEdited={onCellsEdited}
      onPaste={([col, row], values) => {
        onErrors(model.paste(row, col, values))
        refresh()
        return false
      }}
      getCellsForSelection
      fillHandle
      freezeColumns={1}
      rowMarkers="none"
      smoothScrollX
      smoothScrollY
    />
  )
}
