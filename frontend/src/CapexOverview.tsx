import { Fragment, useEffect, useState } from 'react'
import { display, fromCents, numberFormat, toCents } from './amounts'
import { api, type CapexOverview as Overview, type CapexSums } from './api'
import { monthLabel } from './capex'
import { SheetScale } from './NumberFormat'
import Tile from './Tile'

// The overview of a round's investments (ADR 0028): amounts by priority and confirmation, per
// area of the cost center hierarchy or per investment type, and the cash out per period. Over
// what the user reads: an approver sees their area, a controller or the CFO the whole round.

type By = 'area' | 'type'

const PRIORITIES = [1, 2, 3] as const

function add(...values: string[]): string {
  return fromCents(values.reduce((s, v) => s + toCents(v), 0n))
}

function totals(s: CapexSums) {
  const confirmed = add(...s.by_priority.map((p) => p.confirmed))
  const open = add(...s.by_priority.map((p) => p.open))
  const declined = add(...s.by_priority.map((p) => p.declined))
  return { confirmed, open, declined, total: add(confirmed, open) }
}

function Cells({ s, show }: { s: CapexSums; show: (v: string) => string }) {
  const t = totals(s)
  return (
    <>
      {s.by_priority.map((p, i) => (
        <Fragment key={i}>
          <td className="num cx-ok">{show(p.confirmed)}</td>
          <td className="num">{show(p.open)}</td>
        </Fragment>
      ))}
      <td className="num cx-ok cx-sep">{show(t.confirmed)}</td>
      <td className="num">{show(t.open)}</td>
      <td className="num strong">{show(t.total)}</td>
      <td className="num ink-3 cx-sep">{show(t.declined)}</td>
    </>
  )
}

function Head({ first }: { first: string }) {
  return (
    <thead>
      <tr>
        <th rowSpan={2}>{first}</th>
        {PRIORITIES.map((p) => (
          <th key={p} colSpan={2} className="cx-group">
            Priority {p}
          </th>
        ))}
        <th colSpan={3} className="cx-group cx-sep">
          All
        </th>
        <th rowSpan={2} className="num cx-sep">
          Declined
        </th>
      </tr>
      <tr>
        {PRIORITIES.map((p) => (
          <Fragment key={p}>
            <th className="num">Confirmed</th>
            <th className="num">Open</th>
          </Fragment>
        ))}
        <th className="num cx-sep">Confirmed</th>
        <th className="num">Open</th>
        <th className="num">Total</th>
      </tr>
    </thead>
  )
}

