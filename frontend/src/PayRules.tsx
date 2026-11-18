import { useCallback, useEffect, useState } from 'react'
import { format, number, parse } from './amounts'
import { api, type Me, type PayRule, type PayRules as Rules, type PayRulesPage } from './api'
import Select from './Select'
import { LIMIT } from './limits'

// The round's personnel cost rules (ADR 0022), for HR and controllers: an account per staff type
// and whether its amounts are typed; yearly extras, raises, long-service awards and
// contributions, each with its account and optionally one entity; the bonus account; and the
// minimum group size below which a total would show someone's pay.

type Kind = PayRule['kind']
const KIND: Record<Kind, { label: string; hint: string }> = {
  extra: { label: 'Yearly extra', hint: 'e.g. a 13th salary or holiday pay, spread over the year' },
  raise: { label: 'Raise', hint: 'a percentage on base pay and extras, from a month' },
  award: { label: 'Long-service award', hint: 'paid in the month someone completes n years' },
  contribution: { label: 'Contribution', hint: 'a rate on gross pay, up to a monthly ceiling' },
}
const KINDS: Kind[] = ['extra', 'raise', 'award', 'contribution']
const MONTH_YEAR = new Intl.DateTimeFormat('en', { month: 'short', year: 'numeric', timeZone: 'UTC' })
const SAFE_MIN = 3

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

/** A rule's terms in one line. */
function terms(r: PayRule): string {
  switch (r.kind) {
    case 'extra': {
      const n = Number(r.salaries)
      return `${n} salar${n === 1 ? 'y' : 'ies'} a year (${number((n * 100) / 12, 2, 2)} % of base pay)`
    }
    case 'raise':
      return `${number(Number(r.percent), 4)} % from ${r.starts ? MONTH_YEAR.format(new Date(r.starts)) : '–'}`
    case 'contribution':
      return `${number(Number(r.percent), 4)} %${r.ceiling ? ` up to ${format(r.ceiling)} a month` : ', no ceiling'}`
    case 'award':
      return r.steps
        .map((s) => `${s.years} y: ${s.salaries ? `${Number(s.salaries)} salar${Number(s.salaries) === 1 ? 'y' : 'ies'}` : format(s.amount ?? null)}`)
        .join(' · ')
  }
}

