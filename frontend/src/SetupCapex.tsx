import { type Version } from './api'
import { type SetupDraft } from './setupTypes'

// Capex planning on the setup page (ADR 0028): on or off, the threshold above which an investment
// is better planned as a large project, and whether planners schedule cash out.

export function Capex({ draft, setDraft, saved, round }: { draft: SetupDraft; setDraft: (d: SetupDraft) => void; saved: boolean; round: Version }) {
  const capex = draft.capex ?? null
  const on = capex !== null
  return (
    <section className="su-card">
      <h3>
        <span className="su-step">7</span> Capex planning
      </h3>
      <p className="su-hint">
        Investments per cost center as one list, confirmed by approvers above the cost center and by controllers.
      </p>
      <div className="su-body">
        <label className={`wf-mode${on ? ' on' : ''}`}>
          <input
            type="checkbox"
            checked={on}
            onChange={(e) => setDraft({ ...draft, capex: e.target.checked ? { project_threshold: null, cash_out: false } : null })}
          />
          <span>
            <strong>Plan investments</strong>
            <span className="su-hint">
              Name, type, cost center, priority, start and amount; large projects group lines with their own dates.
              {saved && !on && ' The save is refused while the round has investments.'}
            </span>
          </span>
        </label>
      </div>
      {capex && (
        <div className="su-body cx-setup">
          <label className="su-field">
            Large project from
            <input
              className="su-input"
              inputMode="decimal"
              value={capex.project_threshold ?? ''}
              placeholder="e.g. 1000000"
              onChange={(e) =>
                setDraft({ ...draft, capex: { ...capex, project_threshold: e.target.value.trim() === '' ? null : e.target.value } })
              }
            />
            <span className="su-hint">A single investment above it is flagged for a breakdown into a large project.</span>
          </label>
          <label className={`wf-mode${capex.cash_out ? ' on' : ''}`}>
            <input
              type="checkbox"
              checked={!!capex.cash_out}
              onChange={(e) => setDraft({ ...draft, capex: { ...capex, cash_out: e.target.checked } })}
            />
            <span>
              <strong>Cash-out schedule</strong>
              <span className="su-hint">
                Planners spread each investment’s amount over the {round.grain === 'quarter' ? 'quarters' : 'months'} of{' '}
                {round.fiscal_year}, and {round.fiscal_year + 1} as one period.
              </span>
            </span>
          </label>
        </div>
      )}
    </section>
  )
}
