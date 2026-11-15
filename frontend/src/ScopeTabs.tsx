import { useState } from 'react'
import { assign, filterTree, inSet, setOf, thousands, type AccountSet, type ScopeRow } from './scope'
import Select from './Select'
import type { SetupDraft } from './setupTypes'

// The setup page's Cost centers and Accounts tabs (ADR 0014): what the round plans, each cost
// center's account set, and last year's figures beside them. They edit the same draft as the
// rest of the setup page, saved all or nothing.

type Props = {
  draft: SetupDraft
  setDraft: (d: SetupDraft) => void
  rows: ScopeRow[]
  fiscalYear: number
  sourceName: string | null
}

function Switch({
  on,
  label,
  onChange,
  disabled,
  title,
}: {
  on: boolean
  label: string
  onChange: (on: boolean) => void
  disabled?: boolean
  title?: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      className="pc-switch"
      disabled={disabled}
      title={title}
      onClick={() => onChange(!on)}
    >
      <i />
    </button>
  )
}

function Name({ row, sourceName }: { row: ScopeRow; sourceName: string | null }) {
  // Leaves lead with their code, as on the sheets; nodes lead with their name and show the code
  // after it, since inherited account sets name the node by its code.
  return (
    <span className="sc-name" style={{ paddingLeft: row.depth * 18 }}>
      {!row.is_group && <span className="pl-code">{row.code}</span>}
      <span className={row.is_group ? 'sc-group' : undefined}>{row.name}</span>
      {row.is_group && <span className="sc-node-code">{row.code}</span>}
      {row.new && (
        <span
          className="sc-flag new"
          title={
            sourceName
              ? `Not in ${sourceName}: first time in a planning round, without plan values to start from.`
              : 'First time in a planning round, without plan values to start from.'
          }
        >
          new
        </span>
      )}
      {row.missing && (
        <span className="sc-flag gone" title="The source no longer delivers it; its values are kept.">
          no longer delivered
        </span>
      )}
    </span>
  )
}

type CostCenterFilter = 'all' | 'new' | 'not_planned' | 'missing'

