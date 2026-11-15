import { useCallback, useEffect, useRef, useState } from 'react'
import { format } from './amounts'
import { api, type SalaryFormat, type SalaryUpload as Result, type SalaryUploadInfo, type SalaryUploadRow } from './api'

// HR's salary upload (ADR 0025): while the sync doesn't read salaries, HR downloads a template of
// the round's people, fills in the monthly salaries and uploads it. The check shows each row's
// outcome before anything is written; Apply writes HR's salary on the round's positions and in
// HR's staff list, and recomputes the totals.

type Outcome = SalaryUploadRow['outcome']
type Filter = Outcome | 'all' | 'differs'

const OUTCOMES: { value: Outcome; label: string; tone: string; pill: string }[] = [
  { value: 'changed', label: 'Changed', tone: 'chg', pill: 'in_progress' },
  { value: 'unchanged', label: 'Unchanged', tone: 'same', pill: 'neutral' },
  { value: 'not_in_round', label: 'Not in the round', tone: 'same', pill: 'submitted' },
  { value: 'blank', label: 'No salary', tone: 'same', pill: 'neutral' },
  { value: 'unknown', label: 'Unknown number', tone: 'rem', pill: 'overdue' },
  { value: 'failed', label: 'Failed', tone: 'rem', pill: 'overdue' },
]
const LABEL = Object.fromEntries(OUTCOMES.map((o) => [o.value, o])) as Record<Outcome, (typeof OUTCOMES)[number]>
const FORMATS: [SalaryFormat, string][] = [
  ['xlsx', 'Excel'],
  ['csv', 'CSV'],
  ['parquet', 'Parquet'],
]

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

const money = (v: string | null) => (v === null ? '–' : format(v))

