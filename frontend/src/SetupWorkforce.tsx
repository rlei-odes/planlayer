import { useState } from 'react'
import { api, type Version } from './api'
import { type SetupDraft, type WorkforceMode } from './setupTypes'

// Workforce planning on the setup page (ADR 0020): the round's mode, staff from HR, and personnel
// costs from the positions (ADR 0022).

const WORKFORCE_MODES: { value: WorkforceMode | ''; label: string; text: string }[] = [
  { value: '', label: 'Off', text: 'No workforce sheet in this round.' },
  { value: 'fte', label: 'FTE', text: 'Planners type full-time equivalents per staff type × category and month.' },
  { value: 'headcount', label: 'Headcount', text: 'Planners type heads per staff type × category and month.' },
  { value: 'both', label: 'FTE and headcount', text: 'Both, side by side on one sheet.' },
  {
    value: 'positions',
    label: 'Positions',
    text: 'Planners list each position: who, from when to when, at what contract %. FTE and headcount roll up from them.',
  },
]

/** Workforce planning (ADR 0020): the round's mode, and HR's staff as a start for positions. */
export function Workforce({
  draft,
  setDraft,
  saved,
  savedPersonnel,
  round,
  onNote,
}: {
  draft: SetupDraft
  setDraft: (d: SetupDraft) => void
  saved: WorkforceMode | null
  savedPersonnel: boolean
  round: Version
  onNote: (n: { kind: 'ok' | 'error' | 'warn'; text: string }) => void
}) {
  const [taking, setTaking] = useState(false)
  const mode = draft.workforce_mode ?? ''
  const changing = saved !== null && (draft.workforce_mode ?? null) !== saved

  async function takeAll() {
    setTaking(true)
    try {
      const { added } = await api.workforceFromHr(round.code)
      onNote({
        kind: 'ok',
        text: added
          ? `${added} people from HR’s staff list added as positions of their cost centers.`
          : 'Every person in HR’s staff list is a position already, or their cost center can’t be changed.',
      })
    } catch (e) {
      onNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setTaking(false)
    }
  }

  return (
    <section className="su-card">
      <h3>
        <span className="su-step">6</span> Workforce planning
      </h3>
      <p className="su-hint">
        FTE and headcount per cost center, by staff type and category, as month-end levels: the year shows December’s
        value, and a year value typed fills every month. Cost center owners and their deputies plan it next to their
        costs, under the same status.
      </p>
      <div className="su-body wf-modes" role="radiogroup" aria-label="Workforce mode">
        {WORKFORCE_MODES.map((m) => (
          <label key={m.value} className={`wf-mode${mode === m.value ? ' on' : ''}`}>
            <input
              type="radio"
              name="workforce-mode"
              checked={mode === m.value}
              onChange={() =>
                setDraft({
                  ...draft,
                  workforce_mode: m.value === '' ? null : m.value,
                  // Personnel costs come from positions: another mode turns them off.
                  personnel_costs: m.value === 'positions' && !!draft.personnel_costs,
                })
              }
            />
            <span>
              <strong>{m.label}</strong>
              <span className="su-hint">{m.text}</span>
            </span>
          </label>
        ))}
      </div>
      {changing && (
        <div className="su-note warn">
          {round.status === 'setup'
            ? 'Saving clears the workforce values, lines and positions this round has so far.'
            : 'An open round with workforce values or positions keeps its mode; the save is refused while there are any.'}
        </div>
      )}
      {mode === 'positions' && (
        <div className="su-body">
        <label className={`wf-mode wf-personnel${draft.personnel_costs ? ' on' : ''}`}>
          <input
            type="checkbox"
            checked={!!draft.personnel_costs}
            onChange={(e) => setDraft({ ...draft, personnel_costs: e.target.checked })}
          />
          <span>
            <strong>Personnel costs from the positions</strong>
            <span className="su-hint">
              Pay per position, with the round’s rules for extras, raises and contributions, written to the personnel
              accounts. Pay is read by HR and the cost center’s owner only; HR sets up the rules on the Personnel costs
              page.
              {savedPersonnel && !draft.personnel_costs &&
                (round.status === 'setup'
                  ? ' Saving clears the rules, the pay and the values they wrote.'
                  : ' An open round with personnel cost values keeps them; the save is refused.')}
            </span>
          </span>
        </label>
        </div>
      )}
      {saved === 'positions' && !changing && (
        <div className="su-body su-row">
          <button type="button" className="su-btn" disabled={taking || round.status === 'closed'} onClick={() => void takeAll()}>
            {taking ? 'Adding…' : 'Add staff from HR'}
          </button>
          <span className="su-hint">
            For every planned cost center: the people in HR’s staff list active in {round.fiscal_year} who aren’t
            positions yet. Planners can do the same on their sheet.
          </span>
        </div>
      )}
    </section>
  )
}
