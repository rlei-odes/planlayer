import { useCallback, useEffect, useState } from 'react'
import { api, type Signs } from './api'
import type { ConnectionOut } from './schema'
import { detail, example, type NumberFormat } from './amounts'
import { NumberFormatFields } from './NumberFormat'
import LoginSettingsCard from './LoginSettings'
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

type Section = 'login' | 'connections' | 'numbers' | 'signs'
const SECTIONS: { value: Section; label: string; sub: string }[] = [
  { value: 'login', label: 'Login', sub: 'How users log in: local accounts, OpenID Connect or LDAP.' },
  { value: 'connections', label: 'Connections', sub: 'The databases Planlayer connects to. It bundles none of its own.' },
  { value: 'numbers', label: 'Number format', sub: 'How amounts are shown and typed, unless a user chooses their own.' },
  { value: 'signs', label: 'Published signs', sub: 'The sign of amounts in the published tables.' },
]

export default function SettingsPage() {
  // Back from an OpenID Connect test, the Login tab shows its result.
  const [section, setSection] = useState<Section>('login')
  const current = SECTIONS.find((s) => s.value === section) ?? SECTIONS[0]
  return (
    <div className="su st-settings">
      <PageHeader
        eyebrow="Settings"
        title={current.label}
        sub={current.sub}
        tabs={
          <div className="seg" role="group" aria-label="Section">
            {SECTIONS.map((s) => (
              <button key={s.value} type="button" aria-pressed={section === s.value} onClick={() => setSection(s.value)}>
                {s.label}
              </button>
            ))}
          </div>
        }
      />
      {section === 'login' && <LoginSettingsCard />}
      {section === 'connections' && <Connections />}
      {section === 'numbers' && <NumberDefault />}
      {section === 'signs' && <PublishedSigns />}
    </div>
  )
}

/** The sign convention of published amounts (ADR 0019). */
function PublishedSigns() {
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

  const fmt = (n: number) => detail(n.toFixed(2))
  const roles = [
    { role: 'cost' as const, label: 'Cost', count: signs.cost_accounts },
    { role: 'revenue' as const, label: 'Revenue', count: signs.revenue_accounts },
    { role: 'credit' as const, label: 'Credit', count: signs.credit_accounts },
  ]

  return (
    <div className="su-card">
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

const ROLE: Record<ConnectionOut['role'], { label: string; env: string }> = {
  app: { label: 'App store', env: 'PLANLAYER_APP_DATABASE_URL' },
  source: { label: 'Source', env: 'PLANLAYER_SOURCE_DATABASE_URL' },
  output: { label: 'Output', env: 'PLANLAYER_OUTPUT_DATABASE_URL' },
}

/** The app store, the source and the output database, and whether each answers. */
function Connections() {
  const [rows, setRows] = useState<ConnectionOut[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const fetchRows = useCallback(() => {
    api.connections().then(setRows, (e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [])
  useEffect(fetchRows, [fetchRows])
  const load = () => {
    setRows(null)
    fetchRows()
  }
  if (error) return <div className="su-note error">{error}</div>
  return (
    <div className="su-card">
      <p className="su-hint">Set by environment variables at the server. An unset source or output URL uses the app store's database.</p>
      <div className="tbl-card fw-gap">
        <div className="tbl-scroll">
          <table className="tbl">
            <thead>
              <tr>
                <th>Connection</th>
                <th>Host</th>
                <th>Database</th>
                <th>User</th>
                <th>Schema</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows === null && (
                <tr>
                  <td colSpan={6} className="ink-3">
                    Connecting…
                  </td>
                </tr>
              )}
              {rows?.map((c) => (
                <tr key={c.role}>
                  <td title={ROLE[c.role].env}>
                    <strong>{ROLE[c.role].label}</strong>
                    {c.shared && <span className="ink-3"> · the app store's</span>}
                  </td>
                  <td>
                    <code>
                      {c.host ?? 'local socket'}
                      {c.port ? `:${c.port}` : ''}
                    </code>
                  </td>
                  <td>
                    <code>{c.database}</code>
                  </td>
                  <td>
                    <code>{c.user}</code>
                  </td>
                  <td>
                    <code>{c.schema_name}</code>
                  </td>
                  <td className={c.ok ? 'set-ok' : 'set-fail'}>{c.ok ? `Connected, ${c.server}` : c.error}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="fw-buttons">
        <button type="button" className="su-btn" disabled={rows === null} onClick={load}>
          Check
        </button>
      </div>
    </div>
  )
}