export function CostCentersTab({ draft, setDraft, rows, fiscalYear, sourceName }: Props) {
  const [query, setQuery] = useState('')
  const [show, setShow] = useState<CostCenterFilter>('all')
  const sets = draft.account_sets ?? []
  const planned = new Set(draft.planned_cost_centers ?? rows.filter((r) => r.planned).map((r) => r.code))
  // Productive cost centers (ADR 0013, 0027): hours typed per work center, from positions, or both.
  const typed = new Set(draft.capacity_work_centers ?? [])
  const staffed = new Set(draft.capacity_workforce ?? [])
  const positions = draft.workforce_mode === 'positions'
  const leaves = rows.filter((r) => !r.is_group)
  const q = query.trim().toLowerCase()
  const counts: Record<CostCenterFilter, number> = {
    all: leaves.length,
    new: leaves.filter((r) => r.new).length,
    not_planned: leaves.filter((r) => !planned.has(r.code)).length,
    missing: leaves.filter((r) => r.missing).length,
  }
  const matches = (r: ScopeRow) =>
    !r.is_group &&
    (!q || `${r.code} ${r.name}`.toLowerCase().includes(q)) &&
    (show === 'all' ||
      (show === 'new' && r.new) ||
      (show === 'not_planned' && !planned.has(r.code)) ||
      (show === 'missing' && r.missing))
  const shown = filterTree(rows, matches)

  function setPlanned(code: string, on: boolean) {
    const next = on ? [...planned, code] : [...planned].filter((c) => c !== code)
    setDraft({ ...draft, planned_cost_centers: next.sort() })
  }
  function setSource(key: 'capacity_work_centers' | 'capacity_workforce', code: string, on: boolean) {
    const current = key === 'capacity_work_centers' ? typed : staffed
    const next = on ? [...current, code] : [...current].filter((c) => c !== code)
    setDraft({ ...draft, [key]: next.sort() })
  }

  const labels: Record<CostCenterFilter, string> = {
    all: 'All',
    new: 'New',
    not_planned: 'Not planned',
    missing: 'No longer delivered',
  }
  return (
    <section className="su-card sc-card">
      <div className="sc-head">
        <div>
          <h3>Cost centers</h3>
          <p className="su-hint">
            Which cost centers this round plans, the accounts each one plans (its account set, set on a node or a cost
            center; the nearest one above wins), and their capacity: by work centers, by workforce, or both. {planned.size} of {leaves.length} planned.
          </p>
        </div>
        <div className="sc-tools">
          <div className="seg" role="group" aria-label="Show">
            {(Object.keys(labels) as CostCenterFilter[]).map((f) => (
              <button key={f} type="button" aria-pressed={show === f} onClick={() => setShow(f)}>
                {labels[f]} <span className="ink-3">{counts[f]}</span>
              </button>
            ))}
          </div>
          <input className="su-input sc-filter" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter" aria-label="Filter cost centers" />
        </div>
      </div>
      <div className="tbl-scroll">
        <table className="tbl sc-table">
          <thead>
            <tr>
              <th>Cost center</th>
              <th className="sc-switch-col">Planned</th>
              <th className="sc-set-col">Account set</th>
              <th className="sc-switch-col" title="Work center capacity">
                Work centers
              </th>
              <th className="sc-switch-col" title="Workforce capacity">
                Workforce
              </th>
              <th className="num">Actual {fiscalYear - 1}</th>
              {sourceName && <th className="num">{sourceName}</th>}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const { own, inherited } = setOf(sets, rows, r.code)
              const off = !r.is_group && !planned.has(r.code)
              return (
                <tr key={r.code} className={`${r.is_group ? 'node' : ''}${off ? ' sc-off' : ''}`}>
                  <td>
                    <Name row={r} sourceName={sourceName} />
                  </td>
                  <td className="sc-switch-col">
                    {!r.is_group && <Switch on={!off} label={`${r.code} planned`} onChange={(on) => setPlanned(r.code, on)} />}
                  </td>
                  <td className="sc-set-col">
                    <Select
                      variant="compact"
                      label={`Account set of ${r.code}`}
                      value={own ?? ''}
                      options={[
                        {
                          value: '',
                          label: inherited ? `Inherited: ${inherited.set} (${inherited.from})` : 'All accounts',
                        },
                        ...sets.map((s) => ({ value: s.name, label: s.name })),
                      ]}
                      onChange={(name) => setDraft({ ...draft, account_sets: assign(sets, r.code, name || null) })}
                    />
                  </td>
                  <td className="sc-switch-col">
                    {!r.is_group && (
                      <Switch
                        on={typed.has(r.code)}
                        label={`${r.code}: work center capacity`}
                        onChange={(on) => setSource('capacity_work_centers', r.code, on)}
                      />
                    )}
                  </td>
                  <td className="sc-switch-col">
                    {!r.is_group && (
                      <Switch
                        on={staffed.has(r.code)}
                        label={`${r.code}: workforce capacity`}
                        disabled={!positions && !staffed.has(r.code)}
                        title={positions ? undefined : 'Needs workforce planning by positions'}
                        onChange={(on) => setSource('capacity_workforce', r.code, on)}
                      />
                    )}
                  </td>
                  <td className="num">{thousands(r.prior_actuals)}</td>
                  {sourceName && <td className="num">{thousands(r.source_plan)}</td>}
                </tr>
              )
            })}
            {shown.length === 0 && (
              <tr>
                <td colSpan={sourceName ? 7 : 6} className="su-empty">
                  No cost center matches.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="su-hint sc-foot">Figures in thousands. Account sets are defined on the Accounts tab.</p>
    </section>
  )
}