export default function SalaryUpload({ version, onApplied }: { version: string; onApplied: () => void }) {
  const [info, setInfo] = useState<SalaryUploadInfo | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<Result | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  const load = useCallback(() => {
    api.salaryUpload(version).then(setInfo, (e: unknown) => setError(errorText(e)))
  }, [version])
  useEffect(load, [load])

  async function send(step: 'check' | 'apply') {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const r = await api.uploadSalaries(version, file, step)
      setResult(r)
      setFilter(r.applied ? 'changed' : 'all')
      if (r.applied) {
        load()
        onApplied()
      }
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  if (!info) return error ? <div className="su-note error">{error}</div> : null
  if (info.pay_from_source) {
    return (
      <div className="su-card empty-state">
        <h3>Salaries come from the source</h3>
        <p className="su-hint">
          The sync reads HR’s salaries with the staff list. The admin switches that off under Master data, Sources, Staff
          (HR); then HR uploads them here.
        </p>
      </div>
    )
  }

  const count = (o: Outcome) => result?.rows.filter((r) => r.outcome === o).length ?? 0
  const differs = result?.rows.filter((r) => r.file_cost_center !== null).length ?? 0
  const shown =
    result?.rows.filter((r) =>
      filter === 'all' ? true : filter === 'differs' ? r.file_cost_center !== null : r.outcome === filter,
    ) ?? []
  const writes = result ? result.positions + result.staff_list : 0

  return (
    <div className="su-col su-upload">
      {info.reason && <div className="su-note">{info.reason}.</div>}

      <section className="su-card">
        <div className="fw-card-head">
          <h3>Template</h3>
          <span className="su-hint">
            {info.people} people from HR’s staff list and the round, one per row, the salary blank. {info.with_salary} of
            the round’s positions have HR’s salary so far.
          </span>
        </div>
        <div className="su-body su-row">
          {FORMATS.map(([f, label]) => (
            <a key={f} className="su-btn" href={api.salaryTemplate(version, f)} download>
              {label}
            </a>
          ))}
          <span className="su-hint">
            Columns <code>number</code>, <code>name</code>, <code>title</code>, <code>cost_center</code>,{' '}
            <code>monthly_salary</code>.
          </span>
        </div>
      </section>

      <section className="su-card">
        <div className="fw-card-head">
          <h3>File</h3>
          <span className="su-hint">
            Read by its column names: <code>number</code> and <code>monthly_salary</code> are needed, <code>cost_center</code>{' '}
            is compared, the rest is ignored. The salary per month, at the person’s contract percentage; a blank one stays
            as it is.
          </span>
        </div>
        <div className="su-body su-row">
          <input
            ref={input}
            type="file"
            accept=".csv,.txt,.xlsx,.xlsm,.parquet"
            hidden
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null)
              setResult(null)
              setError(null)
            }}
          />
          <button type="button" className="su-btn" onClick={() => input.current?.click()}>
            Choose file
          </button>
          <span className={file ? 'su-file' : 'su-hint'}>{file ? file.name : 'CSV, Excel or Parquet'}</span>
          <button type="button" className="su-btn primary" disabled={!file || busy || !!info.reason} onClick={() => void send('check')}>
            {busy && !result ? 'Checking…' : 'Check'}
          </button>
        </div>
        {error && <div className="su-note error su-body">{error}</div>}
      </section>

      {result && (
        <section className="su-card">
          <div className="fw-card-head">
            <h3>Result</h3>
            <span className="su-hint">
              {result.applied
                ? `Applied: HR’s salary on ${result.positions} position${result.positions === 1 ? '' : 's'} and ${result.staff_list} in the staff list; the totals follow.`
                : 'Nothing is written yet.'}
            </span>
          </div>
          <div className="pb-tiles su-upload-tiles">
            {OUTCOMES.map((o) => (
              <button
                key={o.value}
                type="button"
                className={`pb-tile ${o.tone}`}
                aria-pressed={filter === o.value}
                onClick={() => setFilter(filter === o.value ? 'all' : o.value)}
              >
                <div className="pb-value">{count(o.value)}</div>
                <div className="pb-label">{o.label}</div>
              </button>
            ))}
          </div>
          <div className="su-body su-col">
            {differs > 0 && (
              <div className="su-note warn">
                {differs} row{differs === 1 ? '' : 's'} name{differs === 1 ? 's' : ''} another cost center than the position
                or the staff list. The number decides; check them{' '}
                <button type="button" className="su-link" onClick={() => setFilter('differs')}>
                  here
                </button>
                .
              </div>
            )}
            {result.ignored.length > 0 && (
              <div className="su-hint">Ignored columns: {result.ignored.join(', ')}.</div>
            )}
            {filter !== 'all' && (
              <div className="su-row">
                <span className="su-hint">
                  {filter === 'differs' ? 'Other cost center' : LABEL[filter].label}: {shown.length} row
                  {shown.length === 1 ? '' : 's'}
                </span>
                <button type="button" className="su-btn ghost sm" onClick={() => setFilter('all')}>
                  Show all
                </button>
              </div>
            )}
          </div>
          {shown.length > 0 && (
            <div className="tbl-scroll su-upload-rows">
              <table className="tbl pc-table">
                <thead>
                  <tr>
                    <th className="num">Line</th>
                    <th>Number</th>
                    <th>Name</th>
                    <th>Cost center</th>
                    <th className="num">HR salary now</th>
                    <th className="num">In the file</th>
                    <th>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r) => (
                    <tr key={r.line}>
                      <td className="num muted">{r.line}</td>
                      <td>
                        <span className="code">{r.number ?? '–'}</span>
                      </td>
                      <td className={r.name ? undefined : 'muted'}>{r.name ?? '–'}</td>
                      <td>
                        {r.cost_center ?? <span className="muted">–</span>}
                        {r.file_cost_center !== null && (
                          <span className="su-pill in_progress su-upload-cc" title="The cost center in the file">
                            file: {r.file_cost_center}
                          </span>
                        )}
                      </td>
                      <td className="num muted">{money(r.old)}</td>
                      <td className="num">{r.outcome === 'changed' ? <strong>{money(r.new)}</strong> : money(r.new)}</td>
                      <td>
                        <span className={`su-pill ${LABEL[r.outcome].pill}`}>{LABEL[r.outcome].label}</span>
                        {r.message && <small className="su-upload-msg">{r.message}</small>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {!result.applied && (
            <div className="su-body su-row su-upload-apply">
              <button type="button" className="su-btn primary" disabled={busy || writes === 0} onClick={() => void send('apply')}>
                {busy ? 'Applying…' : 'Apply'}
              </button>
              <span className="su-hint">
                {writes === 0
                  ? 'Nothing to change: every salary in the file is as it is already.'
                  : `Writes HR’s salary on ${result.positions} position${result.positions === 1 ? '' : 's'} and ${result.staff_list} ${result.staff_list === 1 ? 'person' : 'people'} in the staff list, and recomputes the totals. Failed and unknown rows are left out.`}
              </span>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
