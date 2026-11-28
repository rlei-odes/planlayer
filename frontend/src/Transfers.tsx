import { useState } from 'react'
import type { Transfer } from './api'
import { FIELD } from './limits'

// Transfers of positions (ADR 0021) as lists show them: one transfer as a line, the control to
// accept or decline one, and the card of transfers waiting for a cost center.

const DAY = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })

/** One transfer in a list: who, from where to where, and when. */
export function TransferLine({ t }: { t: Transfer }) {
  return (
    <span className="tr-line">
      <span className="tr-who">
        <span className="pl-code">{t.position_code}</span>
        <strong>{t.person_name ?? 'Vacancy'}</strong>
        <span className="ink-3">
          {' '}
          · {t.title} · {t.staff_type_name}, {t.staff_category_name}
          {t.contract_percent ? ` · ${Number(t.contract_percent)} %` : ''}
        </span>
      </span>
      <span className="tr-route">
        <span className="pl-code">{t.from_code}</span> {t.from_name} <span className="tr-arrow-txt">→</span>{' '}
        <span className="pl-code">{t.to_code}</span> {t.to_name}
        <span className="ink-3">
          {' · '}
          {t.starts ? `from ${DAY.format(new Date(t.starts))}` : 'whole year'} · proposed by {t.proposed_by}
        </span>
      </span>
      {t.note && <span className="tr-note">“{t.note}”</span>}
    </span>
  )
}

/** Accept, or decline with an optional reason, for the receiving side. */
export function TransferDecision({
  t,
  busy,
  onDecide,
}: {
  t: Transfer
  busy: boolean
  onDecide: (t: Transfer, accept: boolean, reason?: string) => void
}) {
  const [declining, setDeclining] = useState(false)
  const [reason, setReason] = useState('')
  if (declining)
    return (
      <span className="tr-actions">
        <input maxLength={FIELD.comment} className="su-input" placeholder="Reason (optional)" value={reason} autoFocus onChange={(e) => setReason(e.target.value)} />
        <button type="button" className="su-btn ghost sm" onClick={() => setDeclining(false)}>
          Cancel
        </button>
        <button
          type="button"
          className="su-btn sm danger"
          disabled={busy}
          onClick={() => {
            onDecide(t, false, reason)
            setDeclining(false)
            setReason('')
          }}
        >
          Decline
        </button>
      </span>
    )
  return (
    <span className="tr-actions">
      <button type="button" className="su-btn ghost sm" disabled={busy} onClick={() => setDeclining(true)}>
        Decline…
      </button>
      <button type="button" className="su-btn primary sm" disabled={busy} onClick={() => onDecide(t, true)}>
        Accept
      </button>
    </span>
  )
}

/** Transfers proposed to a cost center: the receiving side accepts or declines each. */
export function IncomingTransfers({
  transfers,
  busy,
  onDecide,
}: {
  transfers: Transfer[]
  busy: boolean
  onDecide: (t: Transfer, accept: boolean, reason?: string) => void
}) {
  return (
    <section className="su-card tr-incoming">
      <div className="fw-card-head">
        <h3>
          Transfers to this cost center <span className="ink-3">{transfers.length}</span>
        </h3>
        <span className="su-hint">The positions stay on the sending cost center until accepted.</span>
      </div>
      <ul className="tr-list">
        {transfers.map((t) => (
          <li key={t.transfer_id}>
            <TransferLine t={t} />
            {t.can_decide ? (
              <TransferDecision t={t} busy={busy} onDecide={onDecide} />
            ) : (
              <span className="su-hint">Waits for the cost center’s planners</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
