import { useEffect, useId, useState } from 'react'
import { api, STATUS_LABEL, type Position, type TransferIn, type TransferTarget, type WorkforceSheet } from './api'
import Select from './Select'
import { FIELD } from './limits'

// Transfer a position to another cost center (ADR 0021). The sending side proposes; the
// position and its values stay here until the receiving side accepts. Two kinds:
// - full: the position moves with its whole year;
// - during the year: it ends here the day before the chosen month and starts there on its first.

type Kind = TransferIn['kind']

const MONTH = new Intl.DateTimeFormat('en', { month: 'short', timeZone: 'UTC' })
const LONG = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })

/** The schematic of a transfer: the sending bar above, an arrow, the receiving bar below. Solid
 * is where the person counts, dashed where they don't; ``at`` is the share of the year before
 * the move (0: a full transfer). */
function Schematic({ at, large = false }: { at: number; large?: boolean }) {
  const clip = useId()
  const w = 240
  const x0 = 8
  const bar = w - 2 * x0
  const split = x0 + bar * at
  const arrowX = at ? split : w / 2
  const h = 26
  const top = 10
  const bottom = 84
  const shape = (y: number) => <rect x={x0 - 0.5} y={y - 0.5} width={bar + 1} height={h + 1} rx={7.5} />
  return (
    <svg
      className={`tr-schema${large ? ' large' : ''}`}
      viewBox={`0 0 ${w} 120`}
      role="img"
      aria-label={at ? 'Here until the move, there from it' : 'The whole year moves there'}
    >
      <defs>
        <clipPath id={`${clip}-t`}>{shape(top)}</clipPath>
        <clipPath id={`${clip}-b`}>{shape(bottom)}</clipPath>
      </defs>
      {/* Sending cost center: solid up to the move, then dashed. */}
      <g className="tr-from">
        <rect className="tr-outline" x={x0 + 0.75} y={top + 0.75} width={bar - 1.5} height={h - 1.5} rx={6.5} />
        {at > 0 && <rect clipPath={`url(#${clip}-t)`} className="tr-solid" x={x0 - 1} y={top - 1} width={split - x0 + 1} height={h + 2} />}
      </g>
      {/* The move. */}
      <g className="tr-arrow">
        {at > 0 && <line className="tr-tick" x1={arrowX} x2={arrowX} y1={top - 4} y2={bottom + h + 4} />}
        <line x1={arrowX} x2={arrowX} y1={top + h + 8} y2={bottom - 12} />
        <path d={`M ${arrowX - 7} ${bottom - 17} L ${arrowX} ${bottom - 8} L ${arrowX + 7} ${bottom - 17}`} />
      </g>
      {/* Receiving cost center: dashed up to the move, then solid. */}
      <g className="tr-to">
        <rect className="tr-outline" x={x0 + 0.75} y={bottom + 0.75} width={bar - 1.5} height={h - 1.5} rx={6.5} />
        <rect clipPath={`url(#${clip}-b)`} className="tr-solid" x={split} y={bottom - 1} width={x0 + bar - split + 1} height={h + 2} />
      </g>
    </svg>
  )
}

