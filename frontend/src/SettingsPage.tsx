import { useEffect, useState } from 'react'
import { api, type Signs } from './api'
import { example, type NumberFormat } from './amounts'
import { NumberFormatFields } from './NumberFormat'
import PageHeader from './PageHeader'

// Installation settings (E-12), for the admin: the sign convention of published amounts
// (ADR 0019), and the number format of users without one of their own. Plan values stay natural-positive in Planlayer and in `amount`; `amount_signed`
// follows the convention chosen here, by each account's role.

type Convention = Signs['convention']
type Note = { kind: 'ok' | 'error'; text: string }

const CONVENTIONS: { value: Convention; label: string; hint: string }[] = [
  { value: 'ledger', label: 'Ledger', hint: 'debit positive, as the ERP books' },
  { value: 'result', label: 'Result', hint: 'profit positive, as P&L reports read' },
]
// The sign per role, as in planlayer.domain.signs.
const SIGN: Record<Convention, Record<'cost' | 'revenue' | 'credit', 1 | -1>> = {
  ledger: { cost: 1, revenue: -1, credit: -1 },
  result: { cost: -1, revenue: 1, credit: 1 },
}
const EXAMPLE = 1000

export default function SettingsPage() {
  const [signs, setSigns] = useState<Signs | null>(null)
  const [choice, setChoice] = useState<Convention | null>(null)
  const [note, setNote] = useState<Note | null>(null)

  useEffect(() => {
    api.signs().then(
      (s) => {
        setSigns(s)
        setChoice(s.convention)
      },
      (e: unknown) => setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) }),
    )
  }, [])

  if (!signs || !choice) return note ? <div className="su-note error">{note.text}</div> : null

  async function save() {
    if (!choice) return
    try {
      setSigns(await api.setSigns(choice))
      setNote({ kind: 'ok', text: 'Saved. Published rounds take it on their next publish run.' })
    } catch (e) {
      setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    }
  }

  const fmt = (n: number) => n.toLocaleString('en-US', { minimumFractionDigits: 2 })
  const roles = [
    { role: 'cost' as const, label: 'Cost', count: signs.cost_accounts },
    { role: 'revenue' as const, label: 'Revenue', count: signs.revenue_accounts },
    { role: 'credit' as const, label: 'Credit', count: signs.credit_accounts },
  ]

  return (
    <div className="su st-settings">
      <PageHeader title="Settings" />

      <NumberDefault />

      <div className="su-card">
        <h3>Published signs</h3>
        <div className="su-row fw-gap">
          <div className="seg" role="group" aria-label="Sign convention">
            {CONVENTIONS.map((c) => (
              <button key={c.value} type="button" aria-pressed={choice === c.value} title={c.hint} onClick={() => setChoice(c.value)}>
                {c.label}
              </button>
            ))}
          </div>
          <span className="ink-3">{CONVENTIONS.find((c) => c.value === choice)?.hint}</span>
        </div>

        <div className="tbl-card fw-gap set-roles">
          <table className="tbl">
            <thead>
              <tr>
                <th>Role</th>
                <th className="right">Accounts</th>
                <th className="right" title="As stored and published in amount">
                  amount
                </th>
                <th className="right">amount_signed</th>
              </tr>
            </thead>
            <tbody>
              {roles.map((r) => (
                <tr key={r.role}>
                  <td>{r.label}</td>
                  <td className="num">{r.count}</td>
                  <td className="num ink-3">{fmt(EXAMPLE)}</td>
                  <td className="num">{fmt(EXAMPLE * SIGN[choice][r.role])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {signs.credits.length > 0 && (
          <details className="fw-gap">
            <summary className="ink-2">Credit accounts ({signs.credits.length})</summary>
            <p className="set-list">
              {signs.credits.map((a) => (
                <span key={a.code} className="su-chip">
                  <span className="code">{a.code}</span>
                  {a.name}
                </span>
              ))}
            </p>
          </details>
        )}
        {signs.untyped.length > 0 && (
          <div className="su-note warn fw-gap">
            {signs.untyped.length} account{signs.untyped.length === 1 ? '' : 's'} without a type, signed as costs:{' '}
            {signs.untyped
              .slice(0, 12)
              .map((a) => a.code)
              .join(', ')}
            {signs.untyped.length > 12 ? ', …' : ''}
          </div>
        )}
        {note && <div className={`su-note ${note.kind} fw-gap`}>{note.text}</div>}
        <div className="fw-buttons">
          <button type="button" className="su-btn primary" disabled={choice === signs.convention} onClick={() => void save()}>
            Save
          </button>
        </div>
      </div>
    </div>
  )
}

/** The installation's number format; a user's own choice in the top bar overrides it. */
function NumberDefault() {
  const [saved, setSaved] = useState<NumberFormat | null>(null)
  const [draft, setDraft] = useState<NumberFormat | null>(null)
  const [note, setNote] = useState<Note | null>(null)

  useEffect(() => {
    api.me().then(
      (m) => {
        setSaved(m.number_default)
        setDraft(m.number_default)
      },
      (e: unknown) => setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) }),
    )
  }, [])

  if (!saved || !draft) return note ? <div className="su-note error">{note.text}</div> : null
  const changed = JSON.stringify(saved) !== JSON.stringify(draft)

  async function save() {
    if (!draft) return
    try {
      setSaved(await api.setDefaultNumberFormat(draft))
      setNote({ kind: 'ok', text: 'Saved. Users without a choice of their own see it on their next page load.' })
    } catch (e) {
      setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    }
  }

  return (
    <div className="su-card">
      <h3>Number format</h3>
      <p className="su-hint">The default for every user; each user may choose their own in the top bar. Display only.</p>
      <NumberFormatFields value={draft} onChange={setDraft} />
      <p className="nf-example">{example(draft)}</p>
      {note && <div className={`su-note ${note.kind} fw-gap`}>{note.text}</div>}
      <div className="fw-buttons">
        <button type="button" className="su-btn primary" disabled={!changed} onClick={() => void save()}>
          Save
        </button>
      </div>
    </div>
  )
}
