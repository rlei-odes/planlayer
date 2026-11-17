import { useMemo, useRef, useState } from 'react'
import { LIMIT } from './limits'

// Choose many members of a hierarchy at once: tick leaves, tick a node for everything below it,
// shift-click for a range, filter to narrow the list. Members already chosen elsewhere show
// ticked and can't be changed here.

export type TreeNode = { code: string; name: string; depth: number; is_group: boolean }

type Props = {
  nodes: TreeNode[]
  /** Already listed, or not allowed: shown ticked or greyed, never added. */
  taken: Set<string>
  /** Can't be chosen at all, e.g. the forwarding's source. */
  excluded?: Set<string>
  label: string
  onAdd: (codes: string[]) => void
  onClose: () => void
}

/** Each node's leaves: the leaves below it in list order, up to the next node at its depth. */
function leavesBelow(nodes: TreeNode[]): Map<number, number[]> {
  const out = new Map<number, number[]>()
  nodes.forEach((n, i) => {
    if (!n.is_group) return
    const leaves: number[] = []
    for (let j = i + 1; j < nodes.length && nodes[j].depth > n.depth; j++) if (!nodes[j].is_group) leaves.push(j)
    out.set(i, leaves)
  })
  return out
}

export default function TreePicker({ nodes, taken, excluded, label, onAdd, onClose }: Props) {
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [filter, setFilter] = useState('')
  const anchor = useRef<number | null>(null) // the last leaf clicked, for shift-click ranges
  const below = useMemo(() => leavesBelow(nodes), [nodes])

  // A filter keeps the matching leaves and the nodes above them.
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase()
    if (!q) return nodes.map((_, i) => i)
    const keep = new Set<number>()
    nodes.forEach((n, i) => {
      if (n.is_group || !`${n.code} ${n.name}`.toLowerCase().includes(q)) return
      keep.add(i)
      let depth = n.depth
      for (let j = i - 1; j >= 0 && depth > 0; j--)
        if (nodes[j].depth < depth) {
          keep.add(j)
          depth = nodes[j].depth
        }
    })
    return nodes.map((_, i) => i).filter((i) => keep.has(i))
  }, [nodes, filter])
  const visible = useMemo(() => new Set(shown), [shown])

  const free = (i: number) => !taken.has(nodes[i].code) && !excluded?.has(nodes[i].code)
  const leavesOf = (i: number) => (below.get(i) ?? []).filter((j) => visible.has(j) && free(j))

  function set(indexes: number[], on: boolean) {
    setChosen((prev) => {
      const next = new Set(prev)
      for (const j of indexes) {
        if (on) next.add(nodes[j].code)
        else next.delete(nodes[j].code)
      }
      return next
    })
  }

  function click(i: number, shift: boolean) {
    const n = nodes[i]
    if (n.is_group) {
      const leaves = leavesOf(i)
      set(leaves, !leaves.every((j) => chosen.has(nodes[j].code)))
      return
    }
    if (!free(i)) return
    const on = !chosen.has(n.code)
    if (shift && anchor.current !== null) {
      const [a, b] = [Math.min(anchor.current, i), Math.max(anchor.current, i)]
      set(
        shown.filter((j) => j >= a && j <= b && !nodes[j].is_group && free(j)),
        on,
      )
    } else set([i], on)
    anchor.current = i
  }

  const allShown = shown.filter((j) => !nodes[j].is_group && free(j))
  const ordered = nodes.filter((n) => chosen.has(n.code)).map((n) => n.code)

  return (
    <div className="tp" role="group" aria-label={label}>
      <div className="tp-bar">
        <input maxLength={LIMIT.name}
          className="su-input tp-filter"
          value={filter}
          autoFocus
          placeholder="Filter by code or name"
          aria-label="Filter"
          onChange={(e) => setFilter(e.target.value)}
          onKeyDown={(e) => e.key === 'Escape' && onClose()}
        />
        <button
          type="button"
          className="su-btn ghost sm"
          disabled={allShown.length === 0}
          onClick={() => set(allShown, !allShown.every((j) => chosen.has(nodes[j].code)))}
        >
          {filter.trim() ? 'All shown' : 'All'}
        </button>
        <span className="ink-3 tp-hint">Click a node for all below it · Shift-click for a range</span>
      </div>
      <ul className="tp-list">
        {shown.map((i) => {
          const n = nodes[i]
          const leaves = n.is_group ? leavesOf(i) : []
          const picked = n.is_group ? leaves.filter((j) => chosen.has(nodes[j].code)).length : 0
          const isTaken = !n.is_group && taken.has(n.code)
          const isExcluded = !n.is_group && !!excluded?.has(n.code)
          const checked = n.is_group ? leaves.length > 0 && picked === leaves.length : isTaken || chosen.has(n.code)
          return (
            <li key={n.code} className={`tp-row${n.is_group ? ' group' : ''}${isTaken || isExcluded ? ' off' : ''}`}>
              <label
                style={{ paddingLeft: 8 + n.depth * 18 }}
                onClick={(e) => {
                  e.preventDefault()
                  click(i, e.shiftKey)
                }}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={isTaken || isExcluded || (n.is_group && leaves.length === 0)}
                  ref={(el) => {
                    if (el) el.indeterminate = n.is_group && picked > 0 && picked < leaves.length
                  }}
                  readOnly
                />
                {!n.is_group && <span className="code">{n.code}</span>}
                <span>{n.name}</span>
                {n.is_group && picked > 0 && <span className="ink-3 tp-count">{picked}</span>}
                {isTaken && <span className="ink-3 tp-count">listed</span>}
              </label>
            </li>
          )
        })}
        {shown.length === 0 && <li className="tp-empty ink-3">Nothing matches “{filter}”.</li>}
      </ul>
      <div className="tp-foot">
        <button type="button" className="su-btn primary sm" disabled={ordered.length === 0} onClick={() => onAdd(ordered)}>
          Add {ordered.length || ''}
        </button>
        <button type="button" className="su-btn ghost sm" onClick={onClose}>
          Cancel
        </button>
      </div>
    </div>
  )
}
