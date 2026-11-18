import { detail } from './amounts'
import type { PayPage, PayPosition, PayRuleRef } from './api'

// A position's costs over the year by where they come from (ADR 0026): its salary or typed
// amount, each rule of the round, and its bonus. HR excludes the position from a rule here;
// everyone who reads the pay sees which rules apply and what they add.

const KIND: Record<PayRuleRef['kind'], string> = {
  extra: 'Yearly extra',
  raise: 'Raise',
  award: 'Long-service award',
  contribution: 'Contribution',
}

export default function PayBreakdown({
  page,
  position: p,
  excluded,
  onExclude,
  pending,
}: {
  page: PayPage
  position: PayPosition // as calculated: saved, or the preview of the dialog's values
  excluded: number[]
  onExclude: ((rule: number, on: boolean) => void) | null // null: not HR
  pending: boolean
}) {
  const part = (source: 'pay' | 'bonus' | 'rule', rule: number | null = null) =>
    p.parts.find((x) => x.source === source && x.rule_id === rule)?.year ?? null
  const amount = (v: string | null) => (v === null ? <span className="muted">–</span> : detail(v))

  if (p.typed === null) {
    return <p className="su-hint">Not costed: its staff type has no account in the rules.</p>
  }
  return (
    <div className={`pc-breakdown${pending ? ' pending' : ''}`}>
      <table className="tbl">
        <thead>
          <tr>
            <th>Part</th>
            <th>Account</th>
            <th className="num">Year {page.fiscal_year}</th>
            {onExclude && !p.typed && <th className="pc-applies">Applies</th>}
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>
              <strong>{p.typed ? 'Amount' : 'Salary'}</strong>
              <small>pro rata by days worked</small>
            </td>
            <td className="pl-code">{p.salary_account}</td>
            <td className="num">{amount(part('pay'))}</td>
            {onExclude && !p.typed && <td />}
          </tr>
          {!p.typed &&
            page.rules.map((r) => {
              const other = r.entity !== null && r.entity !== p.entity
              const off = excluded.includes(r.id)
              const why = other ? `for ${r.entity} only` : off ? 'excluded by HR' : null
              return (
                <tr key={r.id} className={other || off ? 'pc-off' : undefined}>
                  <td>
                    <strong>{r.name}</strong>
                    <small>
                      {KIND[r.kind]}
                      {why && ` · ${why}`}
                    </small>
                  </td>
                  <td className="pl-code">{r.account}</td>
                  <td className="num">{other || off ? <span className="muted">–</span> : amount(part('rule', r.id))}</td>
                  {onExclude && (
                    <td className="pc-applies">
                      {!other && (
                        <input
                          type="checkbox"
                          aria-label={`${r.name} applies`}
                          checked={!off}
                          onChange={(e) => onExclude(r.id, !e.target.checked)}
                        />
                      )}
                    </td>
                  )}
                </tr>
              )
            })}
          {!p.typed && page.bonus_account && (
            <tr>
              <td>
                <strong>Bonus</strong>
                <small>spread over months worked</small>
              </td>
              <td className="pl-code">{page.bonus_account}</td>
              <td className="num">{amount(part('bonus'))}</td>
              {onExclude && <td />}
            </tr>
          )}
        </tbody>
        <tfoot>
          <tr>
            <td>
              <strong>Cost of the position</strong>
            </td>
            <td />
            <td className="num">
              <strong>{detail(p.year_cost)}</strong>
            </td>
            {onExclude && !p.typed && <td />}
          </tr>
        </tfoot>
      </table>
      {p.typed && <p className="su-hint">Typed amount: no rules or bonus apply.</p>}
    </div>
  )
}