export default function TransferDialog({
  version,
  sheet,
  position,
  onClose,
  onProposed,
}: {
  version: string
  sheet: WorkforceSheet
  position: Position
  onClose: () => void
  onProposed: (updated: WorkforceSheet, to: string) => void
}) {
  const [targets, setTargets] = useState<TransferTarget[] | null>(null)
  const [to, setTo] = useState('')
  const [kind, setKind] = useState<Kind>('full')
  const [starts, setStarts] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.transferTargets(version, sheet.cost_center_code).then(setTargets, (e: unknown) =>
      setError(e instanceof Error ? e.message : String(e)),
    )
  }, [version, sheet.cost_center_code])

  // A move during the year starts on the first of a month: not the year's first (that is a full
  // transfer), not a forecast's closed months, after the person entered and before they leave.
  const periods = sheet.periods
  const allowed = periods.map((p, i) => {
    if (i === 0) return 'From January, use “Whole position”'
    if (i < sheet.closed_months) return 'Closed in this forecast'
    if (position.entry_date && position.entry_date >= p) return 'Before the person enters'
    if (position.leave_date && position.leave_date < p) return 'After the person leaves'
    return null
  })
  const index = starts ? periods.indexOf(starts) : -1
  const at = kind === 'during_year' ? (index > 0 ? index / 12 : 2 / 3) : 0
  const target = targets?.find((t) => t.code === to)
  const ready = !!to && (kind === 'full' || !!starts)
  const lastDay = starts ? new Date(new Date(starts).getTime() - 86_400_000) : null
  const who = position.person_name ?? 'the vacancy'

  async function submit() {
    if (!ready) return
    setBusy(true)
    setError(null)
    try {
      const updated = await api.proposeTransfer(version, sheet.cost_center_code, position.position_id, {
        to_cost_center: to,
        kind,
        starts: kind === 'during_year' ? starts : null,
        note: note.trim() || null,
      })
      onProposed(updated, to)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  return (
    <div className="il-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="il-dialog tr-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tr-title"
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Escape') onClose()
        }}
      >
        <header className="il-head">
          <div>
            <div className="su-eyebrow">Transfer to another cost center</div>
            <h3 id="tr-title">
              <span className="pl-code">{position.code}</span> {position.title}
              {position.person_name && <span className="ink-3"> · {position.person_name}</span>}
            </h3>
            <div className="ink-3 il-sub">
              From {sheet.cost_center_code} {sheet.cost_center_name}. Nothing moves until the receiving cost center accepts.
            </div>
          </div>
          <button type="button" className="su-icon" aria-label="Close" title="Close (Esc)" onClick={onClose}>
            ×
          </button>
        </header>

        {error && <div className="su-note error">{error}</div>}

        <div className="il-body tr-body">
          <div className="su-field tr-target">
            To cost center
            <Select
              label="To cost center"
              value={to}
              onChange={setTo}
              searchable
              placeholder={targets ? 'Choose the receiving cost center' : 'Loading…'}
              options={(targets ?? []).map((t) => ({
                value: t.code,
                label: `${t.code} ${t.name}`,
                hint: STATUS_LABEL[t.status],
                dot: t.status,
              }))}
            />
            {target && !target.editable && (
              <span className="tr-warn">
                {target.code} is {STATUS_LABEL[target.status].toLowerCase()}: it can accept once it is open for planning
                again.
              </span>
            )}
          </div>

          <div className="tr-kinds" role="radiogroup" aria-label="Kind of transfer">
            {(
              [
                ['full', 'Whole position', 'The position moves with its whole year. It leaves this cost center’s plan entirely.'],
                [
                  'during_year',
                  'During the year',
                  'The position ends here at the end of a month and continues there from the next. Both keep their part of the year.',
                ],
              ] as const
            ).map(([value, title, text]) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={kind === value}
                className="tr-kind"
                onClick={() => setKind(value)}
              >
                <span className="tr-check" aria-hidden="true" />
                <Schematic at={value === 'full' ? 0 : kind === value ? at : 2 / 3} large />
                <strong>{title}</strong>
                <span>{text}</span>
              </button>
            ))}
          </div>

          {kind === 'during_year' && (
            <div className="su-field tr-months">
              Moves on the first of
              <div className="tr-month-grid" role="radiogroup" aria-label="Month of the transfer">
                {periods.map((p, i) => (
                  <button
                    key={p}
                    type="button"
                    role="radio"
                    aria-checked={starts === p}
                    disabled={!!allowed[i]}
                    title={allowed[i] ?? `From 1 ${MONTH.format(new Date(p))}`}
                    className="tr-month"
                    onClick={() => setStarts(p)}
                  >
                    {MONTH.format(new Date(p))}
                  </button>
                ))}
              </div>
              <span className="su-hint">
                {starts && lastDay
                  ? `Last day here ${LONG.format(lastDay)}; first day at ${to || 'the receiving cost center'} ${LONG.format(new Date(starts))}.`
                  : 'Pick the month the person starts on the receiving cost center.'}
              </span>
            </div>
          )}

          <label className="su-field">
            <span>
              Note for the receiving cost center <span className="ink-3">(optional)</span>
            </span>
            <textarea
              className="su-input"
              rows={2}
              maxLength={FIELD.text}
              value={note}
              placeholder="e.g. moves with the new assembly line"
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
        </div>

        <footer className="tr-foot">
          <span className="su-hint">
            {ready
              ? `${to} gets a task to accept ${who}${kind === 'during_year' && starts ? ` from ${MONTH.format(new Date(starts))}` : ''}. You can withdraw it until then.`
              : 'Choose where the position goes, and how.'}
          </span>
          <button type="button" className="su-btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="su-btn primary" disabled={!ready || busy} onClick={() => void submit()}>
            {busy ? 'Proposing…' : 'Propose transfer'}
          </button>
        </footer>
      </div>
    </div>
  )
}
