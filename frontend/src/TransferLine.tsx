import type { Transfer } from './api'

// A transfer of a position (ADR 0021) as one entry of a list: who, from where to where, and when.

const DAY = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })

/** One transfer in a list: who, from where to where, and when. */
export default function TransferLine({ t }: { t: Transfer }) {
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
