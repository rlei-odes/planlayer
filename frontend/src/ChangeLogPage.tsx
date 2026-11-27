import { Fragment, useCallback, useEffect, useState } from 'react'
import { detail, formatNumber, fromCents, toCents } from './amounts'
import { api, type ChangeBatch, type ChangeDetail, type ChangeLine, type Changes, type Version } from './api'
import PageHeader from './PageHeader'
import Select from './Select'

// The round's change log for controllers: one entry per change batch (a save, a paste, an undo,
// a carry-over, a recalculation), newest first, opened to its lines.

type Kind = 'all' | 'entered' | 'calculated'
type Sheet = ChangeBatch['sheets'][number]
type Source = ChangeBatch['sources'][number]

const TIME = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit' })
const DAY = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' })
const DAY_YEAR = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
const AGO = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

/** "Just now", "12 minutes ago", then "Today, 14:02", "Yesterday, 14:02", "18 Nov, 19:09";
 * a year before this one with the year. The exact time is the cell's tooltip. */
function when(at: Date, now: Date): string {
  const minutes = Math.floor((now.getTime() - at.getTime()) / 60_000)
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return AGO.format(-minutes, 'minute')
  const days = Math.round(
    (new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() -
      new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime()) /
      86_400_000,
  )
  const day = days === 0 ? 'Today' : days === 1 ? 'Yesterday' : (at.getFullYear() === now.getFullYear() ? DAY : DAY_YEAR).format(at)
  return `${day}, ${TIME.format(at)}`
}
const MONTH = new Intl.DateTimeFormat('en', { month: 'short', timeZone: 'UTC' })
const SHEET_LABEL: Record<Sheet, string> = { costs: 'Costs', capacity: 'Capacity', workforce: 'Workforce' }
const SOURCE_LABEL: Record<Source, string> = {
  plan: 'Entered',
  personnel: 'Personnel costs',
  forwarding: 'Forwarding',
  credit: 'Credits',
}
const KINDS: [Kind, string][] = [
  ['all', 'All'],
  ['entered', 'Entered'],
  ['calculated', 'Calculated'],
]

/** "Jan–Mar, Jun": the changed months, runs joined. */
function monthsText(months: string[]): string {
  const index = (m: string) => Number(m.slice(0, 4)) * 12 + Number(m.slice(5, 7))
  const runs: string[][] = []
  for (const m of months) {
    const run = runs.at(-1)
    if (run && index(m) === index(run.at(-1)!) + 1) run.push(m)
    else runs.push([m])
  }
  const name = (m: string) => MONTH.format(new Date(m))
  return runs.map((r) => (r.length === 1 ? name(r[0]) : `${name(r[0])}–${name(r.at(-1)!)}`)).join(', ')
}

function value(v: string | null, unit: ChangeLine['unit']): string {
  if (v === null) return 'blank'
  return unit === 'amount' ? detail(v) : formatNumber(v)
}

function difference(line: ChangeLine): string {
  if (line.before === null || line.after === null) return ''
  const cents = toCents(line.after) - toCents(line.before)
  if (cents === 0n) return '±0'
  const text = value(fromCents(cents), line.unit)
  return cents > 0n ? `+${text}` : text
}

