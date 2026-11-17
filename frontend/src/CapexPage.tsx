import { useCallback, useEffect, useMemo, useState } from 'react'
import { format, sum } from './amounts'
import { api, type Capex, type CapexChange, type CapexItem, type CapexNew, type CapexStatus, type Me } from './api'
import { STATUS_TEXT } from './capex'
import CapexCashOut from './CapexCashOut'
import CapexList from './CapexList'
import CapexOverview from './CapexOverview'
import CapexTypes from './CapexTypes'
import FilterBar, { FilterSearch } from './FilterBar'
import NotesPanel from './NotesPanel'
import PageHeader from './PageHeader'
import Select, { type Option } from './Select'

// Capex planning (ADR 0028): a round's investments as one list over every cost center the user
// reads, an overview by area, type and period, and the investment types (controllers).

type Tab = 'list' | 'overview' | 'types'
type Message = { kind: 'error' | 'ok' | 'info'; text: string }
type Ask = { kind: 'decline' | 'delete'; ids: number[]; reason: string }
const ALL = ''

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export default function CapexPage({
  version,
  me,
  focus,
  onFocused,
}: {
  version: string
  me: Me
  /** An investment's notes to open once the list is loaded (from Home), at a thread. */
  focus?: { investment: number; thread: number } | null
  onFocused?: () => void
}) {
  const [view, setView] = useState<Capex | null>(null)
  const [tab, setTab] = useState<Tab>('list')
  const [message, setMessage] = useState<Message | null>(null)
  const [query, setQuery] = useState('')
  const [costCenter, setCostCenter] = useState(ALL)
  const [priority, setPriority] = useState(ALL)
  const [status, setStatus] = useState(ALL)
  const [type, setType] = useState(ALL)
  const [waiting, setWaiting] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [menu, setMenu] = useState<{ item: CapexItem; anchor: DOMRect; kind: 'row' | 'status' | 'move' } | null>(null)
  const [cashFor, setCashFor] = useState<CapexItem | null>(null)
  const [notesFor, setNotesFor] = useState<{ item: CapexItem; thread: number | null } | null>(null)
  const [ask, setAsk] = useState<Ask | null>(null)
  const [lastCostCenter, setLastCostCenter] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const load = useCallback(() => {
    api.capex(version).then(setView, (e: unknown) => setMessage({ kind: 'error', text: errorText(e) }))
  }, [version])
  useEffect(load, [load])

  /** A write: the list as it returns, or the refusal and the list as it stands. */
  const run = useCallback(
    async (call: () => Promise<Capex>, ok?: string) => {
      setSaving(true)
      try {
        const next = await call()
        setView(next)
        setMessage(ok ? { kind: 'ok', text: ok } : null)
        return next
      } catch (e) {
        setMessage({ kind: 'error', text: errorText(e) })
        load()
        return null
      } finally {
        setSaving(false)
      }
    },
    [load],
  )

  const items = useMemo(() => view?.items ?? [], [view])
  const byId = useMemo(() => new Map(items.map((i) => [i.item_id, i])), [items])

  // Filters keep a large project with its lines: the group shows when the project or a line matches.
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    const matches = (i: CapexItem) =>
      (!q || `${i.item_id} ${i.name} ${i.cost_center_code} ${i.cost_center_name}`.toLowerCase().includes(q)) &&
      (!costCenter || i.cost_center_code === costCenter) &&
      (!priority || String(i.priority) === priority) &&
      (!status || i.status === status) &&
      (!type || i.type_code === type || i.kind === 'project') &&
      (!waiting || (i.can_confirm && i.status === 'open'))
    const groups = new Set<number>()
    for (const i of items) if (matches(i) && (i.kind === 'line' || !type)) groups.add(i.project_id ?? i.item_id)
    return items.filter((i) => groups.has(i.project_id ?? i.item_id))
  }, [items, query, costCenter, priority, status, type, waiting])

  if (message?.kind === 'error' && !view) return <div className="su-note error">{message.text}</div>
  if (!view) return null

  // The notes shown: those opened here, or the investment's Home opened the page for.
  const focused = focus ? view.items.find((i) => i.item_id === focus.investment) : undefined
  const notesOpen = notesFor ?? (focus && focused ? { item: focused, thread: focus.thread } : null)

  const locked = view.lock_reason !== null
  const writable = view.cost_centers.filter((c) => c.can_write)
  const defaultCostCenter =
    (costCenter && writable.some((c) => c.code === costCenter) ? costCenter : null) ??
    (lastCostCenter && writable.some((c) => c.code === lastCostCenter) ? lastCostCenter : null) ??
    writable[0]?.code ??
    null
  const lines = items.filter((i) => i.kind === 'line')
  const confirmed = sum(lines.filter((i) => i.status === 'confirmed').map((i) => i.amount))
  const planned = sum(lines.filter((i) => i.status !== 'declined').map((i) => i.amount))
  const toConfirm = items.filter((i) => i.project_id === null && i.can_confirm && i.status === 'open').length

  async function save(create: CapexNew[], update: CapexChange[]) {
    const cc = create.find((n) => n.cost_center)?.cost_center
    if (cc) setLastCostCenter(cc)
    await run(() => api.capexChanges(version, create, update))
  }

  async function decide(ids: number[], next: CapexStatus, reason: string | null) {
    const done = await run(
      () => api.capexStatus(version, ids, next, reason),
      `${ids.length} ${ids.length === 1 ? 'investment' : 'investments'} ${next === 'open' ? 'reopened' : next}.`,
    )
    if (done) setSelected(new Set())
  }

  async function addProject() {
    if (!defaultCostCenter) return
    setLastCostCenter(defaultCostCenter)
    await run(
      () => api.capexChanges(version, [{ kind: 'project', name: 'New large project', cost_center: defaultCostCenter }], []),
      'Large project added: name it, and add its lines in the row below it.',
    )
  }

  /** Large projects a line can move into: those the user plans, other than its own. */
  const projectsFor = (item: CapexItem) =>
    items.filter((p) => p.kind === 'project' && p.can_write && p.item_id !== item.project_id)

  function onRowMenu(item: CapexItem, value: string) {
    const anchor = menu?.anchor
    setMenu(null)
    if (value === 'move' && anchor) setMenu({ item, anchor, kind: 'move' })
    else if (value === 'cash') setCashFor(item)
    else if (value === 'line') void run(() => api.capexChanges(version, [{ kind: 'line', project_id: item.item_id, name: 'New line' }], []), 'Line added: name it and fill it in.')
    else if (value === 'project') void run(() => api.capexMakeProject(version, item.item_id), `${item.name} is a large project now; add its lines in the row below it.`)
    else if (value === 'out') void run(() => api.capexMove(version, item.item_id, null))
    else if (value === 'delete') setAsk({ kind: 'delete', ids: [item.item_id], reason: '' })
  }

  function rowOptions(item: CapexItem): Option[] {
    const write = item.can_write && !locked
    const options: Option[] = []
    if (view!.settings.cash_out && item.kind === 'line') options.push({ value: 'cash', label: 'Cash out', hint: write ? undefined : 'read only' })
    if (!write) return options
    if (item.kind === 'project') options.push({ value: 'line', label: 'Add a line' })
    if (item.kind === 'line' && item.project_id === null) options.push({ value: 'project', label: 'Make a large project', hint: 'this becomes its first line' })
    if (item.kind === 'line' && projectsFor(item).length > 0) options.push({ value: 'move', label: 'Move into large project…' })
    if (item.project_id !== null) options.push({ value: 'out', label: 'Take out of the project' })
    options.push({ value: 'delete', label: 'Delete', hint: item.kind === 'project' ? 'with its lines' : undefined })
    return options
  }

  function onStatusMenu(item: CapexItem, value: string) {
    setMenu(null)
    if (value === 'declined') setAsk({ kind: 'decline', ids: [item.item_id], reason: '' })
    else void decide([item.item_id], value as CapexStatus, null)
  }

  const statusOptions = (item: CapexItem): Option[] =>
    (['confirmed', 'declined', 'open'] as const)
      .filter((s) => s !== item.status)
      .map((s) => ({ value: s, label: s === 'open' ? 'Reopen' : s === 'confirmed' ? 'Confirm' : 'Decline', hint: s === 'declined' ? 'with a reason' : undefined }))

  const chosen = [...selected].map((id) => byId.get(id)).filter((i): i is CapexItem => !!i)
  const canDecide = chosen.length > 0 && chosen.every((i) => i.can_confirm) && !locked
  const canDelete = chosen.length > 0 && chosen.every((i) => i.can_write) && !locked
  const asked = ask ? ask.ids.map((id) => byId.get(id)).filter((i): i is CapexItem => !!i) : []

  const tabs: [Tab, string][] = [
    ['list', 'List'],
    ['overview', 'Overview'],
    ...(me.is_controller ? [['types', 'Types'] as [Tab, string]] : []),
  ]

  return (
    <div className={`su ${tab === 'list' && view.on ? 'pl cx-page' : 'cx-page'}`}>
      <PageHeader
        eyebrow={`Investments · ${view.round.name}`}
        title={`Capex plan ${view.round.fiscal_year}`}
        sub={
          <>
            {view.on ? (
              <>
                {lines.length} investments · {format(planned) || '0.00'} planned · {format(confirmed) || '0.00'} confirmed
                {view.settings.project_threshold && ` · large project from ${format(view.settings.project_threshold)}`}
              </>
            ) : (
              'Not planned in this round'
            )}
            {saving && <span className="pg-saving"> · Saving…</span>}
          </>
        }
        tabs={
          view.on && (
            <div className="seg" role="tablist" aria-label="Investments">
              {tabs.map(([value, label]) => (
                <button key={value} type="button" role="tab" aria-selected={tab === value} aria-pressed={tab === value} onClick={() => setTab(value)}>
                  {label}
                </button>
              ))}
            </div>
          )
        }
      />

      {!view.on && (
        <div className="su-card empty-state">
          <h3>No capex planning in this round</h3>
          <p className="su-hint">
            {me.is_controller ? 'Turn it on in Setup, step 7.' : 'The controller turns it on in the round’s setup.'}
          </p>
        </div>
      )}
      {view.on && view.lock_reason && (
        <div className="su-note pl-note">
          <strong>Read only:</strong> {view.lock_reason}.
        </div>
      )}
      {message && <div className={`su-note ${message.kind} pl-note`}>{message.text}</div>}

      {view.on && tab === 'list' && (
        <>
          <FilterBar>
            <FilterSearch value={query} onChange={setQuery} placeholder="Search investments" label="Search investments" />
            <Select
              variant="compact"
              label="Cost center"
              value={costCenter}
              searchable
              onChange={setCostCenter}
              options={[{ value: ALL, label: 'All cost centers' }, ...view.cost_centers.map((c) => ({ value: c.code, label: `${c.code} ${c.name}`, hint: c.can_write ? undefined : 'read only' }))]}
            />
            <Select
              variant="compact"
              label="Priority"
              value={priority}
              onChange={setPriority}
              options={[{ value: ALL, label: 'All priorities' }, ...['1', '2', '3'].map((p) => ({ value: p, label: `Priority ${p}` }))]}
            />
            <Select
              variant="compact"
              label="Confirmation"
              value={status}
              onChange={setStatus}
              options={[{ value: ALL, label: 'Any confirmation' }, ...(['open', 'confirmed', 'declined'] as const).map((s) => ({ value: s, label: STATUS_TEXT[s] }))]}
            />
            <Select
              variant="compact"
              label="Type"
              value={type}
              onChange={setType}
              options={[{ value: ALL, label: 'All types' }, ...view.types.map((t) => ({ value: t.code, label: t.name, hint: t.code }))]}
            />
            {view.confirms && (
              <label className={`cx-waiting-toggle${waiting ? ' on' : ''}`}>
                <input type="checkbox" checked={waiting} onChange={(e) => setWaiting(e.target.checked)} />
                To confirm <b>{toConfirm}</b>
              </label>
            )}
            <span className="cp-grow" />
            {!locked && writable.length > 0 && (
              <button type="button" className="su-btn" onClick={() => void addProject()}>
                + Large project
              </button>
            )}
          </FilterBar>

          {/* The selection's actions and questions have a place of their own, so the list stays put. */}
          <div className="cx-slot">
          {chosen.length > 0 && !ask && (
            <div className="su-note info cx-selection">
              <strong>{chosen.length} selected</strong>
              {canDecide && (
                <>
                  <button type="button" className="su-btn primary" onClick={() => void decide(chosen.map((i) => i.item_id), 'confirmed', null)}>
                    Confirm
                  </button>
                  <button type="button" className="su-btn" onClick={() => setAsk({ kind: 'decline', ids: chosen.map((i) => i.item_id), reason: '' })}>
                    Decline
                  </button>
                  <button type="button" className="su-btn ghost" onClick={() => void decide(chosen.map((i) => i.item_id), 'open', null)}>
                    Reopen
                  </button>
                </>
              )}
              {canDelete && (
                <button type="button" className="su-btn ghost danger" onClick={() => setAsk({ kind: 'delete', ids: chosen.map((i) => i.item_id), reason: '' })}>
                  Delete
                </button>
              )}
              <button type="button" className="nt-link" onClick={() => setSelected(new Set())}>
                Clear
              </button>
            </div>
          )}

          {ask && (
            <div className={`su-note ${ask.kind === 'delete' ? 'warn' : 'info'} cx-selection`}>
              <strong>
                {ask.kind === 'delete' ? 'Delete' : 'Decline'} {asked.length === 1 ? asked[0].name : `${asked.length} investments`}
                {ask.kind === 'delete' && asked.some((i) => i.kind === 'project') ? ' with the large project’s lines' : ''}?
              </strong>
              {ask.kind === 'decline' && (
                <input
                  className="su-input cx-reason"
                  autoFocus
                  placeholder="Reason"
                  aria-label="Reason for declining"
                  value={ask.reason}
                  onChange={(e) => setAsk({ ...ask, reason: e.target.value })}
                />
              )}
              <button
                type="button"
                className={`su-btn ${ask.kind === 'delete' ? 'danger' : 'primary'}`}
                disabled={ask.kind === 'decline' && !ask.reason.trim()}
                onClick={() => {
                  const { kind, ids, reason } = ask
                  setAsk(null)
                  if (kind === 'decline') void decide(ids, 'declined', reason)
                  else
                    void run(() => api.capexDelete(version, ids), `${ids.length === 1 ? 'Investment' : `${ids.length} investments`} deleted.`).then(() =>
                      setSelected(new Set()),
                    )
                }}
              >
                {ask.kind === 'delete' ? 'Delete' : 'Decline'}
              </button>
              <button type="button" className="su-btn ghost" onClick={() => setAsk(null)}>
                Cancel
              </button>
            </div>
          )}
          </div>

          <CapexList
            view={view}
            items={shown}
            locked={locked}
            selected={selected}
            onSelect={setSelected}
            defaultCostCenter={defaultCostCenter}
            actions={{
              save,
              openMenu: (item, anchor) => setMenu({ item, anchor, kind: 'row' }),
              openStatus: (item, anchor) => setMenu({ item, anchor, kind: 'status' }),
              openCashOut: setCashFor,
              openNotes: (item) => setNotesFor({ item, thread: null }),
              error: (text) => setMessage({ kind: 'error', text }),
            }}
          />
        </>
      )}
      {view.on && tab === 'overview' && (
        <CapexOverview
          key={JSON.stringify(view.items.map((i) => [i.item_id, i.row_version]))}
          version={version}
          onWaiting={() => {
            setWaiting(true)
            setTab('list')
          }}
        />
      )}
      {view.on && tab === 'types' && <CapexTypes onSaved={load} />}

      {menu && (
        <Select
          key={menu.kind}
          label={
            menu.kind === 'row'
              ? `Actions for ${menu.item.name}`
              : menu.kind === 'move'
                ? `Move ${menu.item.name} into a large project`
                : `Confirmation of ${menu.item.name}`
          }
          anchor={menu.anchor}
          value=""
          searchable={menu.kind === 'move' ? undefined : false}
          options={
            menu.kind === 'row'
              ? rowOptions(menu.item)
              : menu.kind === 'move'
                ? projectsFor(menu.item).map((p) => ({ value: String(p.item_id), label: p.name }))
                : statusOptions(menu.item)
          }
          onClose={() => setMenu(null)}
          onChange={(value) => {
            if (menu.kind === 'row') onRowMenu(menu.item, value)
            else if (menu.kind === 'status') onStatusMenu(menu.item, value)
            else {
              setMenu(null)
              void run(() => api.capexMove(version, menu.item.item_id, Number(value)), `${menu.item.name} moved into the large project.`)
            }
          }}
        />
      )}
      {cashFor && (
        <CapexCashOut
          view={view}
          item={byId.get(cashFor.item_id) ?? cashFor}
          readOnly={locked || !cashFor.can_write}
          onSave={async (periods) => {
            const next = await api.capexCashOut(version, cashFor.item_id, periods)
            setView(next)
          }}
          onClose={() => setCashFor(null)}
        />
      )}
      {notesOpen && (
        <NotesPanel
          display="dialog"
          notepad={false}
          version={version}
          line={{ module: 'capex', investment: notesOpen.item.item_id, cost_center: notesOpen.item.cost_center_code }}
          title={`${notesOpen.item.item_id} · ${notesOpen.item.name}`}
          sub={`${notesOpen.item.cost_center_code} ${notesOpen.item.cost_center_name} · ${STATUS_TEXT[notesOpen.item.status]}${notesOpen.item.status_by ? ` by ${notesOpen.item.status_by}` : ''}`}
          focusThread={notesOpen.thread}
          onChanged={load}
          onClose={() => {
            setNotesFor(null)
            onFocused?.()
          }}
        />
      )}
    </div>
  )
}
