import { useCallback, useEffect, useState } from 'react'
import { display } from './amounts'
import { api, type ItemList, type ListSummary, type Version } from './api'
import ItemListEditor from './ItemListEditor'
import ItemListExport from './ItemListExport'
import ItemListPlace from './ItemListPlace'
import ItemListTakeOver from './ItemListTakeOver'
import { VISIBILITY } from './itemLists'
import PageHeader from './PageHeader'

// Every item list the user can see (ADR 0017): their own, those shared with the cost centers
// they plan, and public ones of cost centers they read. Lists live outside rounds; against the
// current round, a list whose line isn't planned there says why, and its owner can move it
// (ADR 0036).

const WHEN = new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium' })

type Unplaced = NonNullable<ListSummary['unplaced']>
type Filter = 'all' | 'unplaced'

function unplacedText(reason: Unplaced, round: string): string {
  switch (reason) {
    case 'cost_center_gone':
      return 'Cost center no longer delivered'
    case 'account_gone':
      return 'Account no longer delivered'
    case 'account_group':
      return 'Account is a group now'
    case 'cost_center_not_planned':
      return `Cost center not planned in ${round}`
    case 'account_not_planned':
      return `Account not planned in ${round}`
    case 'outside_account_set':
      return 'Not in the cost center’s account set'
  }
}

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export default function ItemListsPage({ version, round }: { version: string | null; round: Version | undefined }) {
  const [lists, setLists] = useState<ListSummary[] | null>(null)
  const [open, setOpen] = useState<ItemList | null>(null)
  const [fresh, setFresh] = useState<{ costCenter: string; account: string; title: string } | null>(null)
  const [placing, setPlacing] = useState<{ move: ListSummary | null } | null>(null)
  const [takingOver, setTakingOver] = useState<ListSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [filter, setFilter] = useState<Filter>('all')
  const roundName = round?.name ?? version ?? ''

  const load = useCallback(() => {
    api.itemLists(version ?? undefined).then(setLists, (e: unknown) => setError(errorText(e)))
  }, [version])
  useEffect(load, [load])

  function show(id: number) {
    api.itemList(id).then(setOpen, (e: unknown) => setError(errorText(e)))
  }

  async function start(costCenter: string, account: string) {
    const existing = await api.lineList(costCenter, account)
    setPlacing(null)
    if (existing) {
      setOpen(existing)
      return
    }
    setFresh({ costCenter, account, title: `${costCenter} · ${account}` })
  }

  async function moveTo(list: ListSummary, costCenter: string, account: string) {
    const moved = await api.moveList(list.list_id, costCenter, account)
    setPlacing(null)
    setNote(`Moved to ${moved.cost_center_code} ${moved.cost_center_name} · ${moved.account_code} ${moved.account_name}.`)
    load()
  }

  const unplaced = (lists ?? []).filter((l) => l.unplaced)
  const shown = filter === 'unplaced' ? unplaced : (lists ?? [])
  return (
    <div className="su il-page">
      <PageHeader
        eyebrow="Item lists"
        title="My item lists"
        sub="The items behind a line’s value: invoices, licences, contracts. Lists live outside planning rounds: keep them up to date during the year, and move them when a line changes."
        actions={
          <>
            {lists && lists.length > 0 && (
              <button type="button" className="su-btn" onClick={() => setExporting(true)}>
                Export
              </button>
            )}
            {version && (
              <button type="button" className="su-btn primary" onClick={() => setPlacing({ move: null })}>
                + List
              </button>
            )}
          </>
        }
        tabs={
          version &&
          unplaced.length > 0 && (
            <div className="seg" role="group" aria-label="Show">
              <button type="button" aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>
                All <span className="count">{lists?.length ?? 0}</span>
              </button>
              <button type="button" aria-pressed={filter === 'unplaced'} onClick={() => setFilter('unplaced')}>
                Not in {roundName} <span className="count">{unplaced.length}</span>
              </button>
            </div>
          )
        }
      />
      {error && <div className="su-note error">{error}</div>}
      {note && <div className="su-note ok">{note}</div>}
      {lists && lists.length === 0 && (
        <div className="su-card empty-state">
          <h3>No item lists yet</h3>
          <p className="su-hint">Start one with + List, or from the list icon beside an account on a planning sheet.</p>
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
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {shown.map((l) => (
                  <tr key={l.list_id} className="il-row" onClick={() => show(l.list_id)}>
                    <td>
                      <span className="pl-code">{l.cost_center_code}</span>
                      {l.cost_center_name}
                    </td>
                    <td>
                      <span className="pl-code">{l.account_code}</span>
                      {l.account_name}
                      {l.unplaced && (
                        <span className="su-pill overdue-soft il-unplaced">{unplacedText(l.unplaced, roundName)}</span>
                      )}
                    </td>
                    <td className="il-nowrap">
                      {l.mine ? 'Me' : l.owner_name}
                      {l.owner_disabled && <span className="su-pill neutral il-unplaced">Disabled</span>}
                    </td>
                    <td>
                      <span className="su-pill neutral" title={VISIBILITY[l.visibility].hint}>
                        {VISIBILITY[l.visibility].label}
                      </span>
                    </td>
                    <td className="num">{l.items}</td>
                    <td className="num">{display(l.total)}</td>
                    <td className="ink-3 il-nowrap">
                      {WHEN.format(new Date(l.changed_at))} · {l.changed_by}
                    </td>
                    <td className="right" onClick={(e) => e.stopPropagation()}>
                      {l.mine && version && (
                        <button type="button" className="su-btn ghost sm" onClick={() => setPlacing({ move: l })}>
                          Move
                        </button>
                      )}
                      {l.can_take_over && (
                        <button type="button" className="su-btn sm" onClick={() => setTakingOver(l)}>
                          Take over
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {exporting && lists && <ItemListExport lists={lists} onClose={() => setExporting(false)} />}
      {placing && version && (
        <ItemListPlace
          version={version}
          roundName={roundName}
          title={placing.move ? 'Move list' : 'New list'}
          sub={
            placing.move
              ? `${placing.move.cost_center_code} ${placing.move.cost_center_name} · ${placing.move.account_code} ${placing.move.account_name}: items, visibility and owner move with it. Plan values stay where they are.`
              : 'The line the list belongs to. A line that has a list already opens it.'
          }
          action={placing.move ? 'Move' : 'Continue'}
          from={placing.move ? { costCenter: placing.move.cost_center_code, account: placing.move.account_code } : undefined}
          onPick={(cc, acc) => (placing.move ? moveTo(placing.move, cc, acc) : start(cc, acc))}
          onClose={() => setPlacing(null)}
        />
      )}
      {takingOver && (
        <ItemListTakeOver
          list={takingOver}
          onDone={() => {
            setTakingOver(null)
            setNote('The list has a new owner.')
            load()
          }}
          onClose={() => setTakingOver(null)}
        />
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
      {fresh && (
        <ItemListEditor
          list={null}
          costCenter={fresh.costCenter}
          account={fresh.account}
          title={fresh.title}
          canCreate
          onSaved={(saved) => {
            load()
            if (saved) {
              setFresh(null)
              setOpen(saved)
            }
          }}
          onClose={() => setFresh(null)}
        />
      )}
    </div>
  )
}
