import { useEffect, useState } from 'react'
import { api, type Account, type PlanningUnit } from './api'
import Select from './Select'

// Where an item list goes (ADR 0036): a cost center the user plans in the round and an account
// it plans there. Starts a new list, or moves one.

export default function ItemListPlace({
  version,
  roundName,
  title,
  sub,
  action,
  from,
  onPick,
  onClose,
}: {
  version: string
  roundName: string
  title: string
  sub: string
  action: string
  from?: { costCenter: string; account: string }
  onPick: (costCenter: string, account: string) => Promise<void>
  onClose: () => void
}) {
  const [units, setUnits] = useState<PlanningUnit[] | null>(null)
  const [costCenter, setCostCenter] = useState(from?.costCenter ?? '')
  const [accounts, setAccounts] = useState<Account[]>([])
  const [account, setAccount] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.costCenters(version).then(
      (all) => setUnits(all.filter((u) => u.can_write)),
      (e: unknown) => setError(e instanceof Error ? e.message : String(e)),
    )
  }, [version])

  useEffect(() => {
    if (!costCenter) return
    api.accounts(version, costCenter).then(setAccounts, (e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [version, costCenter])

  const same = from && from.costCenter === costCenter && from.account === account
  async function pick() {
    setBusy(true)
    try {
      await onPick(costCenter, account)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  return (
    <div className="il-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="il-dialog il-export"
        role="dialog"
        aria-modal="true"
        aria-labelledby="il-place-title"
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Escape') onClose()
        }}
      >
        <header className="il-head">
          <div>
            <div className="su-eyebrow">Item lists · {roundName}</div>
            <h3 id="il-place-title">{title}</h3>
            <div className="ink-3 il-sub">{sub}</div>
          </div>
          <button type="button" className="su-icon" aria-label="Close" title="Close (Esc)" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="il-body il-place-body">
          <label className="su-field">
            Cost center
            <Select
              label="Cost center"
              value={costCenter}
              searchable
              placeholder={units?.length === 0 ? 'No cost center you plan in this round' : 'Choose a cost center'}
              disabled={!units?.length}
              options={(units ?? []).map((u) => ({ value: u.code, label: `${u.code} ${u.name}` }))}
              onChange={(c) => {
                setCostCenter(c)
                setAccount('')
              }}
            />
          </label>
          <label className="su-field">
            Account
            <Select
              label="Account"
              value={account}
              searchable
              placeholder={costCenter ? 'Choose an account' : 'Choose a cost center first'}
              disabled={!costCenter}
              options={accounts.map((a) => ({ value: a.code, label: `${a.code} ${a.name}` }))}
              onChange={setAccount}
            />
          </label>
          {error && <div className="su-note error">{error}</div>}
        </div>
        <footer className="il-foot">
          <span className="su-hint">Accounts the cost center plans in this round.</span>
          <button type="button" className="su-btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="su-btn primary"
            disabled={!costCenter || !account || same || busy}
            onClick={() => void pick()}
          >
            {action}
          </button>
        </footer>
      </div>
    </div>
  )
}
