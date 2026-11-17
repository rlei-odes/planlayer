import { useState } from 'react'
import { Bars } from './PlannerPreview'
import Select from './Select'
import type { Profile } from './setupTypes'
import { type ProfilePreset, setupApi } from './setupShared'
import { LIMIT } from './limits'

// Seasonality profiles on the setup page (E-03): add, edit, delete, start from working days.

const MONTH = new Intl.DateTimeFormat('en', { month: 'short', timeZone: 'UTC' })

const PRESETS: { preset: ProfilePreset; label: string }[] = [
  { preset: 'working_days', label: 'Working days' },
  { preset: 'calendar_days', label: 'Calendar days' },
]

const PRESET_NAME = /^(Working|Calendar) days \d{4}$/

export function Profiles({
  profiles,
  periods,
  fiscalYear,
  onChanged,
}: {
  profiles: Profile[]
  periods: string[]
  fiscalYear: number
  onChanged: () => void
}) {
  // null: closed; '': a new profile; otherwise the code of the profile being edited.
  const [editing, setEditing] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [enteredAs, setEnteredAs] = useState<'percent' | 'relative'>('relative')
  const [values, setValues] = useState<string[]>(Array(12).fill('1'))
  const [presetYear, setPresetYear] = useState('')
  const [deleting, setDeleting] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const months = periods.length === 12 ? periods.map((d) => MONTH.format(new Date(d))) : []
  const numbers = values.map((v) => Number(v.replace(',', '.')))
  const total = numbers.reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0)
  const valid =
    name.trim() !== '' &&
    numbers.every((n) => Number.isFinite(n) && n >= 0) &&
    total > 0 &&
    (enteredAs === 'relative' || Math.abs(total - 100) < 1e-9)
  const year = Number(presetYear)
  const yearValid = Number.isInteger(year) && year >= 1900 && year <= 2200

  function open(p: Profile | null) {
    setEditing(p?.code ?? '')
    setName(p?.name ?? '')
    setEnteredAs(p?.entered_as ?? 'relative')
    setValues(p ? p.values.map(String) : Array(12).fill('1'))
    setPresetYear(String(fiscalYear))
    setDeleting(null)
    setError(null)
  }

  async function fill(preset: ProfilePreset, label: string) {
    try {
      const days = await setupApi.preset(preset, year)
      setValues(days.map(String))
      setEnteredAs('relative')
      if (name.trim() === '' || PRESET_NAME.test(name.trim())) setName(`${label} ${year}`)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function save() {
    const trimmed = name.trim()
    const body = { name: trimmed, entered_as: enteredAs, values: values.map((v) => v.replace(',', '.')) }
    try {
      if (editing) await setupApi.updateProfile({ ...body, code: editing })
      else await setupApi.addProfile({ ...body, code: profileCode(trimmed) })
      setEditing(null)
      setError(null)
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function remove(code: string) {
    try {
      await setupApi.deleteProfile(code)
      setDeleting(null)
      if (editing === code) setEditing(null)
      setError(null)
      onChanged()
    } catch (e) {
      setDeleting(null)
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <section className="su-card">
      <h3>
        <span className="su-step">5</span> Seasonality profiles
      </h3>
      <p className="su-hint">Shared by all rounds. Entered as percentages (summing to 100) or as any relative numbers.</p>
      <div className="su-body">
        <div className="su-profiles">
          {profiles.map((p) => (
            <div className={`su-profile${editing === p.code ? ' editing' : ''}`} key={p.code}>
              <Bars values={p.values.map(Number)} height={22} />
              <div className="meta">
                <strong>{p.name}</strong>
                <span>{p.entered_as === 'percent' ? 'percentages' : 'relative numbers'}</span>
              </div>
              {deleting === p.code ? (
                <span className="su-actions su-confirm">
                  Delete?
                  <button type="button" className="su-btn danger" onClick={() => void remove(p.code)}>
                    Delete
                  </button>
                  <button type="button" className="su-btn ghost" onClick={() => setDeleting(null)}>
                    Keep
                  </button>
                </span>
              ) : (
                <span className="su-actions">
                  <button type="button" className="su-btn ghost" aria-label={`Edit ${p.name}`} onClick={() => open(p)}>
                    Edit
                  </button>
                  <button
                    type="button"
                    className="su-btn ghost"
                    aria-label={`Delete ${p.name}`}
                    onClick={() => {
                      setDeleting(p.code)
                      setError(null)
                    }}
                  >
                    Delete
                  </button>
                </span>
              )}
            </div>
          ))}
          {profiles.length === 0 && <div className="su-empty">No profiles yet.</div>}
        </div>
        {editing === null ? (
          <button type="button" className="su-btn" style={{ marginTop: 10 }} onClick={() => open(null)}>
            New profile
          </button>
        ) : (
          <div className="su-profile-form">
            <div className="su-row">
              <label className="su-field" style={{ flex: 1 }}>
                Name
                <input maxLength={LIMIT.name} className="su-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Retail with Q4 peak" />
              </label>
              <div className="su-field su-w-select">
                Entered as
                <Select
                  label="Entered as"
                  value={enteredAs}
                  options={[
                    { value: 'relative', label: 'relative numbers' },
                    { value: 'percent', label: 'percentages' },
                  ]}
                  onChange={setEnteredAs}
                />
              </div>
            </div>
            <div className="su-field su-presets">
              <label className="su-row">
                Fill with the days of fiscal year
                <input maxLength={4}
                  className="su-input su-year"
                  inputMode="numeric"
                  aria-label="Fiscal year"
                  value={presetYear}
                  onChange={(e) => setPresetYear(e.target.value.trim())}
                />
              </label>
              <span className="su-row">
                {PRESETS.map(({ preset, label }) => (
                  <button key={preset} type="button" className="su-btn" disabled={!yearValid} onClick={() => void fill(preset, label)}>
                    {label}
                  </button>
                ))}
              </span>
              <span className="su-presets-hint">Working days leave out weekends; deduct holidays and shutdowns by hand.</span>
            </div>
            <div className="su-months">
              {values.map((v, i) => (
                <label key={i}>
                  {months[i] ?? `M${i + 1}`}
                  <input maxLength={LIMIT.number}
                    inputMode="decimal"
                    aria-label={`${months[i] ?? `Month ${i + 1}`} value`}
                    value={v}
                    onChange={(e) => setValues(values.map((w, j) => (j === i ? e.target.value : w)))}
                  />
                </label>
              ))}
            </div>
            <div className="su-row" style={{ justifyContent: 'space-between' }}>
              <span className="su-row">
                <Bars values={numbers.map((n) => (Number.isFinite(n) && n > 0 ? n : 0))} height={22} />
                <span className={`su-sum ${enteredAs === 'percent' ? (Math.abs(total - 100) < 1e-9 ? 'good' : 'bad') : ''}`}>
                  sum {Number.isFinite(total) ? total.toLocaleString('en-US', { maximumFractionDigits: 4 }) : '–'}
                  {enteredAs === 'percent' && ' of 100'}
                </span>
              </span>
              <span className="su-row">
                <button type="button" className="su-btn ghost" onClick={() => setEditing(null)}>
                  Cancel
                </button>
                <button type="button" className="su-btn primary" disabled={!valid} onClick={() => void save()}>
                  {editing ? 'Save profile' : 'Create profile'}
                </button>
              </span>
            </div>
            {editing && (
              <p className="su-hint" style={{ margin: '8px 0 0' }}>
                Changes apply to year totals typed from now on; months already planned keep their values.
              </p>
            )}
          </div>
        )}
        {error && <div className="su-note error" style={{ marginTop: 8 }}>{error}</div>}
      </div>
    </section>
  )
}

function profileCode(name: string): string {
  return name.toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 20)
}
