import { useCallback, useEffect, useRef, useState } from 'react'
import { format } from './amounts'
import { api, type Forwarding, type ForwardingIn, type ForwardingPage as Page } from './api'
import Select, { type Option } from './Select'

// Cost forwarding (ADR 0018), for the controller: a round's forwardings in a table, edited one at
// a time with their receivers. Saving recomputes every forwarding of the round; the charges and
// credits then show on the cost centers' sheets, read only.

type Method = Forwarding['method']
type Shares = NonNullable<Forwarding['shares']>
type Note = { kind: 'ok' | 'error'; text: string }
type Receiver = { cc: string; quantity: string }
type Draft = {
  original: string | null // the forwarding's code before this edit; null: a new one
  code: string
  name: string
  method: Method
  source: string
  shares: Shares
  amount: string
  rate: string
  unit: string
  group: string
  chargeCode: string
  chargeName: string
  creditCode: string
  creditName: string
  receivers: Receiver[]
  // Account names follow the forwarding's name until they are typed in.
  namesTyped: boolean
}

const METHODS: { value: Method; label: string; hint: string }[] = [
  { value: 'full', label: 'All costs', hint: 'what is left on the source (its planned costs and charges received, less its credits), split by shares' },
  { value: 'fixed', label: 'Fixed amount', hint: 'a year amount, split by shares' },
  { value: 'rate', label: 'Rate × quantity', hint: 'a rate per unit, times each receiver’s quantity' },
]
const METHOD_LABEL: Record<Method, string> = { full: 'All costs', fixed: 'Fixed amount', rate: 'Rate × quantity' }

function newDraft(page: Page): Draft {
  return {
    original: null,
    code: '',
    name: '',
    method: 'full',
    source: '',
    shares: 'percent',
    amount: '',
    rate: '',
    unit: '',
    group: '',
    chargeCode: page.proposed_codes[0] ?? '',
    chargeName: '',
    creditCode: page.proposed_codes[1] ?? '',
    creditName: '',
    receivers: [],
    namesTyped: false,
  }
}

function draftOf(f: Forwarding): Draft {
  return {
    original: f.code,
    code: f.code,
    name: f.name,
    method: f.method,
    source: f.source_code,
    shares: f.shares ?? 'percent',
    amount: f.amount ?? '',
    rate: f.rate ?? '',
    unit: f.unit ?? '',
    group: f.group_code ?? '',
    chargeCode: f.charge_account.code,
    chargeName: f.charge_account.name,
    creditCode: f.credit_account.code,
    creditName: f.credit_account.name,
    receivers: f.receivers.map((r) => ({ cc: r.cost_center_code, quantity: r.quantity })),
    namesTyped: true,
  }
}

function body(d: Draft): ForwardingIn {
  return {
    code: d.code.trim(),
    name: d.name.trim(),
    method: d.method,
    source_code: d.source,
    group_code: d.group,
    charge_account: { code: d.chargeCode.trim(), name: d.chargeName.trim() },
    credit_account: { code: d.creditCode.trim(), name: d.creditName.trim() },
    receivers: d.receivers.map((r) => ({ cost_center_code: r.cc, quantity: number(r.quantity) })),
    shares: d.method === 'rate' ? null : d.shares,
    amount: d.method === 'fixed' ? number(d.amount) : null,
    rate: d.method === 'rate' ? number(d.rate) : null,
    unit: d.method === 'rate' ? d.unit.trim() || null : null,
  }
}

/** A typed number as the API takes it: separators of thousands dropped; a comma before one or
 * two digits is a decimal comma, as for amounts ("1,300" is 1300, "2,5" is 2.5). */
