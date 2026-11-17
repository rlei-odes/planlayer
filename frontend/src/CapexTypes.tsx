import { useEffect, useState } from 'react'
import { api, type CapexType } from './api'

// The installation's investment types (ADR 0028), set by controllers: the balance sheet account
// an investment is capitalised on and its default useful life for depreciation. A type that
// investments use stays; inactive, no new investment takes it.

export default function CapexTypes({ onSaved }: { onSaved: () => void }) {
  const [saved, setSaved] = useState<CapexType[] | null>(null)
  const [draft, setDraft] = useState<CapexType[]>([])
  const [note, setNote] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.capexTypes().then(
      (types) => {
        setSaved(types)
        setDraft(types)
      },
      (e: unknown) => setNote({ kind: 'error', text: String(e) }),
    )
  }, [])

  const dirty = JSON.stringify(saved) !== JSON.stringify(draft)
  const change = (i: number, patch: Partial<CapexType>) => setDraft(draft.map((t, j) => (j === i ? { ...t, ...patch } : t)))

  async function save() {
    setSaving(true)
    try {
      const types = await api.saveCapexTypes(draft)
      setSaved(types)
      setDraft(types)
      setNote({ kind: 'ok', text: 'Investment types saved.' })
      onSaved()
    } catch (e) {
      setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setSaving(false)
    }
  }

  if (saved === null) return note ? <div className={`su-note ${note.kind}`}>{note.text}</div> : null
  return (
    <section className="su-card">
      <div className="cx-card-head">
        <h3>Investment types</h3>
      </div>
      <p className="su-hint">For all rounds. Asset account and useful life are the defaults for depreciation.</p>
      {note && <div className={`su-note ${note.kind}`}>{note.text}</div>}
      <div className="tbl-scroll">
        <table className="tbl cx-types">
          <thead>
            <tr>
              <th>Code</th>
              <th>Name</th>
              <th>Asset account</th>
              <th className="num">Useful life (years)</th>
              <th>Active</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {draft.map((t, i) => (
              <tr key={i}>
                <td>
                  <input maxLength={20}
                    className="su-input cx-code"
                    value={t.code}
                    aria-label="Code"
                    disabled={!!t.used && saved.some((s) => s.code === t.code)}
                    onChange={(e) => change(i, { code: e.target.value })}
                  />
                </td>
                <td>
                  <input maxLength={100} className="su-input" value={t.name} aria-label="Name" onChange={(e) => change(i, { name: e.target.value })} />
                </td>
                <td>
                  <input maxLength={40}
                    className="su-input cx-code"
                    value={t.asset_account ?? ''}
                    aria-label="Asset account"
                    onChange={(e) => change(i, { asset_account: e.target.value || null })}
                  />
                </td>
                <td className="num">
                  <input maxLength={3}
                    className="su-input num cx-life"
                    inputMode="numeric"
                    value={t.useful_life ?? ''}
                    aria-label="Useful life in years"
                    onChange={(e) => change(i, { useful_life: e.target.value === '' ? null : Number(e.target.value.replace(/\D/g, '')) })}
                  />
                </td>
                <td>
                  <input type="checkbox" checked={t.active ?? true} aria-label="Active" onChange={(e) => change(i, { active: e.target.checked })} />
                </td>
                <td>
                  {t.used ? (
                    <span className="su-pill neutral" title="Investments have this type">
                      In use
                    </span>
                  ) : (
                    <button type="button" className="nt-link" onClick={() => setDraft(draft.filter((_, j) => j !== i))}>
                      Remove
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="su-row cx-types-foot">
        <button type="button" className="su-btn" onClick={() => setDraft([...draft, { code: '', name: '', asset_account: null, useful_life: null, active: true }])}>
          + Type
        </button>
        <span className="cp-grow" />
        {dirty && (
          <button type="button" className="su-btn ghost" onClick={() => setDraft(saved)}>
            Discard
          </button>
        )}
        <button type="button" className="su-btn primary" disabled={!dirty || saving} onClick={() => void save()}>
          {saving ? 'Saving…' : 'Save types'}
        </button>
      </div>
    </section>
  )
}
