import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'

// A month picker opened at a box, as the dropdown is (Select's anchor mode): a year with its
// twelve months, arrows between years, and Clear. Keyboard: arrows move, Enter picks, Escape
// closes, PageUp/PageDown change the year. Values are first-of-month ISO dates.

const NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function iso(year: number, month: number): string {
  return `${year}-${String(month + 1).padStart(2, '0')}-01`
}

export default function MonthPicker({
  value,
  fallbackYear,
  anchor,
  label,
  onChange,
  onClose,
}: {
  value: string | null
  /** The year shown when there is no value, e.g. the round's fiscal year. */
  fallbackYear: number
  anchor: DOMRect
  label: string
  /** A month, or null for Clear. */
  onChange: (value: string | null) => void
  onClose: () => void
}) {
  const [year, setYear] = useState(value ? Number(value.slice(0, 4)) : fallbackYear)
  const [active, setActive] = useState(value ? Number(value.slice(5, 7)) - 1 : 0)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    box.current?.focus()
    const away = (e: MouseEvent) => !box.current?.contains(e.target as Node) && onClose()
    document.addEventListener('mousedown', away, true)
    return () => document.removeEventListener('mousedown', away, true)
  }, [onClose])

  function onKey(e: KeyboardEvent) {
    const moves: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -3, ArrowDown: 3 }
    if (e.key in moves) {
      e.preventDefault()
      const next = active + moves[e.key]
      if (next < 0) {
        setYear(year - 1)
        setActive(next + 12)
      } else if (next > 11) {
        setYear(year + 1)
        setActive(next - 12)
      } else setActive(next)
    } else if (e.key === 'PageUp' || e.key === 'PageDown') {
      e.preventDefault()
      setYear(year + (e.key === 'PageUp' ? -1 : 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      onChange(iso(year, active))
    } else if (e.key === 'Escape' || e.key === 'Tab') {
      e.preventDefault()
      onClose()
    } else if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault()
      onChange(null)
    }
  }

  const width = 236
  const below = window.innerHeight - anchor.bottom > 220
  const style = {
    left: Math.max(8, Math.min(anchor.left, window.innerWidth - width - 8)),
    top: below ? anchor.bottom + 4 : undefined,
    bottom: below ? undefined : window.innerHeight - anchor.top + 4,
    width,
  }
  return createPortal(
    <div ref={box} className="mp" style={style} role="dialog" aria-label={label} tabIndex={-1} onKeyDown={onKey}>
      <div className="mp-head">
        <button type="button" className="su-icon" aria-label="Previous year" onClick={() => setYear(year - 1)}>
          ‹
        </button>
        <strong>{year}</strong>
        <button type="button" className="su-icon" aria-label="Next year" onClick={() => setYear(year + 1)}>
          ›
        </button>
      </div>
      <div className="mp-grid" role="grid">
        {NAMES.map((name, m) => {
          const v = iso(year, m)
          return (
            <button
              key={name}
              type="button"
              className={`mp-month${v === value ? ' on' : ''}${m === active ? ' active' : ''}`}
              aria-pressed={v === value}
              onClick={() => onChange(v)}
              onMouseEnter={() => setActive(m)}
            >
              {name}
            </button>
          )
        })}
      </div>
      <div className="mp-foot">
        <button type="button" className="nt-link" onClick={() => onChange(null)}>
          Clear
        </button>
      </div>
    </div>,
    document.body,
  )
}