function number(raw: string): string {
  const s = raw.trim().replace(/[\s']/g, '')
  if (/^\d+,\d{1,2}$/.test(s)) return s.replace(',', '.')
  return s.includes('.') && s.includes(',') && s.lastIndexOf(',') > s.lastIndexOf('.')
    ? s.replace(/\./g, '').replace(',', '.')
    : s.replace(/,/g, '')
}

// Display only: previews before saving. Stored values come from the server (T-12).
function value(raw: string): number {
  const n = Number(number(raw))
  return Number.isFinite(n) ? n : 0
}
function money(n: number): string {
  return format(n.toFixed(2))
}
function pct(n: number): string {
  return `${n.toLocaleString('en-US', { maximumFractionDigits: 2 })} %`
}

/** What each receiver gets in the year, as far as the page can tell before saving. */
function previews(d: Draft, sourceCosts: number | null): (number | null)[] {
  const total = d.receivers.reduce((s, r) => s + value(r.quantity), 0)
  return d.receivers.map((r) => {
    const q = value(r.quantity)
    if (d.method === 'rate') return q * value(d.rate)
    const base = d.method === 'fixed' ? value(d.amount) : sourceCosts
    if (base === null) return null
    if (d.shares === 'percent') return (base * q) / 100
    return total > 0 ? (base * q) / total : 0
  })
}

export default function ForwardingPage({ version }: { version: string }) {
  const [page, setPage] = useState<Page | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [note, setNote] = useState<Note | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const editor = useRef<HTMLDivElement>(null)

  const load = useCallback(() => {
    api.forwarding(version).then(setPage, (e: unknown) => setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) }))
  }, [version])
  useEffect(load, [load])

  useEffect(() => {
    if (draft?.original !== undefined) editor.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [draft?.original])

  if (!page) return note ? <div className="su-note error">{note.text}</div> : null
  const editable = page.lock_reason === null
  const names = new Map(page.cost_centers.map((c) => [c.code, c.name]))

  function open(d: Draft) {
    setDraft(d)
    setConfirmDelete(false)
    setNote(null)
  }

  async function save() {
    if (!draft) return
    setBusy(true)
    try {
      const b = body(draft)
      const next = draft.original === null ? await api.createForwarding(version, b) : await api.changeForwarding(version, draft.original, b)
      setPage(next)
      const saved = next.forwardings.find((f) => f.code === b.code)
      setDraft(saved ? draftOf(saved) : null)
      setNote({ kind: 'ok', text: `${b.code} saved. Every forwarding of ${next.version_name} was recomputed; the sheets show the result.` })
    } catch (e) {
      setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!draft?.original) return
    setBusy(true)
    try {
      setPage(await api.deleteForwarding(version, draft.original))
      setNote({ kind: 'ok', text: `${draft.original} deleted, with its charges and credits.` })
      setDraft(null)
    } catch (e) {
      setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
      setConfirmDelete(false)
    }
  }

  return (
    <div className="su fw">
      <div className="pg-head">
        <div>
          <div className="su-eyebrow">Cost forwarding · {page.version_name}</div>
          <h2 className="su-title">Passing costs on</h2>
          <div className="pg-sub">
            Service cost centers pass their costs on to the cost centers that use them. Receivers see a charge, the source a
            credit, both read only on their sheets, whatever their status.
          </div>
        </div>
        {editable && (
          <div className="pg-actions">
            <button type="button" className="su-btn primary" onClick={() => open(newDraft(page))}>
              + New forwarding
            </button>
          </div>
        )}
      </div>

      {page.lock_reason && <div className="su-note warn fw-gap">Read only: {page.lock_reason}.</div>}
      {page.closed_months > 0 && (
        <div className="su-note fw-gap">
          This forecast’s first {page.closed_months} months are closed: forwarding writes the open months only.
        </div>
      )}
      {note && !draft && <div className={`su-note ${note.kind} fw-gap`}>{note.text}</div>}

      {page.forwardings.length === 0 && !draft ? (
        <div className="su-card fw-empty">
          <h3>No forwardings in this round yet</h3>
          <p className="ink-3">Three ways to pass costs on:</p>
          <ul>
            <li>
              <strong>All costs:</strong> a tooling cost center passes everything it plans to the lines it serves, by
              percentages or portions.
            </li>
            <li>
              <strong>Fixed amount:</strong> a year amount, split the same way, whatever the source plans.
            </li>
            <li>
              <strong>Rate × quantity:</strong> 1,200 a year per m³ of building A, or a rate per PC or CAD licence. What the
              rates don’t cover stays on the source.
            </li>
          </ul>
        </div>
      ) : (
        <div className="tbl-card">
          <div className="tbl-scroll">
            <table className="tbl fw-list">
              <thead>
                <tr>
                  <th>Forwarding</th>
                  <th>Method</th>
                  <th>Source</th>
                  <th>Basis</th>
                  <th className="right">Receivers</th>
                  <th className="right">Passed on</th>
                  <th>Accounts</th>
                </tr>
              </thead>
              <tbody>
                {page.forwardings.map((f) => (
                  <tr
                    key={f.code}
                    className={draft?.original === f.code ? 'fw-current' : undefined}
                    onClick={() => open(draftOf(f))}
                  >
                    <td>
                      <span className="code">{f.code}</span>
                      {f.name}
                    </td>
                    <td>{METHOD_LABEL[f.method]}</td>
                    <td>
                      <span className="code">{f.source_code}</span>
                      {f.source_name}
                    </td>
                    <td className="ink-2">
                      {f.method === 'rate'
                        ? `${format(f.rate)} per ${f.unit || 'unit'} and year`
                        : f.method === 'fixed'
                          ? `${format(f.amount)} a year, ${f.shares === 'percent' ? 'percentages' : 'portions'}`
                          : f.shares === 'percent'
                            ? 'percentages'
                            : 'portions'}
                    </td>
                    <td className="num">{f.receivers.length}</td>
                    <td className="num">{format(f.passed_on) || '–'}</td>
                    <td className="ink-3">
                      {f.charge_account.code} / {f.credit_account.code}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {draft && (
        <div ref={editor}>
          <Editor
            page={page}
            draft={draft}
            names={names}
            editable={editable}
            busy={busy}
            note={note}
            confirmDelete={confirmDelete}
            onChange={setDraft}
            onSave={() => void save()}
            onCancel={() => setDraft(null)}
            onDelete={() => (confirmDelete ? void remove() : setConfirmDelete(true))}
          />
        </div>
      )}

      {page.sources.length > 0 && (
        <div className="tbl-card fw-gap">
          <div className="fw-card-head">
            <h3>What stays on the sources</h3>
            <span className="ink-3">Planned costs in the round, charges received included, less what is passed on.</span>
          </div>
          <div className="tbl-scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Source</th>
                  <th className="right">Planned costs</th>
                  <th className="right" title="Revenue and capacity credits">
                    Other credits
                  </th>
                  <th className="right">Passed on</th>
                  <th className="right">Stays on the source</th>
                </tr>
              </thead>
              <tbody>
                {page.sources.map((s) => (
                  <tr key={s.cost_center_code}>
                    <td>
                      <span className="code">{s.cost_center_code}</span>
                      {s.cost_center_name}
                    </td>
                    <td className="num">{format(s.costs) || '–'}</td>
                    <td className="num">{format(s.other_credits) || '–'}</td>
                    <td className="num">{format(s.passed_on) || '–'}</td>
                    <td className={`num${s.remaining && s.remaining.startsWith('-') ? ' fw-over' : ''}`}>
                      {format(s.remaining) || '–'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function Editor(p: {
  page: Page
  draft: Draft
  names: Map<string, string>
  editable: boolean
  busy: boolean
  note: Note | null
  confirmDelete: boolean
  onChange: (d: Draft) => void
  onSave: () => void
  onCancel: () => void
  onDelete: () => void
}) {
  const { page, draft: d, editable } = p
  const set = (change: Partial<Draft>) => p.onChange({ ...d, ...change })
  const ccOptions: Option[] = page.cost_centers.map((c) => ({ value: c.code, label: `${c.code} ${c.name}` }))
  const groupOptions: Option[] = page.groups.map((g) => ({
    value: g.code,
    label: g.name,
    hint: g.code,
    depth: g.depth,
    strong: g.depth === 0,
  }))
  const saved = page.forwardings.find((f) => f.code === d.original)
  const source = page.sources.find((s) => s.cost_center_code === d.source)
  const sourceCosts = source?.costs ? Number(source.costs) : null
  const preview = previews(d, sourceCosts)
  const listed = new Set([d.source, ...d.receivers.map((r) => r.cc)])
  const addable = ccOptions.filter((o) => !listed.has(o.value))
  const total = d.receivers.reduce((s, r) => s + value(r.quantity), 0)
  const quantityLabel = d.method === 'rate' ? `Quantity${d.unit ? ` (${d.unit})` : ''}` : d.shares === 'percent' ? 'Share %' : 'Portion'
  const over = d.method !== 'rate' && d.shares === 'percent' && total > 100
  const passedTotal = preview.every((v) => v === null) ? null : preview.reduce<number>((s, v) => s + (v ?? 0), 0)
  // Unchanged since saving: the stored figures, exact; otherwise an estimate.
  const unchanged =
    saved !== undefined &&
    saved.method === d.method &&
    saved.receivers.length === d.receivers.length &&
    d.receivers.every((r) => saved.receivers.some((s) => s.cost_center_code === r.cc && s.quantity === number(r.quantity)))

  function rename(name: string) {
    set(
      d.namesTyped
        ? { name }
        : { name, chargeName: name ? `${name} charged` : '', creditName: name ? `${name} passed on` : '' },
    )
  }
  function receiver(i: number, change: Partial<Receiver>) {
    set({ receivers: d.receivers.map((r, j) => (j === i ? { ...r, ...change } : r)) })
  }

  return (
    <div className="su-card fw-editor">
      <h3>{d.original === null ? 'New forwarding' : `${d.original} · ${saved?.name ?? ''}`}</h3>
      <fieldset disabled={!editable || p.busy} className="fw-fields">
        <div className="fw-grid">
          <label className="su-field fw-code">
            Code
            <input className="su-input" value={d.code} onChange={(e) => set({ code: e.target.value })} placeholder="TOOLS" />
          </label>
          <label className="su-field fw-name">
            Name
            <input className="su-input" value={d.name} onChange={(e) => rename(e.target.value)} placeholder="Tooling" />
          </label>
          <div className="su-field fw-source">
            Source cost center
            <Select
              value={d.source}
              label="Source cost center"
              placeholder="Choose the cost center that passes costs on"
              options={ccOptions}
              searchable
              disabled={!editable}
              onChange={(v) => set({ source: v, receivers: d.receivers.filter((r) => r.cc !== v) })}
            />
          </div>
        </div>

        <div className="su-field fw-gap">
          Method
          <div className="seg fw-methods" role="group" aria-label="Method">
            {METHODS.map((m) => (
              <button key={m.value} type="button" aria-pressed={d.method === m.value} title={m.hint} onClick={() => set({ method: m.value })}>
                {m.label}
              </button>
            ))}
          </div>
          <span className="ink-3">{METHODS.find((m) => m.value === d.method)?.hint}.</span>
        </div>

        <div className="fw-grid fw-gap">
          {d.method !== 'rate' && (
            <div className="su-field">
              Shares as
              <div className="seg" role="group" aria-label="Shares as">
                {(['percent', 'portion'] as const).map((s) => (
                  <button key={s} type="button" aria-pressed={d.shares === s} onClick={() => set({ shares: s })}>
                    {s === 'percent' ? 'Percentages' : 'Portions'}
                  </button>
                ))}
              </div>
            </div>
          )}
          {d.method === 'fixed' && (
            <label className="su-field">
              Amount a year
              <input className="su-input fw-num" inputMode="decimal" value={d.amount} onChange={(e) => set({ amount: e.target.value })} placeholder="120,000" />
            </label>
          )}
          {d.method === 'rate' && (
            <>
              <label className="su-field">
                Rate per unit and year
                <input className="su-input fw-num" inputMode="decimal" value={d.rate} onChange={(e) => set({ rate: e.target.value })} placeholder="1,200" />
              </label>
              <label className="su-field">
                Unit
                <input className="su-input fw-unit" value={d.unit} onChange={(e) => set({ unit: e.target.value })} placeholder="m³, PC, licence" />
              </label>
            </>
          )}
        </div>

        <div className="fw-accounts fw-gap">
          <div className="su-field fw-group">
            Account group
            <Select value={d.group} label="Account group" placeholder="Where the lines show" options={groupOptions} searchable disabled={!editable} onChange={(v) => set({ group: v })} />
          </div>
          <label className="su-field">
            Charge account (receivers)
            <span className="fw-account">
              <input className="su-input fw-acc-code" value={d.chargeCode} onChange={(e) => set({ chargeCode: e.target.value })} aria-label="Charge account code" />
              <input className="su-input" value={d.chargeName} onChange={(e) => set({ chargeName: e.target.value, namesTyped: true })} aria-label="Charge account name" placeholder="Name, if new" />
            </span>
          </label>
          <label className="su-field">
            Credit account (source)
            <span className="fw-account">
              <input className="su-input fw-acc-code" value={d.creditCode} onChange={(e) => set({ creditCode: e.target.value })} aria-label="Credit account code" />
              <input className="su-input" value={d.creditName} onChange={(e) => set({ creditName: e.target.value, namesTyped: true })} aria-label="Credit account name" placeholder="Name, if new" />
            </span>
          </label>
        </div>
        <p className="ink-3 fw-small">
          A new code becomes an account in the group. Put the ERP’s actual forwarding account in the same group and mark it
          not planned in Setup: forecasts then show actuals there and the plan on these accounts.
        </p>

        <div className="tbl-card fw-receivers">
          <table className="tbl">
            <thead>
              <tr>
                <th>Receiver</th>
                <th className="right">{quantityLabel}</th>
                {d.method !== 'rate' && <th className="right">Of the total</th>}
                <th className="right" title="Before saving: an estimate. After saving: as stored in the round.">
                  Year
                </th>
                <th>Status</th>
                <th aria-label="Remove" />
              </tr>
            </thead>
            <tbody>
              {d.receivers.map((r, i) => {
                const stored = saved?.receivers.find((s) => s.cost_center_code === r.cc)
                const unchanged = stored !== undefined && stored.quantity === number(r.quantity)
                const share = d.method === 'rate' ? null : d.shares === 'percent' ? value(r.quantity) : total ? (value(r.quantity) * 100) / total : 0
                return (
                  <tr key={r.cc}>
                    <td>
                      <span className="code">{r.cc}</span>
                      {p.names.get(r.cc) ?? ''}
                    </td>
                    <td className="num">
                      <input
                        className="su-input fw-num"
                        inputMode="decimal"
                        value={r.quantity}
                        aria-label={`${quantityLabel} of ${r.cc}`}
                        onChange={(e) => receiver(i, { quantity: e.target.value })}
                      />
                    </td>
                    {share !== null && <td className="num ink-2">{pct(share)}</td>}
                    <td className="num">
                      {unchanged && stored.year !== null && saved?.method === d.method
                        ? format(stored.year)
                        : preview[i] === null
                          ? '–'
                          : `≈ ${money(preview[i] ?? 0)}`}
                    </td>
                    <td>{stored && <span className={`su-pill ${stored.status}`}>{stored.status.replace('_', ' ')}</span>}</td>
                    <td className="right">
                      <button type="button" className="su-icon" aria-label={`Remove ${r.cc}`} onClick={() => set({ receivers: d.receivers.filter((_, j) => j !== i) })}>
                        ✕
                      </button>
                    </td>
                  </tr>
                )
              })}
              <tr className="fw-add">
                <td colSpan={d.method === 'rate' ? 5 : 6}>
                  <Select
                    variant="action"
                    value=""
                    display="Add receiver"
                    label="Add a receiving cost center"
                    icon={<span className="pl-plus">+</span>}
                    options={addable}
                    searchable
                    disabled={!editable || addable.length === 0}
                    onChange={(v) => set({ receivers: [...d.receivers, { cc: v, quantity: '' }] })}
                  />
                </td>
              </tr>
            </tbody>
            {d.receivers.length > 0 && (
              <tfoot>
                <tr>
                  <td>Total</td>
                  <td className={`num${over ? ' fw-over' : ''}`}>
                    {d.method === 'rate' ? `${total.toLocaleString('en-US')} ${d.unit}` : d.shares === 'percent' ? pct(total) : total.toLocaleString('en-US')}
                  </td>
                  {d.method !== 'rate' && (
                    <td className="num ink-2">
                      {d.shares === 'percent' ? (total < 100 ? `${pct(100 - total)} stays` : '') : pct(total ? 100 : 0)}
                    </td>
                  )}
                  <td className="num">
                    {unchanged ? format(saved.passed_on) || '–' : passedTotal === null ? '–' : `≈ ${money(passedTotal)}`}
                  </td>
                  <td colSpan={2} />
                </tr>
              </tfoot>
            )}
          </table>
        </div>
        {over && <div className="su-note error fw-gap">Percentages add up to more than 100.</div>}
        {d.method === 'full' && d.source && sourceCosts === null && (
          <p className="ink-3 fw-small">The year amounts show once saved: they follow what {d.source} plans.</p>
        )}
      </fieldset>

      {p.note && <div className={`su-note ${p.note.kind} fw-gap`}>{p.note.text}</div>}
      {editable && (
        <div className="fw-buttons">
          <button type="button" className="su-btn primary" disabled={p.busy || over} onClick={p.onSave}>
            {p.busy ? 'Saving…' : 'Save and recompute'}
          </button>
          <button type="button" className="su-btn ghost" onClick={p.onCancel}>
            Cancel
          </button>
          {d.original !== null && (
            <button type="button" className="su-btn danger fw-delete" disabled={p.busy} onClick={p.onDelete}>
              {p.confirmDelete ? `Delete ${d.original} and its values?` : 'Delete'}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
