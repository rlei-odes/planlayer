import { Fragment } from 'react'
import type { EntryForm, MemberRow } from './api'
import type { Profile, SetupDraft } from './setupTypes'

// A designed mock-up of the sheet a planner gets with the draft setup (E-03): the forms decide
// which cells are typed in, which are calculated and which months are hidden; the comparison
// columns come first. Values are examples, derived from the account code, not real data.

// Example values in thousands, so the account column, comparisons, plan and all twelve months fit
// beside the settings.
const K = new Intl.NumberFormat('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
const FMT = { format: (v: number) => K.format(v / 1000) }
const MONTH = new Intl.DateTimeFormat('en', { month: 'short', timeZone: 'UTC' })

type Props = {
  draft: SetupDraft
  accounts: MemberRow[]
  profiles: Profile[]
  versionNames: Record<string, string>
  roundName: string
  fiscalYear: number
  periods: string[]
  sampleCostCenter: string | null
  actualsAvailable: boolean
}

type Resolved = { form: EntryForm; profile: string | null; reference: string | null }

/** The planning sheet's badge: how a line is entered, then what a year total does there. */
function badge(r: Resolved, fallback: boolean): { full: string; short: string } {
  if (r.form === 'annual') return { full: 'Year only', short: 'Year' }
  const spread =
    fallback || r.form === 'annual_even'
      ? 'even'
      : r.form === 'annual_profile' || (r.form === 'monthly' && r.profile)
        ? 'season'
        : (r.reference ?? 'actuals')
  const warn = fallback ? ' ⚠' : ''
  const entry = r.form === 'monthly' ? ['Monthly', 'M'] : ['Year', 'Y']
  return { full: `${entry[0]} · ${spread}${warn}`, short: `${entry[1]} · ${spread}${warn}` }
}

function hash(text: string): number {
  let h = 2166136261
  for (const ch of text) h = Math.imul(h ^ ch.charCodeAt(0), 16777619)
  return Math.abs(h)
}

/** Example prior-year months for an account: a base size with a gentle season and an August dip. */
function priorYear(code: string): number[] {
  const h = hash(code)
  const base = 800 + (h % 9000)
  return Array.from({ length: 12 }, (_, m) => {
    const season = 1 + 0.18 * Math.sin((m + (h % 5)) / 1.9) + (m === 7 ? -0.25 : 0) + (m === 11 ? 0.2 : 0)
    return Math.round(base * season)
  })
}

const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0)

function spread(total: number, weights: number[]): number[] {
  const w = sum(weights)
  if (w <= 0) return weights.map(() => Math.round(total / weights.length))
  return weights.map((x) => Math.round((total * x) / w))
}

