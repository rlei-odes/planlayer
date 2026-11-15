import { useCallback, useEffect, useState } from 'react'
import { api, type PayRights, type PayRightsRow } from './api'
import Select from './Select'

// Who reads pay in a round (ADR 0023), for HR: per cost center the readers HR lists, proposed
// from the owners the source names; the release, before which nobody but HR reads pay; and the
// log of every change of these rights and of the HR role, read by every HR user.

const WHEN = new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short' })
const ACTION: Record<string, string> = {
  reader_added: 'gave pay rights to',
  reader_removed: 'took pay rights from',
  released: 'released pay',
  release_withdrawn: 'took the release back',
  hr_granted: 'gave the HR role to',
  hr_revoked: 'took the HR role from',
}

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export default function PayAccess({ version, onChanged }: { version: string; onChanged: () => void }) {
  const [rights, setRights] = useState<PayRights | null>(null)
  const [message, setMessage] = useState<{ kind: 'error' | 'ok'; text: string } | null>(null)
  const [adding, setAdding] = useState<PayRightsRow | null>(null)
  const [filter, setFilter] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api.payRights(version).then(setRights, (e: unknown) => setMessage({ kind: 'error', text: errorText(e) }))
  }, [version])
  useEffect(load, [load])

  async function change(task: () => Promise<PayRights>, done: string) {
    setBusy(true)
    try {
      setRights(await task())
      setMessage({ kind: 'ok', text: done })
      onChanged()
      return true
    } catch (e) {
      setMessage({ kind: 'error', text: errorText(e) })
      return false
    } finally {
      setBusy(false)
    }
  }

  if (!rights) return message ? <div className="su-note error">{message.text}</div> : null

  const released = rights.released_by !== null
  const proposals = rights.rows.filter((r) => r.proposal)
  const q = filter.trim().toLowerCase()
  const rows = rights.rows.filter(
    (r) => !q || `${r.code} ${r.name} ${r.readers.map((p) => `${p.login} ${p.name}`).join(' ')}`.toLowerCase().includes(q),
  )
  const name = Object.fromEntries(rights.users.map((u) => [u.login, u.name]))
  const readers = (r: PayRightsRow) => r.readers.map((p) => p.login)

  return (
    <>
      {message && <div className={`su-note ${message.kind === 'error' ? 'error' : 'ok'}`}>{message.text}</div>}

      <section className={`su-card pc-module${released ? ' on' : ''}`}>
        <div>
          <strong>{released ? `Released to its readers` : 'Not released: only HR reads pay in this round'}</strong>
          <span className="su-hint">
            {released
              ? `By ${rights.released_by}, ${WHEN.format(new Date(rights.released_at!))}. Changes to the lists below apply at once.`
              : 'Check the readers below, then release. Owners see their cost centers’ pay from then on; the Pay buttons on their Home stay inactive until then.'}
          </span>
        </div>
        <button
          type="button"
          className={`su-btn${released ? ' ghost' : ' primary'}`}
          disabled={busy}
          onClick={() =>
            void change(
              () => api.releasePay(version, !released),
              released ? 'Release taken back: only HR reads pay in this round.' : 'Pay released to its readers.',
            )
          }
        >
          {released ? 'Take release back' : 'Release pay'}
        </button>
      </section>

      {proposals.length > 0 && (
        <div className="su-note warn">
          <strong>
            {proposals.length} cost center{proposals.length === 1 ? '' : 's'} with an owner to decide on:
          </strong>{' '}
          the source names an owner you haven’t confirmed or declined yet. They read nothing until you add them.
        </div>
      )}

      <section className="su-card">
        <div className="fw-card-head">
          <h3>Readers per cost center</h3>
          <span className="su-hint">A cost center without readers is HR only.</span>
          <span className="cp-grow" />
          <input
            className="su-input pc-filter"
            placeholder="Filter…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        <div className="tbl-scroll">
          <table className="tbl pc-rights">
            <thead>
              <tr>
                <th>Cost center</th>
                <th>Source owner</th>
                <th>Readers</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.code} className={r.proposal ? 'pc-proposal' : undefined}>
                  <td>
                    <span className="pl-code">{r.code}</span> {r.name}
                  </td>
                  <td>
                    {r.source_owner ? (
                      <span title={r.source_owner.login}>{r.source_owner.name}</span>
                    ) : (
                      <span className="muted">none</span>
                    )}
                    {r.proposal && r.source_owner && (
                      <span className="pc-decide">
                        {readers(r).includes(r.source_owner.login) ? (
                          <button
                            type="button"
                            className="su-btn sm"
                            disabled={busy}
                            onClick={() => void change(() => api.setPayReaders(version, r.code, readers(r), true), `${r.code}: owner confirmed.`)}
                          >
                            Confirm
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="su-btn sm primary"
                            disabled={busy}
                            onClick={() =>
                              void change(
                                () => api.setPayReaders(version, r.code, [...readers(r), r.source_owner!.login], true),
                                `${r.source_owner!.name} reads ${r.code}’s pay.`,
                              )
                            }
                          >
                            Add as reader
                          </button>
                        )}
                        <button
                          type="button"
                          className="su-btn sm ghost"
                          disabled={busy}
                          onClick={() => void change(() => api.setPayReaders(version, r.code, readers(r), true), `${r.code}: kept as it is.`)}
                        >
                          Keep list
                        </button>
                      </span>
                    )}
                  </td>
                  <td>
                    {r.readers.length === 0 ? (
                      <span className="su-pill neutral">HR only</span>
                    ) : (
                      <span className="pc-chips">
                        {r.readers.map((p) => (
                          <span key={p.login} className="pc-chip" title={p.login}>
                            {p.name}
                            <button
                              type="button"
                              aria-label={`Take pay rights from ${p.name}`}
                              title="Take pay rights away"
                              disabled={busy}
                              onClick={() =>
                                void change(
                                  () => api.setPayReaders(version, r.code, readers(r).filter((x) => x !== p.login)),
                                  `${p.name} no longer reads ${r.code}’s pay.`,
                                )
                              }
                            >
                              ×
                            </button>
                          </span>
                        ))}
                      </span>
                    )}
                  </td>
                  <td className="right">
                    <button type="button" className="su-btn ghost sm" onClick={() => setAdding(r)}>
                      <span className="pl-plus">+</span> Reader
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="su-card">
        <div className="fw-card-head">
          <h3>Log</h3>
          <span className="su-hint">Every change of pay rights and of the HR role, in every round. Every HR user sees it.</span>
        </div>
        {rights.log.length === 0 ? (
          <p className="su-hint wf-empty">Nothing yet.</p>
        ) : (
          <ul className="pc-log">
            {rights.log.map((e, i) => (
              <li key={i}>
                <span className="ink-3">{WHEN.format(new Date(e.at))}</span>
                <span>
                  <strong>{e.actor ?? 'system'}</strong> {ACTION[e.action] ?? e.action}
                  {e.user && <strong> {name[e.user] ?? e.user}</strong>}
                  {e.cost_center && (
                    <>
                      {' '}
                      on <span className="pl-code">{e.cost_center}</span>
                    </>
                  )}
                  {e.round_code && <span className="ink-3"> · {e.round_code}</span>}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {adding && (
        <AddReaderDialog
          row={adding}
          rights={rights}
          busy={busy}
          onClose={() => setAdding(null)}
          onAdd={async (login) => {
            const ok = await change(
              () => api.setPayReaders(version, adding.code, [...readers(adding), login]),
              `${name[login] ?? login} reads ${adding.code}’s pay.`,
            )
            if (ok) setAdding(null)
          }}
        />
      )}
    </>
  )
}

/** Choosing a reader: the suggestions first (the source owner, deputies, approvers above);
 * "More…" opens every active user, which the log records like any other change. */
function AddReaderDialog({
  row,
  rights,
  busy,
  onClose,
  onAdd,
}: {
  row: PayRightsRow
  rights: PayRights
  busy: boolean
  onClose: () => void
  onAdd: (login: string) => Promise<void>
}) {
  const [all, setAll] = useState(false)
  const [login, setLogin] = useState('')
  const listed = new Set(row.readers.map((p) => p.login))
  const suggested = new Set(row.suggested)
  const options = rights.users
    .filter((u) => !listed.has(u.login) && (all || suggested.has(u.login)))
    .map((u) => ({
      value: u.login,
      label: u.name,
      hint: [
        u.login,
        row.source_owner?.login === u.login ? 'source owner' : suggested.has(u.login) ? 'deputy or approver' : '',
      ]
        .filter(Boolean)
        .join(' · '),
    }))

  return (
    <div className="il-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form
        className="il-dialog pc-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pc-add-title"
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Escape') onClose()
        }}
        onSubmit={(e) => {
          e.preventDefault()
          if (login) void onAdd(login)
        }}
      >
        <header className="il-head">
          <div>
            <div className="su-eyebrow">Give pay rights</div>
            <h3 id="pc-add-title">
              <span className="pl-code">{row.code}</span> {row.name}
            </h3>
            <div className="ink-3 il-sub">
              They read and change the pay of this cost center’s positions in {rights.round_code}, once the round is released.
            </div>
          </div>
          <button type="button" className="su-icon" aria-label="Close" title="Close (Esc)" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="il-body pc-dialog-body">
          <div className="su-field">
            {all ? 'Any user' : 'Suggested: the source owner, deputies and approvers above'}
            <Select
              label="Reader"
              value={login}
              onChange={setLogin}
              searchable
              placeholder={options.length ? 'Choose a person' : all ? 'Everyone reads it already' : 'No suggestions left'}
              options={options}
            />
          </div>
          {!all && (
            <button type="button" className="su-btn ghost sm pc-more" onClick={() => setAll(true)}>
              More… every user
            </button>
          )}
          {all && <span className="su-hint">Anyone can be given pay rights; the log shows every HR user who did.</span>}
        </div>
        <footer className="il-foot">
          <span className="su-hint">Saved at once, and written to the log.</span>
          <button type="button" className="su-btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="su-btn primary" disabled={!login || busy}>
            Give pay rights
          </button>
        </footer>
      </form>
    </div>
  )
}
