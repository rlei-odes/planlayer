import { useRef, useState, type KeyboardEvent } from 'react'
import { format, fromCents, parse, toCents } from './amounts'
import type { Capex, CapexItem } from './api'
import { monthLabel } from './capex'
import { LIMIT } from './limits'

// An investment's cash-out schedule (ADR 0028): when the money goes out, over the round's months
// (or quarters) and one period more, the next fiscal year. An entry above what is left of the
// investment is reduced to it, so the schedule never exceeds the amount.
//
// Keyboard: the first period has the focus; Enter takes the value and moves down, from the last
// period to Save; the up and down arrows move between periods; Escape closes without saving.

function periodLabel(iso: string, view: Capex): string {
  if (iso === view.next_year) return `FY ${view.round.fiscal_year + 1}`
  if (view.round.grain === 'quarter') return `Q${view.periods.indexOf(iso) + 1} ${monthLabel(iso).slice(-4)}`
  return monthLabel(iso)
}

export default function CapexCashOut({
  view,
  item,
  readOnly,
  onSave,
  onClose,
}: {
  view: Capex
  item: CapexItem
  readOnly: boolean
  onSave: (periods: { period: string; amount: string }[]) => Promise<void>
  onClose: () => void
}) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(item.cash_out.map((c) => [c.period, format(c.amount)])),
  )
  const [nextYear, setNextYear] = useState(item.cash_out.some((c) => c.period === view.next_year))
  const [reduced, setReduced] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const saveButton = useRef<HTMLButtonElement>(null)
  const amount = toCents(item.amount ?? '0')
  const periods = [...view.periods, ...(nextYear ? [view.next_year] : [])]

  const cents = (p: string) => {
    const v = parse(values[p] ?? '')
    return v ? toCents(v) : 0n
  }
  const scheduled = periods.reduce((s, p) => s + cents(p), 0n)
  const rest = amount - scheduled
  const restFor = (p: string) => amount - scheduled + cents(p)

  /** A finished entry: a number, at most what is left for its period. */
  function settle(p: string) {
    const raw = values[p] ?? ''
    const v = parse(raw)
    if (v === undefined) return setError(`${periodLabel(p, view)}: “${raw}” is not a number`)
    setError(null)
    if (v === null) return
    if (v.startsWith('-')) return setError('cash out is not negative')
    const left = restFor(p)
    const capped = toCents(v) > left ? left : toCents(v)
    if (toCents(v) > left) setReduced(p)
    setValues({ ...values, [p]: capped === 0n ? '' : format(fromCents(capped)) })
  }

  /** Enter and the arrows: to the period below or above; Enter from the last period to Save. */
  function move(e: KeyboardEvent<HTMLInputElement>, p: string) {
    const by = e.key === 'ArrowUp' ? -1 : e.key === 'ArrowDown' || e.key === 'Enter' ? 1 : 0
    if (!by) return
    e.preventDefault()
    if (e.key === 'Enter') settle(p)
    const inputs = [...(e.currentTarget.closest('table')?.querySelectorAll('input') ?? [])]
    const next = inputs[inputs.indexOf(e.currentTarget) + by]
    if (next) {
      next.focus()
      next.select()
    } else if (e.key === 'Enter') saveButton.current?.focus()
  }

  async function save() {
    const out = periods.flatMap((p) => (cents(p) > 0n ? [{ period: p, amount: fromCents(cents(p)) }] : []))
    setSaving(true)
    try {
      await onSave(out)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="il-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="il-dialog cx-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={`Cash out of ${item.name}`}
        // Read only, no field takes the focus: the dialog does, so Escape reaches it.
        tabIndex={-1}
        autoFocus={readOnly}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Escape') onClose()
        }}
      >
        <header className="il-head">
          <div>
            <div className="su-eyebrow">Cash out</div>
            <h3>
              <span className="pl-code">{item.item_id}</span> {item.name}
            </h3>
            <div className="il-sub ink-3">
              {item.cost_center_code} {item.cost_center_name} · start {monthLabel(item.start_month) || '–'} · amount{' '}
              {format(item.amount)}
            </div>
          </div>
          <button type="button" className="su-icon" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        {error && <div className="su-note error">{error}</div>}
        <div className="il-body">
          <table className="cx-co">
            <tbody>
              {periods.map((p, i) => (
                <tr key={p} className={p === view.next_year ? 'next' : undefined}>
                  <th scope="row">{periodLabel(p, view)}</th>
                  <td>
                    <input maxLength={LIMIT.number}
                      className="su-input num"
                      inputMode="decimal"
                      aria-label={`Cash out ${periodLabel(p, view)}`}
                      value={values[p] ?? ''}
                      disabled={readOnly}
                      autoFocus={i === 0 && !readOnly}
                      onChange={(e) => {
                        setReduced(null)
                        setValues({ ...values, [p]: e.target.value })
                      }}
                      onBlur={() => settle(p)}
                      onKeyDown={(e) => move(e, p)}
                    />
                  </td>
                  <td className="cx-co-act">
                    {reduced === p ? (
                      <span className="cx-co-reduced">Reduced to the rest</span>
                    ) : (
                      !readOnly &&
                      rest > 0n &&
                      cents(p) === 0n && (
                        <button
                          type="button"
                          className="nt-link"
                          onClick={() => setValues({ ...values, [p]: format(fromCents(restFor(p))) })}
                        >
                          Rest here
                        </button>
                      )
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!nextYear && !readOnly && (
            <button type="button" className="nt-link cx-co-add" onClick={() => setNextYear(true)}>
              + FY {view.round.fiscal_year + 1}
            </button>
          )}
        </div>
        <footer className="il-foot">
          <span className={`cx-co-rest ${rest === 0n ? 'ok' : 'open'}`}>
            {rest === 0n ? 'Fully scheduled' : `Unallocated ${format(fromCents(rest))}`}
          </span>
          <span className="su-hint">Scheduled {format(fromCents(scheduled))} of {format(item.amount)}.</span>
          <button type="button" className="su-btn ghost" onClick={onClose}>
            {readOnly ? 'Close' : 'Cancel'}
          </button>
          {!readOnly && (
            <button ref={saveButton} type="button" className="su-btn primary" disabled={saving} onClick={() => void save()}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          )}
        </footer>
      </div>
    </div>
  )
}
