import { useState } from 'react'
import type { Position, PositionIn, Transfer, WorkforceSheet } from './api'
import { span } from './periods'
import Select from './Select'
import Tile from './Tile'
import { FIELD, LIMIT } from './limits'
import { defaultStaffType } from './staffTypes'

// The positions of a cost center in a positions round (ADR 0020): the build-up of heads over the
// year, and the list of positions with their editor, transfers (ADR 0021) and removal.

const MONTH = new Intl.DateTimeFormat('en', { month: 'short', timeZone: 'UTC' })
const MONTH_YEAR = new Intl.DateTimeFormat('en', { month: 'short', year: 'numeric', timeZone: 'UTC' })
const DAY = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })

type Draft = {
  title: string
  person_name: string
  staff_type_code: string
  staff_category_code: string
  entry_date: string
  leave_date: string
  contract_percent: string
  is_plan: boolean
  is_replacement: boolean
  description: string
}

/** A contract % to start with: full time for internal staff, none for external staff (e.g.
 * freelancers, whose capacity is typed in hours, ADR 0027). */
const startingPercent = (external: boolean) => (external ? '' : '100')

function draftOf(p: Position | null, sheet: WorkforceSheet): Draft {
  const type = p?.staff_type_code ?? defaultStaffType(sheet)
  return {
    title: p?.title ?? '',
    person_name: p?.person_name ?? '',
    staff_type_code: type,
    staff_category_code: p?.staff_category_code ?? sheet.staff_categories[0]?.code ?? '',
    entry_date: p?.entry_date ?? '',
    leave_date: p?.leave_date ?? '',
    contract_percent: p?.contract_percent
      ? String(Number(p.contract_percent))
      : p
        ? ''
        : startingPercent(sheet.staff_types.find((t) => t.code === type)?.external ?? false),
    is_plan: p?.is_plan ?? true,
    is_replacement: p?.is_replacement ?? false,
    description: p?.description ?? '',
  }
}

function positionIn(d: Draft): PositionIn {
  const blank = (s: string) => (s.trim() ? s.trim() : null)
  return {
    title: d.title.trim(),
    person_name: blank(d.person_name),
    staff_type_code: d.staff_type_code,
    staff_category_code: d.staff_category_code,
    entry_date: blank(d.entry_date),
    leave_date: blank(d.leave_date),
    contract_percent: blank(d.contract_percent),
    is_plan: d.is_plan,
    is_replacement: d.is_plan && d.is_replacement,
    description: blank(d.description),
  }
}

