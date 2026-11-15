import { useCallback, useEffect, useState } from 'react'
import { format, sum } from './amounts'
import { api, type LineRef, type Staff, type StaffPosition } from './api'
import Select, { type Option } from './Select'

// The capacity page's workforce tab (ADR 0027): the productive hours of a cost center's
// positions and the line each one's go to. The server computes the hours and writes them to
// the lines on every change.

type Message = { kind: 'error' | 'ok'; text: string }

/** A percentage without trailing zeros: "80.00" is "80", "62.50" is "62.5". */
function pct(value: string | null): string {
  return value === null ? '' : value.includes('.') ? value.replace(/\.?0+$/, '') : value
}

const lineKey = (wc: string | null, at: string | null) => (wc && at ? `${wc}|${at}` : '')

function lineOf(key: string): LineRef | null {
  if (!key) return null
  const [work_center_code, activity_type_code] = key.split('|')
  return { work_center_code, activity_type_code }
}

/** A percentage typed into a table: saved on Enter or leaving the field, if it changed. */
function PercentInput({
  value,
  placeholder,
  label,
  disabled,
  onSave,
}: {
  value: string | null
  placeholder?: string
  label: string
  disabled: boolean
  onSave: (value: string | null) => void
}) {
  const shown = pct(value)
  function commit(raw: string) {
    const next = raw.trim()
    if (next !== shown) onSave(next === '' ? null : next.replace(',', '.'))
  }
  return (
    <span className="cw-pct">
      <input
        key={shown}
        className="su-input"
        inputMode="decimal"
        defaultValue={shown}
        placeholder={placeholder}
        aria-label={label}
        disabled={disabled}
        onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.currentTarget.blur()
        }}
      />
      <span className="ink-3">%</span>
    </span>
  )
}