export default function CapexOverview({ version, onWaiting }: { version: string; onWaiting: () => void }) {
  const [o, setO] = useState<Overview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [by, setBy] = useState<By>('area')
  // Nodes whose rows below are hidden; by default the areas show down to departments.
  const [closed, setClosed] = useState<Set<string> | null>(null)

  useEffect(() => {
    api.capexOverview(version).then(
      (found) => {
        setO(found)
        setClosed(new Set(found.areas.filter((a) => a.kind === 'node' && a.depth >= 2).map((a) => a.code)))
      },
      (e: unknown) => setError(e instanceof Error ? e.message : String(e)),
    )
  }, [version])

  if (error) return <div className="su-note error">{error}</div>
  if (!o || !closed) return null
  const show = (v: string) => (toCents(v) === 0n ? '–' : display(v, numberFormat()))
  const t = totals(o.total)

  // An area row shows while no node above it is closed.
  const visible: boolean[] = []
  const hiddenFrom: number[] = []
  for (const a of o.areas) {
    while (hiddenFrom.length && a.depth <= hiddenFrom[hiddenFrom.length - 1]) hiddenFrom.pop()
    visible.push(hiddenFrom.length === 0)
    if (a.kind === 'node' && closed.has(a.code) && hiddenFrom.length === 0) hiddenFrom.push(a.depth)
  }
  const toggle = (code: string) => {
    const next = new Set(closed)
    if (!next.delete(code)) next.add(code)
    setClosed(next)
  }
  const cumulative: string[] = []
  o.schedule.reduce((run, c) => {
    const next = c.period === null ? run : add(run, c.confirmed, c.open)
    cumulative.push(next)
    return next
  }, '0')

  return (
    <div className="cx-overview">
      <div className="tiles cx-tiles">
        <Tile label="Planned" value={display(t.total)} hint={`${o.total.count} investments, declined left out`} />
        <Tile label="Confirmed" value={display(t.confirmed)} tone="ok" />
        <Tile label="Open" value={display(t.open)} />
        <Tile label="Declined" value={display(t.declined)} tone="same" />
        {o.waiting > 0 && <Tile label="Waiting for you" value={o.waiting} hint="open investments you confirm" tone="waiting" onClick={onWaiting} />}
      </div>

      <section className="su-card">
        <div className="cx-card-head">
          <h3>Investments by {by === 'area' ? 'area' : 'type'}</h3>
          <div className="seg" role="group" aria-label="Group by">
            {(['area', 'type'] as const).map((b) => (
              <button key={b} type="button" aria-pressed={by === b} onClick={() => setBy(b)}>
                {b === 'area' ? 'Area' : 'Type'}
              </button>
            ))}
          </div>
          <SheetScale />
        </div>
        <div className="tbl-scroll">
          <table className="tbl cx-sums">
            <Head first={by === 'area' ? 'Area' : 'Type'} />
            <tbody>
              {by === 'area'
                ? o.areas.map(
                    (a, i) =>
                      visible[i] && (
                        <tr key={a.code} className={a.kind === 'node' ? 'cx-node' : undefined}>
                          <td style={{ paddingLeft: 12 + a.depth * 16 }}>
                            {a.kind === 'node' ? (
                              <button type="button" className="cx-tree" onClick={() => toggle(a.code)} aria-expanded={!closed.has(a.code)}>
                                {closed.has(a.code) ? '▸' : '▾'} {a.name}
                              </button>
                            ) : (
                              <>
                                <span className="pl-code">{a.code}</span> {a.name}
                              </>
                            )}
                          </td>
                          <Cells s={a.sums} show={show} />
                        </tr>
                      ),
                  )
                : o.types.map((ty) => (
                    <tr key={ty.code ?? '-'}>
                      <td>
                        {ty.code && <span className="pl-code">{ty.code}</span>} {ty.name}
                      </td>
                      <Cells s={ty.sums} show={show} />
                    </tr>
                  ))}
            </tbody>
            <tfoot>
              <tr>
                <th>Total</th>
                <Cells s={o.total} show={show} />
              </tr>
            </tfoot>
          </table>
        </div>
      </section>

      {o.cash_out && (
        <section className="su-card">
          <div className="cx-card-head">
            <h3>Cash out</h3>
          </div>
          <p className="su-hint">Scheduled cash out per period; declined investments left out of the totals.</p>
          <div className="tbl-scroll">
            <table className="tbl cx-sums cx-cash-table">
              <thead>
                <tr>
                  <th>Period</th>
                  <th className="num">Confirmed</th>
                  <th className="num">Open</th>
                  <th className="num">Total</th>
                  <th className="num">Cumulative</th>
                  <th className="num cx-sep">Declined</th>
                </tr>
              </thead>
              <tbody>
                {o.schedule.map((c, i) => (
                  <tr key={c.period ?? 'rest'} className={c.period === null ? 'cx-rest' : undefined}>
                    <td>{c.period === null ? 'Not scheduled' : c.period === o.next_year ? `FY ${o.round.fiscal_year + 1}` : monthLabel(c.period)}</td>
                    <td className="num cx-ok">{show(c.confirmed)}</td>
                    <td className="num">{show(c.open)}</td>
                    <td className="num strong">{show(add(c.confirmed, c.open))}</td>
                    <td className="num ink-3">{c.period === null ? '' : show(cumulative[i])}</td>
                    <td className="num ink-3 cx-sep">{show(c.declined)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}