export default function Positions({
  sheet,
  open,
  saving,
  onTakeHr,
  onSave,
  onDelete,
  onTransfer,
  onWithdraw,
}: {
  sheet: WorkforceSheet
  open: boolean
  saving: boolean
  onTakeHr: () => Promise<boolean>
  onSave: (p: PositionIn, id: number | null) => Promise<boolean>
  onDelete: (p: Position) => Promise<boolean>
  onTransfer: (p: Position) => void
  onWithdraw: (t: Transfer) => void
}) {
  // The position being edited: its id, or "new"; null while none is.
  const [editing, setEditing] = useState<number | 'new' | null>(null)
  const [draft, setDraft] = useState<Draft>(() => draftOf(null, sheet))
  const [filter, setFilter] = useState<'all' | 'plan' | 'staff'>('all')
  const [confirm, setConfirm] = useState<number | null>(null)
  const types = Object.fromEntries(sheet.staff_types.map((t) => [t.code, t]))
  const categories = Object.fromEntries(sheet.staff_categories.map((c) => [c.code, c.name]))
  const b = sheet.build_up
  const months = sheet.periods.map((p) => MONTH.format(new Date(p)))
  const shown = sheet.positions.filter((p) => filter === 'all' || (filter === 'plan') === p.is_plan)
  const external = types[draft.staff_type_code]?.external ?? false
  const percent = draft.contract_percent.trim()
  const valid = draft.title.trim() && draft.staff_type_code && draft.staff_category_code && (external || percent)

  const current = typeof editing === 'number' ? sheet.positions.find((p) => p.position_id === editing) : undefined
  const fromHr = current?.origin === 'hr'

  function edit(p: Position | null) {
    setDraft(draftOf(p, sheet))
    setEditing(p ? p.position_id : 'new')
  }

  const plan = sheet.positions.filter((p) => p.is_plan)
  const total = (xs: number[] | undefined) => (xs ?? []).reduce((a, n) => a + n, 0)
  const end = sheet.periods.length ? MONTH_YEAR.format(new Date(sheet.periods[11])) : ''
  const entering = total(b?.entering)
  const newCount = total(b?.new)
  const replacements = total(b?.replacements)
  const movingIn = total(b?.moving_in)
  const movingOut = total(b?.moving_out)
  const others = entering - newCount - replacements - movingIn
  // Heads, not positions listed: start + entering − leaving = end.
  const tiles: { label: string; value: string; hint: string; tone?: string }[] = [
    { label: 'Start of the year', value: String(b?.start ?? 0), hint: 'heads at the end of the year before' },
    {
      label: 'Entering',
      value: `+${entering}`,
      hint: [
        `${newCount} new`,
        `${replacements} replacement${replacements === 1 ? '' : 's'}`,
        movingIn ? `${movingIn} moving in` : '',
        others ? `${others} hired already` : '',
      ]
        .filter(Boolean)
        .join(' · '),
      tone: 'up',
    },
    {
      label: 'Leaving',
      value: `−${total(b?.leaving)}`,
      hint: movingOut ? `no longer counted · ${movingOut} moving out` : 'no longer counted',
      tone: 'down',
    },
    { label: 'End of the year', value: String(b?.end ?? 0), hint: `heads at the end of ${end}` },
  ]

  return (
    <>
      <section className="su-card wf-buildup">
        <div className="fw-card-head">
          <h3>Build-up</h3>
          <span className="su-hint">
            {plan.length} plan position{plan.length === 1 ? '' : 's'} · headcount changes month to month by entering minus leaving
          </span>
        </div>
        <div className="tiles wf-tiles">
          {tiles.map((t) => (
            <Tile key={t.label} value={t.value} label={t.label} hint={t.hint} tone={t.tone} />
          ))}
        </div>
        {b && (
          <div className="tbl-scroll">
            <table className="tbl wf-months">
              <thead>
                <tr>
                  <th />
                  {months.map((m, i) => (
                    <th key={i} className="num">
                      {m}
                    </th>
                  ))}
                  <th className="num">Year</th>
                </tr>
              </thead>
              <tbody>
                {(
                  [
                    ['Entering', b.entering],
                    ['of them new', b.new],
                    ['of them replacements', b.replacements],
                    ['of them moving in', b.moving_in],
                    ['Leaving', b.leaving],
                    ['of them moving out', b.moving_out],
                  ] as const
                ).map(([label, values]) => (
                  <tr key={label} className={label.startsWith('of') ? 'wf-sub' : undefined}>
                    <td>{label}</td>
                    {values.map((n, i) => (
                      <td key={i} className={`num${n ? '' : ' muted'}`}>
                        {n || '·'}
                      </td>
                    ))}
                    <td className="num">
                      <strong>{values.reduce((a, n) => a + n, 0)}</strong>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="su-card wf-positions">
        <div className="fw-card-head">
          <h3>Positions</h3>
          <div className="seg" role="group" aria-label="Show">
            {(
              [
                ['all', 'All', sheet.positions.length],
                ['staff', 'Staff', sheet.positions.length - plan.length],
                ['plan', 'Plan positions', plan.length],
              ] as const
            ).map(([value, label, count]) => (
              <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>
                {label} <span className="count">{count}</span>
              </button>
            ))}
          </div>
          <span className="cp-grow" />
          {open && sheet.hr_available > 0 && (
            <button type="button" className="su-btn" disabled={saving} onClick={() => void onTakeHr()}>
              Add staff from HR ({sheet.hr_available})
            </button>
          )}
          {open && (
            <button type="button" className="su-btn primary" onClick={() => edit(null)}>
              <span className="pl-plus">+</span> Add position
            </button>
          )}
        </div>

        {editing !== null && (
          <div className="il-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && setEditing(null)}>
          <form
            className="il-dialog wf-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="wf-edit-title"
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Escape') setEditing(null)
            }}
            onSubmit={(e) => {
              e.preventDefault()
              if (!valid) return
              void onSave(positionIn(draft), editing === 'new' ? null : editing).then((ok) => ok && setEditing(null))
            }}
          >
            <header className="il-head">
              <div>
                <div className="su-eyebrow">{editing === 'new' ? 'New position' : 'Edit position'}</div>
                <h3 id="wf-edit-title">
                  {current && <span className="pl-code">{current.code}</span>} {draft.title.trim() || 'Untitled position'}
                </h3>
                <div className="ink-3 il-sub">
                  {sheet.cost_center_code} {sheet.cost_center_name}
                </div>
              </div>
              <button type="button" className="su-icon" aria-label="Close" title="Close (Esc)" onClick={() => setEditing(null)}>
                ×
              </button>
            </header>
            <div className="il-body wf-editor">
            <div className="wf-grid">
              <label className="su-field wf-title">
                Position (title)
                <input maxLength={FIELD.title} className="su-input" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} required autoFocus />
              </label>
              <label className="su-field">
                Name
                <input maxLength={FIELD.person}
                  className="su-input"
                  value={draft.person_name}
                  placeholder={draft.is_plan ? 'vacant' : ''}
                  disabled={fromHr}
                  onChange={(e) => setDraft({ ...draft, person_name: e.target.value })}
                />
              </label>
              <label className="su-field wf-code">
                Number
                <input maxLength={LIMIT.code} className="su-input" value={current?.code ?? 'given on saving'} disabled />
              </label>
              <div className="su-field">
                Staff type
                <Select
                  label="Staff type"
                  value={draft.staff_type_code}
                  onChange={(v) => {
                    // A contract % still at the starting value follows the staff type.
                    const starting = draft.contract_percent === startingPercent(external)
                    const next = types[v]?.external ?? false
                    setDraft({
                      ...draft,
                      staff_type_code: v,
                      contract_percent: starting ? startingPercent(next) : draft.contract_percent,
                    })
                  }}
                  options={sheet.staff_types.map((t) => ({ value: t.code, label: t.name, hint: t.external ? 'external' : undefined }))}
                />
              </div>
              <div className="su-field">
                Category
                <Select
                  label="Category"
                  value={draft.staff_category_code}
                  searchable
                  onChange={(v) => setDraft({ ...draft, staff_category_code: v })}
                  options={sheet.staff_categories.map((c) => ({ value: c.code, label: c.name }))}
                />
              </div>
              <label className="su-field wf-pct">
                Contract %{external ? ' (optional)' : ''}
                <input maxLength={LIMIT.number}
                  className="su-input"
                  inputMode="decimal"
                  value={draft.contract_percent}
                  onChange={(e) => setDraft({ ...draft, contract_percent: e.target.value })}
                  required={!external}
                />
              </label>
              <label className="su-field">
                Entry date
                <input
                  className="su-input"
                  type="date"
                  value={draft.entry_date}
                  disabled={fromHr}
                  onChange={(e) => setDraft({ ...draft, entry_date: e.target.value })}
                />
              </label>
              <label className="su-field">
                Leave date
                <input className="su-input" type="date" value={draft.leave_date} onChange={(e) => setDraft({ ...draft, leave_date: e.target.value })} />
              </label>
              <div className="su-field wf-flags">
                <label className={`md-check${fromHr ? ' wf-off' : ''}`}>
                  <input
                    type="checkbox"
                    checked={draft.is_plan}
                    disabled={fromHr}
                    onChange={(e) => setDraft({ ...draft, is_plan: e.target.checked, is_replacement: e.target.checked && draft.is_replacement })}
                  />
                  Plan position (still to fill)
                </label>
                <label className={`md-check${draft.is_plan ? '' : ' wf-off'}`}>
                  <input
                    type="checkbox"
                    disabled={!draft.is_plan}
                    checked={draft.is_plan && draft.is_replacement}
                    onChange={(e) => setDraft({ ...draft, is_replacement: e.target.checked })}
                  />
                  Replaces a leaver
                </label>
              </div>
              <label className="su-field wf-desc">
                Description
                <textarea maxLength={FIELD.text}
                  className="su-input"
                  rows={2}
                  value={draft.description}
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                />
              </label>
            </div>
            </div>
            <footer className="il-foot">
              <span className="su-hint">
                {fromHr
                  ? 'Taken from HR: number, name and entry date are HR’s. Plan its leaving with the leave date.'
                  : 'Blank dates mean “before the year” and “stays”. A position counts in a month when active on its last day.'}
              </span>
              <button type="button" className="su-btn ghost" onClick={() => setEditing(null)}>
                Cancel
              </button>
              <button type="submit" className="su-btn primary" disabled={!valid || saving}>
                {editing === 'new' ? 'Add position' : 'Save position'}
              </button>
            </footer>
          </form>
          </div>
        )}

        {sheet.positions.length === 0 ? (
          <p className="su-hint wf-empty">
            No positions yet.{' '}
            {open && (sheet.hr_available > 0 ? 'Start from HR’s staff list, or add them one by one.' : 'Add them one by one.')}
          </p>
        ) : (
          <div className="tbl-scroll">
            <table className="tbl wf-table">
              <thead>
                <tr>
                  <th>Position</th>
                  <th>Name</th>
                  <th>Type · category</th>
                  <th>Entry</th>
                  <th>Leave</th>
                  <th className="num">Contract</th>
                  <th>Counted</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {shown.map((p) => (
                  <tr key={p.position_id} className={editing === p.position_id ? 'fw-current' : undefined}>
                    <td>
                      <span className="wf-pos">
                        <span className="code">{p.code}</span>
                        <strong>{p.title}</strong>
                        {p.is_plan && (
                          <span className={`su-pill ${p.is_replacement ? 'submitted' : 'in_progress'}`}>
                            {p.is_replacement ? 'Replacement' : 'New'}
                          </span>
                        )}
                        {p.transferred_from && (
                          <span className="su-pill neutral" title={`Transferred here from ${p.transferred_from}`}>
                            from {p.transferred_from}
                          </span>
                        )}
                        {p.transferred_to && (
                          <span className="su-pill approved" title={`Transferred to ${p.transferred_to}; it stays as it was transferred`}>
                            Transferred to {p.transferred_to}
                          </span>
                        )}
                      </span>
                      {p.transfer && (
                        <span className="tr-pending">
                          <span className="su-pill submitted">Transfer pending</span>
                          to {p.transfer.to_code}
                          {p.transfer.starts ? ` from ${DAY.format(new Date(p.transfer.starts))}` : ', whole year'} · waits for{' '}
                          {p.transfer.to_code} to accept
                          {p.transfer.can_cancel && (
                            <button type="button" className="tr-link" onClick={() => onWithdraw(p.transfer!)}>
                              Withdraw
                            </button>
                          )}
                        </span>
                      )}
                      {p.description && <span className="wf-note">{p.description}</span>}
                    </td>
                    <td className={p.person_name ? undefined : 'muted'}>{p.person_name ?? 'vacant'}</td>
                    <td>
                      {types[p.staff_type_code]?.name ?? p.staff_type_code}
                      <span className="muted"> · {categories[p.staff_category_code] ?? p.staff_category_code}</span>
                    </td>
                    <td className="wf-date">{p.entry_date ? DAY.format(new Date(p.entry_date)) : <span className="muted">before</span>}</td>
                    <td className="wf-date">{p.leave_date ? DAY.format(new Date(p.leave_date)) : <span className="muted">stays</span>}</td>
                    <td className="num">{p.contract_percent ? `${Number(p.contract_percent)} %` : <span className="muted">–</span>}</td>
                    <td>
                      <span
                        className="wf-dots"
                        aria-label={p.counted.every(Boolean) ? 'All year' : `${p.counted.filter(Boolean).length} months`}
                        title={[
                          p.counted.some(Boolean)
                            ? `Counted ${p.counted.every(Boolean) ? 'all year' : `here ${runOf(p.counted, sheet.periods)}`}`
                            : 'Not counted in this year',
                          p.elsewhere.some(Boolean)
                            ? `at ${p.transferred_to ?? p.transferred_from} ${runOf(p.elsewhere, sheet.periods)} (hatched)`
                            : '',
                        ]
                          .filter(Boolean)
                          .join(', ')}
                      >
                        {p.counted.map((c, i) => (
                          <i key={i} className={c ? 'on' : p.elsewhere[i] ? 'moved' : undefined} />
                        ))}
                      </span>
                    </td>
                    <td className="right">
                      {open && (
                        <span className="btn-row">
                          {!p.transferred_to && (
                            <button type="button" className="su-btn ghost sm" onClick={() => edit(p)}>
                              Edit
                            </button>
                          )}
                          {p.transferable && (
                            <button type="button" className="su-btn ghost sm" onClick={() => onTransfer(p)}>
                              Transfer
                            </button>
                          )}
                          {p.transferred_to ? null : !p.removable ? (
                            <span className="wf-hr" title="Taken from HR: only a controller removes it. Plan its leaving with the leave date.">
                              HR
                            </span>
                          ) : confirm === p.position_id ? (
                            <button
                              type="button"
                              className="su-btn sm danger"
                              onClick={() => void onDelete(p).then(() => setConfirm(null))}
                              onBlur={() => setConfirm(null)}
                            >
                              Delete?
                            </button>
                          ) : (
                            <button type="button" className="su-btn ghost sm danger" onClick={() => setConfirm(p.position_id)}>
                              Delete
                            </button>
                          )}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  )
}

/** "Mar–Dec 2026": the months a position counts in, first to last. */
function runOf(counted: boolean[], periods: string[]): string {
  const first = counted.indexOf(true)
  const lastMonth = counted.lastIndexOf(true)
  return span(periods, first, lastMonth)
}

