import { useCallback, useEffect, useState } from 'react'
import { display } from './amounts'
import { api, type ItemList, type ListSummary } from './api'
import ItemListEditor from './ItemListEditor'
import { VISIBILITY } from './itemLists'
import PageHeader from './PageHeader'

// Every item list the user can see (ADR 0017): their own, those shared with the cost centers
// they plan, and public ones of cost centers they read. A list is started from its line on the
// planning sheet; here it is kept up to date during the year.

const WHEN = new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium' })

export default function ItemListsPage() {
  const [lists, setLists] = useState<ListSummary[] | null>(null)
  const [open, setOpen] = useState<ItemList | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    api.itemLists().then(setLists, (e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [])
  useEffect(load, [load])

  function show(id: number) {
    api.itemList(id).then(setOpen, (e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }

  return (
    <div className="su il-page">
      <PageHeader
        eyebrow="Item lists"
        title="My item lists"
        sub="The items behind a line’s value: invoices, licences, contracts. Lists live outside planning rounds, so keep them up to date during the year. Start one from a line on the planning sheet (the list icon beside the account)."
      />
      {error && <div className="su-note error">{error}</div>}
      {lists && lists.length === 0 && (
        <div className="su-card empty-state">
          <h3>No item lists yet</h3>
          <p className="su-hint">Open a planning sheet and click the list icon beside an account to start one.</p>
        </div>
      )}
      {lists && lists.length > 0 && (
        <div className="tbl-card">
          <div className="tbl-scroll">
            <table className="tbl il-overview">
              <thead>
                <tr>
                  <th>Cost center</th>
                  <th>Account</th>
                  <th>Owner</th>
                  <th>Visible to</th>
                  <th className="num">Items</th>
                  <th className="num">Total</th>
                  <th>Changed</th>
                </tr>
              </thead>
              <tbody>
                {lists.map((l) => (
                  <tr key={l.list_id} className="il-row" onClick={() => show(l.list_id)}>
                    <td>
                      <span className="pl-code">{l.cost_center_code}</span>
                      {l.cost_center_name}
                    </td>
                    <td>
                      <span className="pl-code">{l.account_code}</span>
                      {l.account_name}
                    </td>
                    <td>{l.mine ? 'Me' : l.owner_name}</td>
                    <td>
                      <span className="su-pill neutral" title={VISIBILITY[l.visibility].hint}>
                        {VISIBILITY[l.visibility].label}
                      </span>
                    </td>
                    <td className="num">{l.items}</td>
                    <td className="num">{display(l.total)}</td>
                    <td className="ink-3">
                      {WHEN.format(new Date(l.changed_at))} · {l.changed_by}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {open && (
        <ItemListEditor
          list={open}
          costCenter={open.cost_center_code}
          account={open.account_code}
          title={`${open.cost_center_code} ${open.cost_center_name} · ${open.account_code} ${open.account_name}`}
          canCreate={false}
          onSaved={load}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  )
}