export default function CapacityWorkforce({
  version,
  unit,
  onChanged,
}: {
  version: string
  unit: string
  /** Hours changed: the sheet and the cost center's status follow. */
  onChanged: () => void
}) {
  const [view, setView] = useState<Staff | null>(null)
  const [message, setMessage] = useState<Message | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api.capacityStaff(version, unit).then(setView, (e: unknown) =>
      setMessage({ kind: 'error', text: e instanceof Error ? e.message : String(e) }),
    )
  }, [version, unit])
  useEffect(load, [load])

  async function apply(saving: Promise<Staff>) {
    setBusy(true)
    try {
      setView(await saving)
      setMessage(null)
      onChanged()
    } catch (e) {
      setMessage({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
      load()
    } finally {
      setBusy(false)
    }
  }

  if (!view) return message ? <div className="su-note error pl-note">{message.text}</div> : null

  const open = view.lock_reason === null && !busy
  const lines: Option[] = view.lines.map((l) => ({
    value: lineKey(l.work_center_code, l.activity_type_code),
    label: `${l.work_center_code} ${l.work_center_name} · ${l.activity_type_name}`,
    hint: `${l.rate} / h`,
  }))
  const fallback = view.lines.find(
    (l) => lineKey(l.work_center_code, l.activity_type_code) === lineKey(view.default_work_center_code, view.default_activity_type_code),
  )
  const defaultLabel = fallback ? `Default · ${fallback.work_center_code} ${fallback.activity_type_name}` : 'Default · none'
  const defaultLine = lineOf(lineKey(view.default_work_center_code, view.default_activity_type_code))

  function saveDefaults(line: LineRef | null, percent: string | null) {
    void apply(api.saveStaffDefaults(version, unit, { line, percent }))
  }
  function savePosition(p: StaffPosition, line: LineRef | null, percent: string | null) {
    void apply(api.saveStaffPosition(version, unit, p.position_id, { line, percent }))
  }
  const own = (p: StaffPosition) => lineOf(lineKey(p.work_center_code, p.activity_type_code))
  const staffed = view.lines.filter((l) => l.hours !== null)

  return (
    <>
      {view.lock_reason && (
        <div className="su-note pl-note">
          <strong>Read only:</strong> {view.lock_reason}.
        </div>
      )}
      {message && <div className={`su-note ${message.kind} pl-note`}>{message.text}</div>}

      <section className="su-card cw-card">
        <h3>Defaults</h3>
        <p className="su-hint">
          {view.fte_hours ? `${format(view.fte_hours)} hours per FTE a year, from the round’s setup.` : 'No hours per FTE set in the round’s setup.'}{' '}
          Positions without their own line or share take these.
        </p>
        <div className="su-body su-row cw-fields">
          <div className="su-field cw-w-line">
            Line
            <Select
              label="Default line"
              value={lineKey(view.default_work_center_code, view.default_activity_type_code)}
              options={[{ value: '', label: 'None' }, ...lines]}
              disabled={!open}
              onChange={(key) => saveDefaults(lineOf(key), view.default_percent)}
            />
          </div>
          <div className="su-field">
            Productive share
            <PercentInput
              value={view.default_percent}
              label="Default productive share"
              disabled={!open}
              onSave={(percent) => saveDefaults(defaultLine, percent)}
            />
          </div>
        </div>
      </section>

      <section className="su-card cw-card">
        <h3>Positions</h3>
        <p className="su-hint">Hours = hours per FTE × contract % × productive share, for the days a position is active.</p>
        {view.positions.length === 0 ? (
          <p className="su-empty">No positions on {view.cost_center_code}. They come from the Workforce page.</p>
        ) : (
          <div className="tbl-scroll">
            <table className="tbl cw-table">
              <thead>
                <tr>
                  <th>Number</th>
                  <th>Position</th>
                  <th>Type</th>
                  <th className="num">Contract</th>
                  <th>Productive</th>
                  <th>Line</th>
                  <th className="num">Hours</th>
                </tr>
              </thead>
              <tbody>
                {view.positions.map((p) => (
                  <tr key={p.position_id}>
                    <td className="pl-code">{p.code}</td>
                    <td>
                      {p.title}
                      {p.person_name && <span className="ink-3"> · {p.person_name}</span>}
                      {(p.entry_date || p.leave_date) && (
                        <div className="su-hint">
                          {p.entry_date && `from ${p.entry_date}`}
                          {p.entry_date && p.leave_date && ' · '}
                          {p.leave_date && `until ${p.leave_date}`}
                        </div>
                      )}
                    </td>
                    <td>{p.staff_type}</td>
                    <td className="num">{p.contract_percent === null ? '' : `${pct(p.contract_percent)} %`}</td>
                    <td>
                      <PercentInput
                        value={p.productive_percent}
                        placeholder={pct(view.default_percent)}
                        label={`Productive share of ${p.code}`}
                        disabled={!open}
                        onSave={(percent) => savePosition(p, own(p), percent)}
                      />
                    </td>
                    <td className="cw-line">
                      <Select
                        variant="compact"
                        label={`Line of ${p.code}`}
                        value={lineKey(p.work_center_code, p.activity_type_code)}
                        options={[{ value: '', label: defaultLabel }, ...lines]}
                        disabled={!open}
                        onChange={(key) => savePosition(p, lineOf(key), p.productive_percent)}
                      />
                    </td>
                    <td className="num">{p.hours === null ? '' : format(p.hours)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={6}>Total</td>
                  <td className="num">{format(sum(view.positions.map((p) => p.hours)))}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </section>

      {staffed.length > 0 && (
        <section className="su-card cw-card">
          <h3>Lines</h3>
          <p className="su-hint">Hours from the positions, per line.</p>
          <div className="tbl-scroll">
            <table className="tbl cw-table">
              <thead>
                <tr>
                  <th>Line</th>
                  <th className="num">Rate / h</th>
                  <th className="num">Hours</th>
                </tr>
              </thead>
              <tbody>
                {staffed.map((l) => (
                  <tr key={lineKey(l.work_center_code, l.activity_type_code)}>
                    <td>
                      <span className="pl-code">{l.work_center_code}</span> {l.work_center_name} · {l.activity_type_name}
                    </td>
                    <td className="num">{l.rate}</td>
                    <td className="num">{format(l.hours)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  )
}