export function AccountsTab({ draft, setDraft, rows, fiscalYear, sourceName }: Props) {
  const [query, setQuery] = useState('')
  const [newSet, setNewSet] = useState('')
  const sets = draft.account_sets ?? []
  const excluded = new Set(draft.not_planned_accounts)
  const q = query.trim().toLowerCase()
  const shown = filterTree(rows, (r) => !q || `${r.code} ${r.name}`.toLowerCase().includes(q))

  function setPlanned(code: string, on: boolean) {
    const next = on ? draft.not_planned_accounts.filter((c) => c !== code) : [...draft.not_planned_accounts, code]
    setDraft({ ...draft, not_planned_accounts: next.sort() })
  }
  function toggle(set: AccountSet, code: string) {
    const accounts = set.accounts.includes(code) ? set.accounts.filter((c) => c !== code) : [...set.accounts, code].sort()
    setDraft({ ...draft, account_sets: sets.map((s) => (s.name === set.name ? { ...s, accounts } : s)) })
  }
  function addSet() {
    const name = newSet.trim()
    if (!name || sets.some((s) => s.name === name)) return
    setDraft({ ...draft, account_sets: [...sets, { name, accounts: [], assigned_to: [] }] })
    setNewSet('')
  }
  function removeSet(name: string) {
    setDraft({ ...draft, account_sets: sets.filter((s) => s.name !== name) })
  }

  return (
    <section className="su-card sc-card">
      <div className="sc-head">
        <div>
          <h3>Accounts</h3>
          <p className="su-hint">
            An account not planned takes no plan values; sheets still show it where it has comparison values, read only,
            so totals stay complete. Account sets list what a kind of cost center plans: tick an account group to include
            every account below it.
          </p>
        </div>
        <div className="sc-tools">
          <input className="su-input sc-filter" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter" aria-label="Filter accounts" />
        </div>
      </div>
      <div className="sc-sets">
        <span className="su-sub">Account sets</span>
        {sets.map((s) => (
          <span className="su-chip" key={s.name}>
            {s.name}
            <span className="ink-3">· {(s.assigned_to ?? []).length ? `on ${(s.assigned_to ?? []).join(', ')}` : 'not assigned'}</span>
            <button type="button" aria-label={`Remove account set ${s.name}`} onClick={() => removeSet(s.name)}>
              ✕
            </button>
          </span>
        ))}
        <form
          className="sc-newset"
          onSubmit={(e) => {
            e.preventDefault()
            addSet()
          }}
        >
          <input className="su-input" value={newSet} onChange={(e) => setNewSet(e.target.value)} placeholder="New set, e.g. Administration" aria-label="New account set" />
          <button type="submit" className="su-btn sm" disabled={!newSet.trim()}>
            Add set
          </button>
        </form>
      </div>
      <div className="tbl-scroll">
        <table className="tbl sc-table">
          <thead>
            <tr>
              <th>Account</th>
              <th className="sc-switch-col">Planned</th>
              {sets.map((s) => (
                <th key={s.name} className="sc-check-col" title={s.name}>
                  {s.name}
                </th>
              ))}
              <th className="num">Actual {fiscalYear - 1}</th>
              {sourceName && <th className="num">{sourceName}</th>}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const off = !r.is_group && excluded.has(r.code)
              return (
                <tr key={r.code} className={`${r.is_group ? 'node' : ''}${off ? ' sc-off' : ''}`}>
                  <td>
                    <Name row={r} sourceName={sourceName} />
                  </td>
                  <td className="sc-switch-col">
                    {!r.is_group && <Switch on={!off} label={`${r.code} planned`} onChange={(on) => setPlanned(r.code, on)} />}
                  </td>
                  {sets.map((s) => {
                    const how = inSet(s, rows, r.code)
                    return (
                      <td key={s.name} className="sc-check-col">
                        <input
                          type="checkbox"
                          aria-label={`${r.code} in ${s.name}`}
                          checked={how !== null}
                          disabled={how === 'group'}
                          title={how === 'group' ? 'Included through its group' : undefined}
                          onChange={() => toggle(s, r.code)}
                        />
                      </td>
                    )
                  })}
                  <td className="num">{thousands(r.prior_actuals)}</td>
                  {sourceName && <td className="num">{thousands(r.source_plan)}</td>}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="su-hint sc-foot">Figures in thousands, summed over all cost centers.</p>
    </section>
  )
}