export default function PlannerPreview(p: Props) {
  const byCode = new Map(p.accounts.map((a) => [a.code, a]))
  const rules = new Map(p.draft.entry_forms.map((r) => [r.account_code, r]))
  const hidden = new Set(p.draft.hidden_accounts)

  function resolve(code: string): Resolved {
    for (let c: string | null = code; c; c = byCode.get(c)?.parent ?? null) {
      const r = rules.get(c)
      if (r) return { form: r.form, profile: r.profile_code ?? null, reference: r.reference_version_code ?? null }
    }
    return { form: p.draft.default_form, profile: null, reference: null }
  }

  // A sample sheet: groups that hold accounts, those with their own forms first; three accounts each.
  const leafGroups = new Map<string, MemberRow[]>()
  for (const a of p.accounts) {
    if (a.is_group || !a.parent || hidden.has(a.code)) continue
    leafGroups.set(a.parent, [...(leafGroups.get(a.parent) ?? []), a])
  }
  const distinct = (g: string) =>
    (leafGroups.get(g) ?? []).some((a) => resolve(a.code).form !== p.draft.default_form) ? 0 : 1
  const groups = [...leafGroups.keys()].sort((a, b) => distinct(a) - distinct(b)).slice(0, 4)
  groups.sort((a, b) => p.accounts.findIndex((x) => x.code === a) - p.accounts.findIndex((x) => x.code === b))

  const months = p.periods.length === 12 ? p.periods.map((d) => MONTH.format(new Date(d))) : []
  const profileOf = (code: string | null) => p.profiles.find((x) => x.code === code)

  type Line = {
    code: string
    name: string
    r: Resolved
    compare: number[]
    total: number
    months: number[]
    fallback: boolean
  }
  function line(a: MemberRow): Line {
    const r = resolve(a.code)
    const py = priorYear(a.code)
    const pyTotal = sum(py)
    const total = Math.round((pyTotal * 1.03) / 100) * 100
    const compare = p.draft.comparisons.map((c) =>
      c.kind === 'prior_year_actuals' ? pyTotal : c.kind === 'actuals' ? Math.round(pyTotal * 0.62) : Math.round(pyTotal * 1.04),
    )
    let fallback = false
    let values: number[]
    switch (r.form) {
      case 'monthly':
        values = py.map((v) => Math.round(v * 1.03))
        break
      case 'annual_profile': {
        const prof = profileOf(r.profile)
        values = spread(total, prof ? prof.values.map(Number) : py.map(() => 1))
        break
      }
      case 'annual_reference':
        fallback = !r.reference && !p.actualsAvailable
        values = spread(total, fallback ? py.map(() => 1) : py)
        break
      default:
        values = spread(total, py.map(() => 1))
    }
    return { code: a.code, name: a.name, r, compare, total: r.form === 'monthly' ? sum(values) : total, months: values, fallback }
  }

  const sheet = groups.map((g) => ({ group: byCode.get(g), lines: (leafGroups.get(g) ?? []).slice(0, 3).map(line) }))
  const nComp = p.draft.comparisons.length
  const compareLabel = (i: number) => {
    const c = p.draft.comparisons[i]
    if (c.kind === 'prior_year_actuals') return `Actual ${p.fiscalYear - 1}`
    if (c.kind === 'actuals') return `Actual ${p.fiscalYear} · YTD`
    return p.versionNames[c.version_code ?? ''] ?? c.version_code ?? 'Version'
  }
  const usesActualsWithoutSource =
    !p.actualsAvailable &&
    (p.draft.comparisons.some((c) => c.kind !== 'version') ||
      sheet.some((s) => s.lines.some((l) => l.fallback || (l.r.form === 'monthly' && !l.r.profile && !l.r.reference))))

  return (
    <aside className="pv" aria-label="Planner preview">
      <div className="pv-label">
        <span className="su-eyebrow">
          <i className="pv-live" /> What planners will see
        </span>
        <span className="pv-sample">example values, in thousands</span>
      </div>
      <div className="pv-head">
        <div>
          <div className="pv-cc">{p.sampleCostCenter ?? 'Cost center'}</div>
          <div className="pv-sub">
            {p.roundName} · fiscal year {p.fiscalYear}
          </div>
        </div>
        <div className="pv-pills">
          <span className="pv-pill">In progress</span>
          {p.draft.deadline && <span className="pv-pill neutral">Due {p.draft.deadline}</span>}
          <span className="pv-pill action">Submit for approval</span>
        </div>
      </div>
      <div className="pv-scroll">
        <table className="pv-table" style={{ minWidth: 160 + 48 * (nComp + 1) + 36 * months.length }}>
          <colgroup>
            <col className="acc" />
            {p.draft.comparisons.map((_, i) => (
              <col key={i} className="year" />
            ))}
            <col className="year" />
            {months.map((m) => (
              <col key={m} />
            ))}
          </colgroup>
          <thead>
            <tr>
              <th className="acc">Account</th>
              {p.draft.comparisons.map((_, i) => (
                <th key={i} className="cmp">
                  {compareLabel(i)}
                </th>
              ))}
              <th className="plan">Plan {p.fiscalYear}</th>
              {months.map((m) => (
                <th key={m}>{m}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sheet.map(({ group, lines }) => renderGroup(group?.code ?? '?', group?.name ?? '', lines))}
            {sheet.length === 0 && (
              <tr>
                <td className="acc" colSpan={2 + nComp + 12}>
                  No accounts yet: sync them or add them under Master data.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="pv-notes">
        {nComp === 0 && <div className="su-note">No comparison columns: planners see only their plan.</div>}
        {usesActualsWithoutSource && (
          <div className="su-note warn">
            Some settings use actuals, but no actuals are mapped yet: those year totals spread evenly until a sync brings
            them in.
          </div>
        )}
        {hidden.size > 0 && (
          <div className="su-note">
            {hidden.size} account{hidden.size === 1 ? '' : 's'} hidden from every sheet in this round.
          </div>
        )}
      </div>
      <div className="pv-foot">
        <span className="pv-key">
          <i className="k-edit" /> typed in
        </span>
        <span className="pv-key">
          <i className="k-calc" /> calculated from the year total
        </span>
        <span className="pv-key">
          <i className="k-hidden" /> months not shown
        </span>
        <span className="pv-key">
          <i className="k-cmp" /> comparison
        </span>
      </div>
    </aside>
  )

  function renderGroup(key: string, name: string, lines: Line[]) {
    const compare = Array.from({ length: nComp }, (_, i) => sum(lines.map((l) => l.compare[i])))
    const monthTotals = Array.from({ length: 12 }, (_, m) => sum(lines.map((l) => l.months[m])))
    return (
      <Fragment key={key}>
        <tr className="pv-group">
          <td className="acc">{name}</td>
          {compare.map((v, i) => (
            <td key={i} className={`pv-num pv-cmp${i === nComp - 1 ? ' last' : ''}`}>
              {FMT.format(v)}
            </td>
          ))}
          <td className="pv-num">{FMT.format(sum(lines.map((l) => l.total)))}</td>
          {months.length > 0 && monthTotals.map((v, m) => <td key={m} className="pv-num">{FMT.format(v)}</td>)}
        </tr>
        {lines.map(renderRow)}
      </Fragment>
    )
  }

  function renderRow(l: Line) {
    const prof = l.r.form === 'annual_profile' ? profileOf(l.r.profile) : undefined
    const ref = l.r.reference ? (p.versionNames[l.r.reference] ?? l.r.reference) : 'last year’s actuals'
    const { full, short } = badge(l.r, l.fallback)
    const monthlyBasis = l.r.profile
      ? `spreads by the profile ${profileOf(l.r.profile)?.name ?? l.r.profile}`
      : `spreads like ${ref}`
    const basis =
      l.r.form === 'monthly'
        ? `Months are typed in; a year total ${monthlyBasis}.`
        : l.r.form === 'annual'
          ? 'Only the year total is entered; it is stored as 12 equal months.'
          : l.r.form === 'annual_even'
            ? 'The year total spreads evenly over the months.'
            : l.r.form === 'annual_profile'
              ? `The year total spreads by the profile ${prof?.name ?? '(none chosen)'}.`
              : l.fallback
                ? 'No reference values yet: the year total spreads evenly.'
                : `The year total spreads like ${ref}.`
    return (
      <tr key={l.code} title={`${full}: ${basis}`}>
        <td className="acc">
          <span className="pv-acc">
            <span className="name">
              <span className="code">{l.code}</span>
              {l.name}
            </span>
            {prof && <Bars values={prof.values.map(Number)} />}
            <span className={`pv-form ${l.r.form === 'monthly' ? 'monthly' : ''} ${l.fallback ? 'fallback' : ''}`}>
              <span className="full">{full}</span>
              <span className="short">{short}</span>
            </span>
          </span>
        </td>
        {l.compare.map((v, i) => (
          <td key={i} className={`pv-num pv-cmp${i === nComp - 1 ? ' last' : ''}`}>
            {FMT.format(v)}
          </td>
        ))}
        <td className="pv-num pv-edit total">{FMT.format(l.total)}</td>
        {months.length > 0 &&
          (l.r.form === 'annual' ? (
            <td className="pv-hidden" colSpan={12}>
              entered as a full-year value
            </td>
          ) : (
            l.months.map((v, m) => (
              <td key={m} className={`pv-num ${l.r.form === 'monthly' ? 'pv-edit' : 'pv-calc'}`}>
                {FMT.format(v)}
              </td>
            ))
          ))}
      </tr>
    )
  }
}

export function Bars({ values, height = 14 }: { values: number[]; height?: number }) {
  const max = Math.max(...values, 0) || 1
  return (
    <span className="pv-bars" style={{ height }} aria-hidden="true">
      {values.map((v, i) => (
        <i key={i} style={{ height: `${Math.max(1, (v / max) * height)}px` }} />
      ))}
    </span>
  )
}
