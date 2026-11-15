import { useCallback, useEffect, useState } from 'react'
import { format, parse, sum } from './amounts'
import { api, STATUS_LABEL, type Me, type PayPage, type PayPosition, type PayUnit } from './api'
import PayRules from './PayRules'
import Select from './Select'

// Personnel costs (ADR 0022). Pay is read by HR and by the cost center's owner as the source
// names them; nobody else. The page shows a cost center's positions with HR's salary next to the
// planned one, and the totals the rules write to its personnel accounts. HR confirms the pay,
// charges positions of small groups to other cost centers, and keeps the rules with controllers.

const MONTH = new Intl.DateTimeFormat('en', { month: 'short', timeZone: 'UTC' })
const DAY = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })

type Tab = 'pay' | 'rules'
type Message = { kind: 'error' | 'ok'; text: string }

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

const money = (v: string | null) => (v === null ? '' : format(v))

export default function PersonnelPage({
  version,
  unit: chosen,
  onUnit,
  me,
}: {
  version: string
  unit: string
  onUnit: (code: string) => void
  me: Me
}) {
  const [tab, setTab] = useState<Tab>(me.reads_pay ? 'pay' : 'rules')
  const [units, setUnits] = useState<PayUnit[] | null>(null)
  const [page, setPage] = useState<PayPage | null>(null)
  const [missing, setMissing] = useState(false)
  const [message, setMessage] = useState<Message | null>(null)
  const [editing, setEditing] = useState<PayPosition | null>(null)
  const [busy, setBusy] = useState(false)
  const unit = units?.some((u) => u.code === chosen) ? chosen : (units?.[0]?.code ?? '')
  const showError = useCallback((e: unknown) => setMessage({ kind: 'error', text: errorText(e) }), [])

  const loadUnits = useCallback(() => {
    if (!me.reads_pay) return
    api.payUnits(version).then(
      (u) => {
        setUnits(u)
        setMissing(false)
      },
      (e: unknown) => {
        if (errorText(e).includes('no personnel costs')) setMissing(true)
        else showError(e)
      },
    )
  }, [version, me.reads_pay, showError])
  useEffect(loadUnits, [loadUnits])

  useEffect(() => {
    if (unit && unit !== chosen) onUnit(unit)
  }, [unit, chosen, onUnit])

  const load = useCallback(() => {
    if (!unit || tab !== 'pay') return
    api.payPage(version, unit).then(setPage, showError)
  }, [version, unit, tab, showError])
  useEffect(load, [load])

  async function review(confirmed: boolean) {
    if (!page) return
    setBusy(true)
    try {
      setPage(await api.reviewPay(version, page.cost_center_code, confirmed))
      setMessage({ kind: 'ok', text: confirmed ? `Pay of ${page.cost_center_code} confirmed.` : 'Confirmation taken back.' })
      loadUnits()
    } catch (e) {
      showError(e)
    } finally {
      setBusy(false)
    }
  }

  // Two views: pay (HR, owners) and the rules (HR, controllers). The switch shows only to those
  // who have both; everyone else sees their one view without it.
  const both = me.reads_pay && (me.is_hr || me.is_controller)
  const tabs = both ? (
    <div className="seg pc-tabs" role="tablist" aria-label="Personnel costs">
      <button type="button" role="tab" aria-selected={tab === 'pay'} aria-pressed={tab === 'pay'} onClick={() => setTab('pay')}>
        Pay by cost center
      </button>
      <button type="button" role="tab" aria-selected={tab === 'rules'} aria-pressed={tab === 'rules'} onClick={() => setTab('rules')}>
        Rules of the round
      </button>
    </div>
  ) : null

  if (tab === 'rules') {
    return (
      <div className="su pc">
        <div className="pl-head">
          <div className="pl-title">
            <div className="su-eyebrow">Personnel costs</div>
            <h1 className="pc-h1">Rules of the round</h1>
            <div className="pl-sub">Accounts, yearly extras, raises, awards and contributions. They hold no one’s pay.</div>
          </div>
          <div className="pl-actions">{tabs}</div>
        </div>
        <PayRules version={version} me={me} onSaved={() => loadUnits()} />
      </div>
    )
  }

  if (missing) {
    return (
      <div className="su pc">
        {tabs}
        <div className="su-card empty-state">
          <h3>No personnel costs in this round</h3>
          <p className="su-hint">
            They come from positions: the controller turns them on in the round’s Setup, step 6, together with workforce
            planning by positions.
          </p>
        </div>
      </div>
    )
  }
  if (units === null) return message ? <div className="su-note error">{message.text}</div> : null
  if (units.length === 0) {
    return (
      <div className="su pc">
        {tabs}
        <div className="su-card empty-state">
          <h3>No pay to show</h3>
          <p className="su-hint">Pay is read by HR, and by each cost center’s owner as the source names them.</p>
        </div>
      </div>
    )
  }

  const current = units.find((u) => u.code === unit)
  const months = page?.periods.map((p) => MONTH.format(new Date(p))) ?? []
  const smallCount = page?.accounts.filter((a) => a.small !== null).length ?? 0
  const total = page ? sum(page.accounts.flatMap((a) => a.months)) : null

  return (
    <div className="su pc">
      <div className="pl-head">
        <div className="pl-title">
          <div className="su-eyebrow">Personnel costs{page ? ` · ${page.round_name}` : ''}</div>
          <Select
            variant="title"
            label="Cost center"
            value={unit}
            onChange={(code) => {
              onUnit(code)
              setMessage(null)
            }}
            searchable
            options={units.map((u) => ({
              value: u.code,
              label: `${u.code} ${u.name}`,
              hint: [
                STATUS_LABEL[u.status],
                `${u.positions} position${u.positions === 1 ? '' : 's'}`,
                u.small_groups ? `${u.small_groups} small` : '',
                u.confirmed ? 'confirmed' : '',
              ]
                .filter(Boolean)
                .join(' · '),
              dot: u.status,
            }))}
          />
          <div className="pl-sub">
            Pay per position and the totals on the personnel accounts · fiscal year {page?.fiscal_year ?? ''}
            {busy && <span className="pl-saving"> · Saving…</span>}
          </div>
        </div>
        <div className="pl-actions">
          {tabs}
          {current && <span className={`su-pill ${current.status}`}>{STATUS_LABEL[current.status]}</span>}
        </div>
      </div>

      {message && <div className={`su-note ${message.kind === 'error' ? 'error' : 'ok'}`}>{message.text}</div>}
      {page?.lock_reason && <div className="su-note">{lockText(page)}</div>}

      {page && (
        <section className={`su-card pc-review${page.review ? ' done' : ''}`}>
          <div>
            <strong>{page.review ? 'Confirmed by HR' : 'Waiting for HR’s review'}</strong>
            <span className="su-hint">
              {page.review
                ? `${page.review.confirmed_by}, ${DAY.format(new Date(page.review.confirmed_at))}. A change by anyone else withdraws it.`
                : 'The cost center can be approved once HR has confirmed its pay. Planners submit without it.'}
            </span>
          </div>
          {page.may_review && (
            <button
              type="button"
              className={`su-btn${page.review ? ' ghost' : ' primary'}`}
              disabled={busy}
              onClick={() => void review(!page.review)}
            >
              {page.review ? 'Take confirmation back' : 'Confirm pay'}
            </button>
          )}
        </section>
      )}

      {page && smallCount > 0 && (
        <div className="su-note pc-small">
          <strong>
            {smallCount} small group{smallCount === 1 ? '' : 's'}:
          </strong>{' '}
          fewer than {page.min_group} positions write to {smallCount === 1 ? 'an account' : 'accounts'} here, so the total would
          show someone’s pay. Others who read this cost center don’t see {smallCount === 1 ? 'it' : 'them'}, and the round can’t
          be closed or published until HR charges the positions to another cost center.
        </div>
      )}

      {page && (
        <section className="su-card">
          <div className="fw-card-head">
            <h3>Positions</h3>
            <span className="su-hint">HR’s salary as taken from the staff list; the planned salary replaces it where typed.</span>
          </div>
          {page.positions.length === 0 ? (
            <p className="su-hint wf-empty">No positions on this cost center yet. They are listed on the Workforce page.</p>
          ) : (
            <div className="tbl-scroll">
              <table className="tbl pc-table">
                <thead>
                  <tr>
                    <th>Position</th>
                    <th>Name</th>
                    <th>Type · category</th>
                    <th>Dates</th>
                    <th className="num">HR’s salary</th>
                    <th className="num">Planned salary</th>
                    <th className="num">Bonus / year</th>
                    <th>Charged to</th>
                    <th className="num">Cost {page.fiscal_year}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {page.positions.map((p) => (
                    <tr key={p.position_id}>
                      <td>
                        <span className="wf-pos">
                          <span className="code">{p.code}</span>
                          <strong>{p.title}</strong>
                          {p.is_plan && <span className="su-pill in_progress">Plan</span>}
                          {p.typed && <span className="su-pill neutral" title="Its amount is typed: no extras, raises or contributions">Amount</span>}
                          {p.typed === null && (
                            <span className="su-pill warn" title="Its staff type has no account in the rules: not costed">
                              Not costed
                            </span>
                          )}
                        </span>
                      </td>
                      <td className={p.person_name ? undefined : 'muted'}>{p.person_name ?? 'vacant'}</td>
                      <td>
                        <span className="pc-kind" title={`${p.staff_type} · ${p.staff_category}`}>
                          {p.staff_type}
                          <small>{p.staff_category}</small>
                        </span>
                      </td>
                      <td className="wf-date">
                        <span className="pc-kind">
                          {p.entry_date ? DAY.format(new Date(p.entry_date)) : <span className="muted">before the year</span>}
                          <small>{p.leave_date ? `to ${DAY.format(new Date(p.leave_date))}` : 'stays'}</small>
                        </span>
                      </td>
                      <td className="num muted">{money(p.hr_salary) || '–'}</td>
                      <td className="num">
                        {p.salary !== null ? <strong>{money(p.salary)}</strong> : <span className="muted">as HR</span>}
                      </td>
                      <td className="num">{money(p.bonus) || <span className="muted">–</span>}</td>
                      <td>{p.charged_to ? <span className="su-pill neutral">{p.charged_to}</span> : <span className="muted">here</span>}</td>
                      <td className="num">{money(p.year_cost)}</td>
                      <td className="right">
                        {!page.lock_reason && !p.transferred_to && (
                          <button type="button" className="su-btn ghost sm" onClick={() => setEditing(p)}>
                            Edit
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {page && (
        <section className="su-card">
          <div className="fw-card-head">
            <h3>Totals on the cost center</h3>
            <span className="su-hint">
              Written to the cost center’s sheet, read only, by the round’s rules · {format(total) || '0.00'} for the year
            </span>
          </div>
          {page.accounts.length === 0 ? (
            <p className="su-hint wf-empty">Nothing is charged to this cost center yet.</p>
          ) : (
            <div className="tbl-scroll">
              <table className="tbl wf-months pc-totals">
                <thead>
                  <tr>
                    <th>Account</th>
                    {months.map((m, i) => (
                      <th key={i} className={`num${i < page.closed_months ? ' muted' : ''}`}>
                        {m}
                      </th>
                    ))}
                    <th className="num">Year</th>
                  </tr>
                </thead>
                <tbody>
                  {page.accounts.map((a) => (
                    <tr key={a.code} className={a.small !== null ? 'pc-small-row' : undefined}>
                      <td title={`${a.code} ${a.name}`}>
                        <span className="pl-code">{a.code}</span> {a.name}
                        {a.small !== null && (
                          <span className="su-pill warn" title={`Only ${a.small} position${a.small === 1 ? '' : 's'}; hidden from other readers`}>
                            small group · {a.small}
                          </span>
                        )}
                      </td>
                      {a.months.map((v, i) => (
                        <td key={i} className={`num${v === null ? ' muted' : ''}`}>
                          {v === null ? '·' : format(v)}
                        </td>
                      ))}
                      <td className="num">
                        <strong>{format(sum(a.months))}</strong>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {editing && page && (
        <PayDialog
          page={page}
          position={editing}
          canCharge={page.may_charge}
          onClose={() => setEditing(null)}
          onSave={async (salary, bonus, charged) => {
            setBusy(true)
            try {
              const updated = await api.savePay(version, page.cost_center_code, editing.position_id, {
                salary,
                bonus,
                charged_to: charged,
              })
              setPage(updated)
              setEditing(null)
              setMessage({ kind: 'ok', text: `${editing.code} saved; the totals follow.` })
              loadUnits()
              return null
            } catch (e) {
              return errorText(e)
            } finally {
              setBusy(false)
            }
          }}
        />
      )}
    </div>
  )
}

/** Why the page is read only, in the reader's terms. */
function lockText(page: PayPage): string {
  if (page.round_status === 'setup')
    return 'This round is still being set up. HR prepares pay meanwhile; you can change it once the controller opens the round.'
  if (page.round_status !== 'open') return `This round is ${page.round_status}: pay is shown as it was planned and can’t change.`
  if (page.status === 'submitted' || page.status === 'approved')
    return `${page.cost_center_code} is ${STATUS_LABEL[page.status].toLowerCase()}: pay can change again once the cost center is reopened.`
  return `Read only: ${page.lock_reason}.`
}

function PayDialog({
  page,
  position: p,
  canCharge,
  onClose,
  onSave,
}: {
  page: PayPage
  position: PayPosition
  canCharge: boolean
  onClose: () => void
  onSave: (salary: string | null, bonus: string | null, charged: string | null) => Promise<string | null>
}) {
  const [salary, setSalary] = useState(p.salary ? format(p.salary) : '')
  const [bonus, setBonus] = useState(p.bonus ? format(p.bonus) : '')
  const [charged, setCharged] = useState(p.charged_to ?? '')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const salaryValue = parse(salary)
  const bonusValue = parse(bonus)
  const valid = salaryValue !== undefined && bonusValue !== undefined
  const typed = p.typed === true

  async function submit() {
    if (!valid) return
    setBusy(true)
    setError(await onSave(salaryValue, bonusValue, charged || null))
    setBusy(false)
  }

  return (
    <div className="il-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form
        className="il-dialog pc-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pc-title"
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Escape') onClose()
        }}
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <header className="il-head">
          <div>
            <div className="su-eyebrow">Pay of a position</div>
            <h3 id="pc-title">
              <span className="pl-code">{p.code}</span> {p.title}
              {p.person_name && <span className="ink-3"> · {p.person_name}</span>}
            </h3>
            <div className="ink-3 il-sub">
              {page.cost_center_code} {page.cost_center_name} · {p.staff_type} · {p.staff_category}
              {p.contract_percent && ` · ${Number(p.contract_percent)} %`}
            </div>
          </div>
          <button type="button" className="su-icon" aria-label="Close" title="Close (Esc)" onClick={onClose}>
            ×
          </button>
        </header>
        {error && <div className="su-note error">{error}</div>}
        <div className="il-body pc-dialog-body">
          <div className="pc-compare">
            <div>
              <span className="su-eyebrow">From HR</span>
              <strong>{p.hr_salary ? format(p.hr_salary) : '–'}</strong>
              <small>{p.hr_salary ? 'as taken from the staff list; stays as it is' : 'not in HR’s list'}</small>
            </div>
            <label className="su-field">
              {typed ? 'Monthly amount' : 'Planned monthly salary'}
              <input
                className={`su-input num${salaryValue === undefined ? ' invalid' : ''}`}
                inputMode="decimal"
                value={salary}
                placeholder={p.hr_salary ? `${format(p.hr_salary)} (HR’s)` : '0.00'}
                autoFocus
                onChange={(e) => setSalary(e.target.value)}
              />
              <span className="su-hint">
                Blank: {p.hr_salary ? 'HR’s salary' : '0'}. What the person is paid a month, at their contract percentage.
              </span>
            </label>
          </div>
          {!typed && (
            <label className="su-field">
              Bonus for the year
              <input
                className={`su-input num${bonusValue === undefined ? ' invalid' : ''}`}
                inputMode="decimal"
                value={bonus}
                placeholder="none"
                onChange={(e) => setBonus(e.target.value)}
              />
              <span className="su-hint">Spread over the months the position is worked, on the round’s bonus account.</span>
            </label>
          )}
          {canCharge && (
            <div className="su-field">
              Costs charged to
              <Select
                label="Costs charged to"
                value={charged}
                onChange={setCharged}
                searchable
                options={[
                  { value: '', label: `${page.cost_center_code} ${page.cost_center_name} (its own)` },
                  ...page.charge_targets
                    .filter((t) => t.code !== page.cost_center_code)
                    .map((t) => ({ value: t.code, label: `${t.code} ${t.name}` })),
                ]}
              />
              <span className="su-hint">
                HR only. Charge a small group’s positions to a larger cost center, e.g. the department’s, so no total shows
                one person’s pay.
              </span>
            </div>
          )}
        </div>
        <footer className="il-foot">
          <span className="su-hint">Saving recomputes the totals; a change by anyone but HR withdraws HR’s confirmation.</span>
          <button type="button" className="su-btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="su-btn primary" disabled={!valid || busy}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </footer>
      </form>
    </div>
  )
}
