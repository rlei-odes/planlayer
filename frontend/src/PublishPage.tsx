import { useCallback, useEffect, useState } from 'react'
import type { Version } from './api'
import type * as S from './schema'

// Publishing (ADR 0011): download a round's plan values, or preview and run a publish into
// planlayer_out. The preview shows every row added, changed and removed before anything is
// written; the run refuses if anything changed since.

type Scope = S.PublishRunIn['scope']
type Preview = S.PublishPreviewOut
type Run = S.PublishRunOut

const FMT = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const MONTH = new Intl.DateTimeFormat('en', { month: 'short', year: 'numeric', timeZone: 'UTC' })
const amount = (v: string | null) => (v === null ? '–' : FMT.format(Number(v)))

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(typeof body.detail === 'string' ? body.detail : `${response.status} ${response.statusText}`)
  }
  return response.json() as Promise<T>
}

export default function PublishPage({ round }: { round: Version }) {
  const code = round.code
  const [scope, setScope] = useState<Scope>('approved')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ kind: 'ok' | 'error' | 'warn'; text: string } | null>(null)

  const loadRuns = useCallback(() => {
    if (code) request<Run[]>(`/api/rounds/${encodeURIComponent(code)}/publish/runs`).then(setRuns, () => setRuns([]))
  }, [code])
  useEffect(loadRuns, [loadRuns])

  function choose(next: { scope: Scope }) {
    setScope(next.scope)
    setPreview(null)
    setNote(null)
  }

  async function makePreview() {
    setBusy(true)
    try {
      setPreview(await request<Preview>(`/api/rounds/${encodeURIComponent(code)}/publish/preview?scope=${scope}`))
      setNote(null)
    } catch (e) {
      setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  async function publish() {
    if (!preview) return
    setBusy(true)
    try {
      const done = await request<Preview>(`/api/rounds/${encodeURIComponent(code)}/publish`, {
        method: 'POST',
        body: JSON.stringify({ scope, fingerprint: preview.fingerprint }),
      })
      setNote({
        kind: 'ok',
        text: `Published ${done.rows} rows: ${done.added} added, ${done.changed} changed, ${done.removed} removed.`,
      })
      setPreview(null)
      loadRuns()
    } catch (e) {
      setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
      setPreview(null)
    } finally {
      setBusy(false)
    }
  }

  const nothing = preview && preview.added + preview.changed + preview.removed === 0
  const file = (fmt: string) => `/api/rounds/${encodeURIComponent(code)}/export.${fmt}?scope=${scope}`
  return (
    <div className="su">
      <div className="su-head">
        <div>
          <div className="su-eyebrow">Publishing</div>
          <h2 className="su-title">{round.name}</h2>
        </div>
      </div>

      <div className="pb-layout">
        <div className="su-col">
          <section className="su-card">
            <h3>
              <span className="su-step">1</span> What to publish
            </h3>
            <p className="su-hint">Plan values of {round.name}. Actuals stay in the warehouse.</p>
            <div className="su-body">
              <div className="su-forms pb-scope" role="group" aria-label="Scope">
                <button type="button" aria-pressed={scope === 'approved'} onClick={() => choose({ scope: 'approved' })}>
                  Approved cost centers
                </button>
                <button type="button" aria-pressed={scope === 'all'} onClick={() => choose({ scope: 'all' })}>
                  Whole round
                </button>
              </div>
              <p className="su-explain">
                {scope === 'approved'
                  ? 'Only approved cost centers reach BI. A cost center reopened since its last publish is removed.'
                  : 'Every current value, drafts included. Each row carries its cost center’s status, so BI can filter.'}
              </p>
            </div>
          </section>

          <section className="su-card">
            <h3>
              <span className="su-step">2</span> Download
            </h3>
            <p className="su-hint">The same rows as a file. Writes nothing.</p>
            <div className="su-body su-row">
              <a className="su-btn" href={file('csv')} download>
                CSV
              </a>
              <a className="su-btn" href={file('parquet')} download>
                Parquet
              </a>
            </div>
          </section>

          <section className="su-card">
            <h3>
              <span className="su-step">3</span> Publish to planlayer_out
            </h3>
            <p className="su-hint">Replaces this round’s rows in the output, where BI reads them. Preview first.</p>
            <div className="su-body su-row">
              <button type="button" className="su-btn" disabled={busy || !code} onClick={() => void makePreview()}>
                {preview ? 'Preview again' : 'Preview publish'}
              </button>
              <button
                type="button"
                className="su-btn primary"
                disabled={busy || !preview || !!nothing}
                onClick={() => void publish()}
              >
                {preview && !nothing ? `Publish: +${preview.added} ~${preview.changed} −${preview.removed}` : 'Publish'}
              </button>
            </div>
            {note && <div className={`su-note ${note.kind}`} style={{ margin: '12px 0 0 32px' }}>{note.text}</div>}
          </section>

          <section className="su-card">
            <h3>Recent runs</h3>
            <ul className="su-list" style={{ marginTop: 10 }}>
              {runs.map((r) => (
                <li key={r.id}>
                  <span className="grow">
                    {new Date(r.started_at).toLocaleString()} · {r.scope === 'approved' ? 'approved' : 'whole round'}
                    <span className="pb-muted"> · {r.actor}</span>
                  </span>
                  {r.finished_at ? (
                    <span className="pb-counts">
                      {r.rows} rows · <b className="add">+{r.added}</b> <b className="chg">~{r.changed}</b>{' '}
                      <b className="rem">−{r.removed}</b>
                    </span>
                  ) : (
                    <span className="pb-failed">did not finish</span>
                  )}
                </li>
              ))}
              {runs.length === 0 && <li className="su-empty">Not published yet.</li>}
            </ul>
          </section>
        </div>

        <section className="pv pb-preview" aria-label="Publish preview">
          <div className="pv-label">
            <span className="su-eyebrow">What this run would do</span>
            <span className="pv-sample">{preview ? (preview.scope === 'approved' ? 'approved only' : 'whole round') : ''}</span>
          </div>
          {!preview && <div className="pb-empty">Preview a publish to see every row it adds, changes and removes.</div>}
          {preview && (
            <>
              <div className="pb-tiles">
                <Tile label="added" value={preview.added} tone="add" />
                <Tile label="changed" value={preview.changed} tone="chg" />
                <Tile label="removed" value={preview.removed} tone="rem" />
                <Tile label="unchanged" value={preview.unchanged} tone="same" />
              </div>
              <div className="pv-notes">
                {nothing && <div className="su-note ok">The output already holds exactly these rows. Nothing to publish.</div>}
                {preview.changed + preview.removed > 0 && (
                  <div className="su-note warn">
                    {preview.changed > 0 && `${preview.changed} existing row${preview.changed === 1 ? '' : 's'} will be overwritten`}
                    {preview.changed > 0 && preview.removed > 0 && ', '}
                    {preview.removed > 0 && `${preview.removed} removed`}. BI sees the change at once.
                  </div>
                )}
              </div>
              {preview.cost_centers.length > 0 && (
                <div className="pv-scroll">
                  <table className="pv-table pb-table">
                    <thead>
                      <tr>
                        <th className="acc">Cost center</th>
                        <th>added</th>
                        <th>changed</th>
                        <th>removed</th>
                        <th>unchanged</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...preview.cost_centers]
                        .sort((a, b) => b.added + b.changed + b.removed - (a.added + a.changed + a.removed))
                        .map((c) => (
                          <tr key={c.code}>
                            <td className="acc">{c.code}</td>
                            <td className="pv-num add">{c.added || ''}</td>
                            <td className="pv-num chg">{c.changed || ''}</td>
                            <td className="pv-num rem">{c.removed || ''}</td>
                            <td className="pv-num pb-muted">{c.unchanged || ''}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              )}
              {preview.changes.length > 0 && (
                <div className="pv-scroll">
                  <table className="pv-table pb-table">
                    <thead>
                      <tr>
                        <th className="acc">Row</th>
                        <th>Month</th>
                        <th>In the output</th>
                        <th>After publishing</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.changes.map((c, i) => (
                        <tr key={i}>
                          <td className="acc">
                            <span className={`pb-kind ${c.kind}`}>{c.kind}</span> {c.cost_center_code} · {c.account_code}
                          </td>
                          <td className="pv-num">{MONTH.format(new Date(c.period))}</td>
                          <td className={`pv-num ${c.kind === 'added' ? 'pb-muted' : ''}`}>{amount(c.old)}</td>
                          <td className={`pv-num ${c.kind === 'removed' ? 'pb-muted' : ''}`}>{amount(c.new)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {preview.added + preview.changed + preview.removed > preview.changes.length && (
                    <div className="pb-empty">
                      Showing the first {preview.changes.length} of {preview.added + preview.changed + preview.removed} changes.
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  )
}

function Tile({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className={`pb-tile ${tone}`}>
      <div className="pb-value">{value.toLocaleString('en-US')}</div>
      <div className="pb-label">{label}</div>
    </div>
  )
}