function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  const rest = { ...record }
  delete rest[key]
  return rest
}

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export default function ChangeLogPage({ version, round }: { version: string; round: Version | undefined }) {
  const [kind, setKind] = useState<Kind>('all')
  const [sheet, setSheet] = useState<Sheet | ''>('')
  const [actor, setActor] = useState('')
  const [costCenter, setCostCenter] = useState('')
  const [page, setPage] = useState<Changes | null>(null)
  const [batches, setBatches] = useState<ChangeBatch[]>([])
  const [open, setOpen] = useState<Record<string, ChangeDetail | 'loading'>>({})
  const [error, setError] = useState<string | null>(null)
  const [olderLoading, setOlderLoading] = useState(false)
  const [now, setNow] = useState(() => new Date())

  const fetchPage = useCallback(
    (cursor: string | null) =>
      api
        .changes(version, { kind, sheet: sheet || null, actor: actor || null, cost_center: costCenter || null, cursor })
        .then((p) => {
          setNow(new Date())
          setPage(p)
          setBatches((before) => (cursor ? [...before, ...p.batches] : p.batches))
          if (!cursor) setOpen({})
          setError(null)
        }, (e: unknown) => setError(errorText(e))),
    [version, kind, sheet, actor, costCenter],
  )

  useEffect(() => {
    void fetchPage(null)
  }, [fetchPage])

  function older(cursor: string) {
    setOlderLoading(true)
    void fetchPage(cursor).finally(() => setOlderLoading(false))
  }

  function toggle(b: ChangeBatch) {
    if (open[b.batch_id]) {
      setOpen((o) => without(o, b.batch_id))
      return
    }
    setOpen((o) => ({ ...o, [b.batch_id]: 'loading' }))
    api.changeBatch(version, b.batch_id, costCenter || null).then(
      (d) => setOpen((o) => ({ ...o, [b.batch_id]: d })),
      (e: unknown) => {
        setError(errorText(e))
        setOpen((o) => without(o, b.batch_id))
      },
    )
  }

  const filtered = sheet !== '' || actor !== '' || costCenter !== ''
  return (
    <div className="page">
      <PageHeader
        eyebrow={`Log · ${round?.name ?? version}`}
        title="Change log"
        sub="Every change of plan values, hours and FTE in this round, one entry per save. Open an entry to see its lines."
        tabs={
          <div className="seg" role="group" aria-label="Show">
            {KINDS.map(([k, label]) => (
              <button key={k} type="button" aria-pressed={kind === k} onClick={() => setKind(k)}>
                {label}
                <span className="count">{page?.counts[k] ?? 0}</span>
              </button>
            ))}
          </div>
        }
      />

      <div className="cl-filters">
        <Select<Sheet | ''>
          variant="pill"
          label="Sheet"
          value={sheet}
          options={[
            { value: '', label: 'All sheets' },
            ...(Object.keys(SHEET_LABEL) as Sheet[]).map((s) => ({ value: s, label: SHEET_LABEL[s] })),
          ]}
          onChange={setSheet}
        />
        <Select
          variant="pill"
          label="User"
          value={actor}
          searchable
          options={[{ value: '', label: 'Everyone' }, ...(page?.users ?? []).map((u) => ({ value: u.login, label: u.name, hint: u.login }))]}
          onChange={setActor}
        />
        <Select
          variant="pill"
          label="Cost center"
          value={costCenter}
          searchable
          options={[
            { value: '', label: 'All cost centers' },
            ...(page?.cost_centers ?? []).map((c) => ({ value: c.code, label: `${c.code} ${c.name}` })),
          ]}
          onChange={setCostCenter}
        />
        {filtered && (
          <button
            type="button"
            className="su-btn ghost sm"
            onClick={() => {
              setSheet('')
              setActor('')
              setCostCenter('')
            }}
          >
            Clear filters
          </button>
        )}
      </div>

      {error && <div className="su-note error st-gap">{error}</div>}

      <div className="tbl-card">
        <div className="tbl-scroll">
          <table className="tbl cl-tbl">
            <thead>
              <tr>
                <th>When</th>
                <th>Who</th>
                <th>Change</th>
                <th>Cost centers</th>
                <th className="num">Cells</th>
                <th aria-label="Open" />
              </tr>
            </thead>
            <tbody>
              {batches.map((b) => {
                const shown = open[b.batch_id]
                return (
                  <Fragment key={b.batch_id}>
                    <tr className={`cl-row${shown ? ' open' : ''}`} onClick={() => toggle(b)}>
                      <td className="muted cl-when" title={new Date(b.at).toLocaleString()}>
                        {when(new Date(b.at), now)}
                      </td>
                      <td title={b.actor.login}>{b.actor.name}</td>
                      <td>
                        <span className="cl-tags">
                          {b.calculated && <span className="su-pill submitted">Calculated</span>}
                          {(b.sheets.length > 1 || b.sheets[0] !== 'costs' ? b.sheets : []).map((s) => (
                            <span key={s} className="su-pill neutral">
                              {SHEET_LABEL[s]}
                            </span>
                          ))}
                          {b.sources
                            .filter((s) => s !== 'plan')
                            .map((s) => (
                              <span key={s} className="su-pill neutral">
                                {SOURCE_LABEL[s]}
                              </span>
                            ))}
                          {b.cleared > 0 && (
                            <span className="su-pill overdue-soft">
                              {b.cleared === b.cells ? 'Cleared' : `${b.cleared} cleared`}
                            </span>
                          )}
                        </span>
                      </td>
                      <td>
                        {b.cost_center_count === 1 ? (
                          <span>
                            <span className="code">{b.cost_centers[0].code}</span>
                            {b.cost_centers[0].name}
                          </span>
                        ) : (
                          <span title={b.cost_centers.map((c) => `${c.code} ${c.name}`).join('\n')}>
                            {b.cost_centers.map((c) => c.code).join(', ')}
                            {b.cost_center_count > b.cost_centers.length && (
                              <span className="muted"> +{b.cost_center_count - b.cost_centers.length} more</span>
                            )}
                          </span>
                        )}
                      </td>
                      <td className="num">{b.cells.toLocaleString('en')}</td>
                      <td className="right">
                        <span className="cl-chevron" aria-hidden="true">
                          ›
                        </span>
                      </td>
                    </tr>
                    {shown && (
                      <tr className="cl-detail">
                        <td colSpan={6}>
                          {shown === 'loading' ? (
                            <span className="muted">Loading…</span>
                          ) : (
                            <Lines lines={shown.lines} total={shown.total_lines} />
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
              {batches.length === 0 && page !== null && (
                <tr>
                  <td colSpan={6} className="st-empty">
                    {filtered || kind !== 'all' ? 'No changes match the filters.' : 'Nothing has changed in this round yet.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      {page?.cursor && (
        <div className="cl-more">
          <button type="button" className="su-btn" disabled={olderLoading} onClick={() => older(page.cursor!)}>
            {olderLoading ? 'Loading…' : 'Show older changes'}
          </button>
        </div>
      )}
    </div>
  )
}

function Lines({ lines, total }: { lines: ChangeLine[]; total: number }) {
  return (
    <>
      <table className="cl-lines">
        <thead>
          <tr>
            <th>Cost center</th>
            <th>Line</th>
            <th>Months</th>
            <th className="num">Before</th>
            <th className="num">After</th>
            <th className="num">Change</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((l, i) => (
            <tr key={i}>
              <td>
                <span className="code">{l.cost_center.code}</span>
                {l.cost_center.name}
              </td>
              <td>
                <span className="code">{l.code}</span>
                {l.name}
                {l.sheet !== 'costs' && <span className="cl-unit">{l.unit === 'hours' ? 'hours' : l.unit === 'fte' ? 'FTE' : 'heads'}</span>}
                {l.source !== 'plan' && <span className="cl-unit">{SOURCE_LABEL[l.source]}</span>}
              </td>
              <td className="muted">{monthsText(l.months)}</td>
              <td className={`num${l.before === null ? ' muted' : ''}`}>{value(l.before, l.unit)}</td>
              <td className={`num${l.after === null ? ' muted' : ''}`}>{value(l.after, l.unit)}</td>
              <td className="num">{difference(l)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {total > lines.length && (
        <p className="muted cl-cut">
          The first {lines.length.toLocaleString('en')} of {total.toLocaleString('en')} lines.
        </p>
      )}
    </>
  )
}