export default function PayRules({ version, me, onSaved }: { version: string; me: Me; onSaved: () => void }) {
  const [page, setPage] = useState<PayRulesPage | null>(null)
  const [draft, setDraft] = useState<Rules | null>(null)
  const [editing, setEditing] = useState<{ index: number | null; rule: PayRule } | null>(null)
  const [message, setMessage] = useState<{ kind: 'error' | 'ok'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api.payRules(version).then(
      (p) => {
        setPage(p)
        setDraft(p.rules)
      },
      (e: unknown) => setMessage({ kind: 'error', text: errorText(e) }),
    )
  }, [version])
  useEffect(load, [load])

  if (!page || !draft) return message ? <div className="su-note error">{message.text}</div> : null

  const dirty = JSON.stringify(draft) !== JSON.stringify(page.rules)
  const editable = page.on && page.may_edit
  const accounts = page.accounts.map((a) => ({ value: a.code, label: `${a.code} ${a.name}` }))
  const accountName = Object.fromEntries(page.accounts.map((a) => [a.code, a.name]))
  const entityName = Object.fromEntries(page.entities.map((e) => [e.code, e.name]))

  async function save() {
    if (!draft) return
    setBusy(true)
    try {
      const saved = await api.savePayRules(version, draft)
      setPage(saved)
      setDraft(saved.rules)
      setMessage({ kind: 'ok', text: 'Rules saved; every cost center’s totals are recomputed.' })
      onSaved()
    } catch (e) {
      setMessage({ kind: 'error', text: errorText(e) })
    } finally {
      setBusy(false)
    }
  }

  const minLow = draft.min_group < SAFE_MIN

  return (
    <>
      {message && <div className={`su-note ${message.kind === 'error' ? 'error' : 'ok'}`}>{message.text}</div>}

      <section className={`su-card pc-module${page.on ? ' on' : ''}`}>
        <div>
          <strong>{page.on ? 'On' : 'Off'}</strong>
          <span className="su-hint">
            {page.workforce_positions
              ? page.on
                ? 'Positions carry pay; their costs go to the accounts below, on the cost center charged.'
                : 'The controller turns them on in the round’s Setup, step 6, under Workforce planning. Accounts that become personnel accounts are then written by the rules only.'
              : 'They need workforce planning by positions: the controller sets both in the round’s Setup, step 6.'}
          </span>
        </div>
      </section>

      {page.on && (
        <>
          <section className="su-card">
            <div className="fw-card-head">
              <h3>Staff types</h3>
              <span className="su-hint">
                Salaried: pay is calculated with the rules. Amount: a monthly amount is typed per position (freelancers, agency,
                hourly paid), nothing added.
              </span>
            </div>
            <div className="tbl-scroll">
              <table className="tbl pc-types">
                <thead>
                  <tr>
                    <th>Staff type</th>
                    <th>Account</th>
                    <th>Pay</th>
                  </tr>
                </thead>
                <tbody>
                  {draft.staff_types.map((t, i) => {
                    const set = (patch: Partial<typeof t>) =>
                      setDraft({ ...draft, staff_types: draft.staff_types.map((x, j) => (j === i ? { ...x, ...patch } : x)) })
                    return (
                      <tr key={t.code}>
                        <td>
                          <span className="pl-code">{t.code}</span> {t.name}
                          {t.external && <span className="wf-ext">external</span>}
                        </td>
                        <td className="pc-account">
                          <Select
                            label={`Account of ${t.name}`}
                            value={t.account ?? ''}
                            onChange={(v) => set({ account: v || null })}
                            searchable
                            disabled={!editable}
                            options={[{ value: '', label: 'Not costed' }, ...accounts]}
                          />
                        </td>
                        <td>
                          <div className="seg" role="group" aria-label={`Pay of ${t.name}`}>
                            {(
                              [
                                [false, 'Salaried'],
                                [true, 'Amount'],
                              ] as const
                            ).map(([typed, label]) => (
                              <button
                                key={label}
                                type="button"
                                aria-pressed={t.typed === typed}
                                disabled={!editable || !t.account}
                                onClick={() => set({ typed })}
                              >
                                {label}
                              </button>
                            ))}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            {draft.staff_types.some((t) => !t.account) && (
              <p className="su-hint pc-warn">Staff types without an account aren’t costed: their positions add nothing.</p>
            )}
          </section>

          <section className="su-card">
            <div className="fw-card-head">
              <h3>Rules</h3>
              <span className="su-hint">On salaried positions. A rule for one entity applies to its cost centers only.</span>
              <span className="cp-grow" />
              {editable && (
                <span className="btn-row">
                  {KINDS.map((k) => (
                    <button
                      key={k}
                      type="button"
                      className="su-btn sm"
                      title={KIND[k].hint}
                      onClick={() =>
                        setEditing({
                          index: null,
                          rule: {
                            kind: k,
                            name: k === 'extra' ? '13th salary' : '',
                            account: '',
                            entity: null,
                            salaries: k === 'extra' ? '1' : null,
                            percent: null,
                            ceiling: null,
                            starts: k === 'raise' ? page.periods[0] : null,
                            steps: k === 'award' ? [{ years: 10, salaries: '1', amount: null }] : [],
                          },
                        })
                      }
                    >
                      <span className="pl-plus">+</span> {KIND[k].label}
                    </button>
                  ))}
                </span>
              )}
            </div>
            {draft.rules.length === 0 ? (
              <p className="su-hint wf-empty">No rules: salaried positions cost their monthly salary only.</p>
            ) : (
              <div className="tbl-scroll">
                <table className="tbl pc-rules">
                  <thead>
                    <tr>
                      <th>Rule</th>
                      <th>Terms</th>
                      <th>Account</th>
                      <th>Entity</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {draft.rules.map((r, i) => (
                      <tr key={i}>
                        <td>
                          <strong>{r.name}</strong>
                          <span className="muted"> · {KIND[r.kind].label}</span>
                        </td>
                        <td>{terms(r)}</td>
                        <td title={`${r.account} ${accountName[r.account] ?? ''}`}>
                          <span className="pl-code">{r.account}</span> {accountName[r.account] ?? ''}
                        </td>
                        <td className={r.entity ? undefined : 'muted'} title={r.entity ? entityName[r.entity] : undefined}>
                          {r.entity ?? 'all'}
                        </td>
                        <td className="right">
                          {editable && (
                            <span className="btn-row">
                              <button type="button" className="su-btn ghost sm" onClick={() => setEditing({ index: i, rule: r })}>
                                Edit
                              </button>
                              <button
                                type="button"
                                className="su-btn ghost sm danger"
                                onClick={() => setDraft({ ...draft, rules: draft.rules.filter((_, j) => j !== i) })}
                              >
                                Remove
                              </button>
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

          <section className="su-card pc-settings">
            <label className="su-field">
              Bonus account
              <Select
                label="Bonus account"
                value={draft.bonus_account ?? ''}
                onChange={(v) => setDraft({ ...draft, bonus_account: v || null })}
                searchable
                disabled={!editable}
                options={[{ value: '', label: 'No bonus' }, ...accounts]}
              />
              <span className="su-hint">Bonuses are yearly amounts per position, spread over the months worked.</span>
            </label>
            <div className="su-field">
              Review
              <div className="seg" role="group" aria-label="Review">
                {(
                  [
                    [true, 'Before approval'],
                    [false, 'Off'],
                  ] as const
                ).map(([on, label]) => (
                  <button
                    key={label}
                    type="button"
                    aria-pressed={draft.review_required === on}
                    disabled={!editable || !me.is_hr}
                    onClick={() => setDraft({ ...draft, review_required: on })}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <span className="su-hint">
                {draft.review_required
                  ? 'A cost center is approved once HR has confirmed its pay. Set by HR.'
                  : 'Approval doesn’t wait for HR. HR still hands each cost center’s pay over. Set by HR.'}
              </span>
            </div>
            <label className="su-field pc-min">
              Minimum group size
              <input
                className={`su-input num${minLow ? ' pc-low' : ''}`}
                type="number"
                min={1}
                value={draft.min_group}
                disabled={!editable || !me.is_hr}
                onChange={(e) => setDraft({ ...draft, min_group: Math.max(1, Number(e.target.value) || 1) })}
              />
              <span className={`su-hint${minLow ? ' pc-low' : ''}`}>
                {minLow
                  ? `Below ${SAFE_MIN}, a total can show one or two people’s pay to everyone who reads the cost center.`
                  : 'Positions an account on a cost center needs before others see its total. Set by HR.'}
              </span>
            </label>
          </section>

          {editable && (
            <div className="pc-savebar">
              <span className="su-hint">{dirty ? 'Unsaved changes to the rules.' : 'The rules are saved.'}</span>
              <button type="button" className="su-btn ghost" disabled={!dirty || busy} onClick={() => setDraft(page.rules)}>
                Discard
              </button>
              <button type="button" className="su-btn primary" disabled={!dirty || busy} onClick={() => void save()}>
                {busy ? 'Saving…' : 'Save rules'}
              </button>
            </div>
          )}
        </>
      )}

      {editing && (
        <RuleDialog
          rule={editing.rule}
          page={page}
          onClose={() => setEditing(null)}
          onDone={(rule) => {
            const rules = [...draft.rules]
            if (editing.index === null) rules.push(rule)
            else rules[editing.index] = rule
            setDraft({ ...draft, rules })
            setEditing(null)
          }}
        />
      )}
    </>
  )
}

function RuleDialog({
  rule,
  page,
  onClose,
  onDone,
}: {
  rule: PayRule
  page: PayRulesPage
  onClose: () => void
  onDone: (rule: PayRule) => void
}) {
  const [r, setR] = useState<PayRule>(rule)
  // A yearly extra is typed as salaries per year or as a percentage of base pay; it is stored as
  // salaries (ADR 0022): 10.2 % is 1.224 salaries.
  const [extraAs, setExtraAs] = useState<'salaries' | 'percent'>('salaries')
  const [extraText, setExtraText] = useState(rule.salaries ? String(Number(rule.salaries)) : '')
  const number = (s: string | null | undefined) => (s && s.trim() && Number.isFinite(Number(s.replace(',', '.'))) ? Number(s.replace(',', '.')) : null)
  const extra = number(extraText)
  const salaries = extra === null ? null : extraAs === 'salaries' ? extra : (extra * 12) / 100
  const ceiling = r.ceiling ? parse(r.ceiling) : null
  const kind = KIND[r.kind]

  const valid =
    r.name.trim() !== '' &&
    r.account !== '' &&
    (r.kind !== 'extra' || (salaries !== null && salaries > 0)) &&
    (!['raise', 'contribution'].includes(r.kind) || (number(r.percent) ?? 0) > 0) &&
    (r.kind !== 'contribution' || ceiling !== undefined) &&
    (r.kind !== 'award' ||
      (r.steps.length > 0 &&
        r.steps.every((s) => s.years > 0 && ((number(s.salaries) ?? 0) > 0 || (number(s.amount) ?? 0) > 0)) &&
        new Set(r.steps.map((s) => s.years)).size === r.steps.length))

  function done() {
    onDone({
      ...r,
      name: r.name.trim(),
      salaries: r.kind === 'extra' && salaries !== null ? salaries.toFixed(4) : null,
      ceiling: r.kind === 'contribution' && ceiling ? ceiling : null,
    })
  }

  const accounts = page.accounts.map((a) => ({ value: a.code, label: `${a.code} ${a.name}` }))

  return (
    <div className="il-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form
        className="il-dialog pc-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pc-rule-title"
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Escape') onClose()
        }}
        onSubmit={(e) => {
          e.preventDefault()
          if (valid) done()
        }}
      >
        <header className="il-head">
          <div>
            <div className="su-eyebrow">{kind.label}</div>
            <h3 id="pc-rule-title">{r.name.trim() || kind.label}</h3>
            <div className="ink-3 il-sub">{kind.hint}</div>
          </div>
          <button type="button" className="su-icon" aria-label="Close" title="Close (Esc)" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="il-body pc-dialog-body">
          <div className="pc-grid">
            <label className="su-field">
              Name
              <input maxLength={LIMIT.name} className="su-input" value={r.name} autoFocus onChange={(e) => setR({ ...r, name: e.target.value })} />
            </label>
            <div className="su-field">
              Account
              <Select label="Account" value={r.account} onChange={(v) => setR({ ...r, account: v })} searchable placeholder="Choose" options={accounts} />
            </div>
            <div className="su-field">
              Entity
              <Select
                label="Entity"
                value={r.entity ?? ''}
                onChange={(v) => setR({ ...r, entity: v || null })}
                options={[{ value: '', label: 'All entities' }, ...page.entities.map((e) => ({ value: e.code, label: `${e.code} ${e.name}` }))]}
              />
            </div>
          </div>

          {r.kind === 'extra' && (
            <div className="pc-grid pc-extra">
              <div className="su-field">
                Typed as
                <div className="seg" role="group" aria-label="Typed as">
                  {(
                    [
                      ['salaries', 'Salaries per year'],
                      ['percent', '% of base pay'],
                    ] as const
                  ).map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      aria-pressed={extraAs === value}
                      onClick={() => {
                        if (value === extraAs) return
                        if (extra !== null)
                          setExtraText(String(+(value === 'percent' ? (extra * 100) / 12 : (extra * 12) / 100).toFixed(4)))
                        setExtraAs(value)
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              <label className="su-field">
                {extraAs === 'salaries' ? 'Salaries per year' : 'Percent of base pay'}
                <input maxLength={LIMIT.number} className="su-input num" inputMode="decimal" value={extraText} onChange={(e) => setExtraText(e.target.value)} />
                <span className="su-hint">
                  {salaries !== null
                    ? `${+salaries.toFixed(4)} salar${+salaries.toFixed(4) === 1 ? 'y' : 'ies'} a year = ${((salaries * 100) / 12).toFixed(2)} % of base pay, a twelfth each month`
                    : 'e.g. 1 for a 13th salary, 0.5 for half of one; 10.2 % for Norway’s holiday pay'}
                </span>
              </label>
            </div>
          )}

          {(r.kind === 'raise' || r.kind === 'contribution') && (
            <div className="pc-grid">
              <label className="su-field">
                {r.kind === 'raise' ? 'Raise %' : 'Rate %'}
                <input maxLength={LIMIT.number}
                  className="su-input num"
                  inputMode="decimal"
                  value={r.percent ?? ''}
                  onChange={(e) => setR({ ...r, percent: e.target.value || null })}
                />
              </label>
              {r.kind === 'raise' ? (
                <div className="su-field">
                  From
                  <Select
                    label="From"
                    value={r.starts ?? ''}
                    onChange={(v) => setR({ ...r, starts: v })}
                    options={page.periods.map((p) => ({ value: p, label: MONTH_YEAR.format(new Date(p)) }))}
                  />
                  <span className="su-hint">On base pay and yearly extras; several raises compound.</span>
                </div>
              ) : (
                <label className="su-field">
                  Monthly ceiling <span className="ink-3">(optional)</span>
                  <input maxLength={LIMIT.number}
                    className={`su-input num${ceiling === undefined ? ' invalid' : ''}`}
                    inputMode="decimal"
                    value={r.ceiling ?? ''}
                    placeholder="none"
                    onChange={(e) => setR({ ...r, ceiling: e.target.value || null })}
                  />
                  <span className="su-hint">Pay above it bears no contribution, per person and month (pro rata in part months).</span>
                </label>
              )}
            </div>
          )}

          {r.kind === 'award' && (
            <div className="su-field">
              Steps
              <table className="tbl pc-steps">
                <thead>
                  <tr>
                    <th className="num">Years of service</th>
                    <th className="num">Salaries</th>
                    <th className="num">or amount</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {r.steps.map((s, i) => {
                    const set = (patch: Partial<typeof s>) => setR({ ...r, steps: r.steps.map((x, j) => (j === i ? { ...x, ...patch } : x)) })
                    return (
                      <tr key={i}>
                        <td>
                          <input
                            className="su-input num"
                            type="number"
                            min={1}
                            value={s.years}
                            onChange={(e) => set({ years: Number(e.target.value) })}
                          />
                        </td>
                        <td>
                          <input maxLength={LIMIT.number}
                            className="su-input num"
                            inputMode="decimal"
                            value={s.salaries ?? ''}
                            onChange={(e) => set({ salaries: e.target.value || null, amount: e.target.value ? null : s.amount })}
                          />
                        </td>
                        <td>
                          <input maxLength={LIMIT.number}
                            className="su-input num"
                            inputMode="decimal"
                            value={s.amount ?? ''}
                            onChange={(e) => set({ amount: e.target.value || null, salaries: e.target.value ? null : s.salaries })}
                          />
                        </td>
                        <td>
                          <button type="button" className="su-btn ghost sm danger" onClick={() => setR({ ...r, steps: r.steps.filter((_, j) => j !== i) })}>
                            Remove
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              <button
                type="button"
                className="su-btn sm pc-add-step"
                onClick={() => setR({ ...r, steps: [...r.steps, { years: (r.steps.at(-1)?.years ?? 0) + 15, salaries: '1', amount: null }] })}
              >
                <span className="pl-plus">+</span> Step
              </button>
              <span className="su-hint">Paid in the month of the entry date’s anniversary; positions without an entry date get none.</span>
            </div>
          )}
        </div>
        <footer className="il-foot">
          <span className="su-hint">Saved with the rules.</span>
          <button type="button" className="su-btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="su-btn primary" disabled={!valid}>
            Done
          </button>
        </footer>
      </form>
    </div>
  )
}
