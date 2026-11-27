import type { MemberRow, Version } from './api'
import Select from './Select'
import { closedCount, monthName } from './periods'
import type { ComparisonColumn, Profile, Rule } from './setupTypes'
import { type DraftProps, formOptions, formsFor, type Level, PROFILE_FORMS, REFERENCE_FORMS, accountOptions } from './setupShared'

// The setup page's starting points, entry forms and comparison columns (E-03, ADR 0007).

export function LevelCard(props: {
  level: Level
  current: Level | null
  title: string
  text: string
  icon: React.ReactNode
  disabled?: boolean
  onPick: (l: Level) => void
}) {
  return (
    <button
      type="button"
      className="su-level"
      aria-pressed={props.current === props.level}
      disabled={props.disabled}
      onClick={() => props.onPick(props.level)}
    >
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        {props.icon}
      </svg>
      <strong>{props.title}</strong>
      <span>{props.text}</span>
    </button>
  )
}

export function EntryForms({
  draft,
  setDraft,
  accounts,
  profiles,
  rounds,
  current,
}: DraftProps & { accounts: MemberRow[]; profiles: Profile[]; rounds: Version[]; current: string }) {
  const forms = formsFor(draft.grain)
  const explain = forms.find((f) => f.form === draft.default_form)?.explain
  const update = (i: number, rule: Rule) => setDraft({ ...draft, entry_forms: draft.entry_forms.map((r, j) => (j === i ? rule : r)) })
  const free = accounts.find((a) => a.is_group && !draft.entry_forms.some((r) => r.account_code === a.code))
  return (
    <section className="su-card">
      <h3>
        <span className="su-step">3</span> Entry forms
      </h3>
      <p className="su-hint">How planners enter values: a default, and exceptions per account group or account.</p>
      <div className="su-body">
        <div className="su-sub">Default</div>
        <div className="su-forms" role="group" aria-label="Default entry form">
          {forms.map((f) => (
            <button
              key={f.form}
              type="button"
              aria-pressed={draft.default_form === f.form}
              onClick={() => setDraft({ ...draft, default_form: f.form })}
            >
              {f.label}
            </button>
          ))}
        </div>
        <p className="su-explain">{explain}</p>
        <p className="su-hint su-form-change">
          Every form stores months. Changing a form keeps the months already stored, carried over or typed: a line
          switched to a year form shows their total, and only a year total typed afterwards is spread the new way.
        </p>
        <div className="su-sub">Exceptions · the nearest one above an account wins</div>
        <div className="su-rules">
          {draft.entry_forms.map((r, i) => (
            <div className="su-rule" key={i}>
              <Select
                label="Account or group"
                value={r.account_code}
                options={accountOptions(accounts, true).map((o) =>
                  o.value !== r.account_code && draft.entry_forms.some((other) => other.account_code === o.value)
                    ? { ...o, disabled: true, hint: 'has an exception' }
                    : o,
                )}
                searchable
                onChange={(code) => update(i, { ...r, account_code: code })}
              />
              <Select
                label="Entry form"
                value={r.form}
                options={formOptions(draft.grain)}
                onChange={(form) => {
                  update(i, {
                    ...r,
                    form,
                    profile_code: PROFILE_FORMS.includes(form)
                      ? (r.profile_code ?? (form === 'annual_profile' ? (profiles[0]?.code ?? null) : null))
                      : null,
                    reference_version_code: REFERENCE_FORMS.includes(form) ? r.reference_version_code : null,
                  })
                }}
              />
              {PROFILE_FORMS.includes(r.form) && (r.form === 'annual_profile' || !r.reference_version_code) ? (
                <Select
                  label="Seasonality profile"
                  value={r.profile_code ?? ''}
                  placeholder="choose a profile"
                  options={[
                    ...(r.form === 'monthly' ? [{ value: '', label: 'by last year’s actuals' }] : []),
                    ...profiles.map((p) => ({ value: p.code, label: `season: ${p.name}` })),
                  ]}
                  onChange={(code) => update(i, { ...r, profile_code: code || null })}
                />
              ) : REFERENCE_FORMS.includes(r.form) ? (
                <Select
                  label="Spread reference"
                  value={r.reference_version_code ?? ''}
                  options={[
                    { value: '', label: 'like last year’s actuals' },
                    ...rounds.filter((v) => v.code !== current).map((v) => ({ value: v.code, label: `like ${v.name}` })),
                  ]}
                  onChange={(code) => update(i, { ...r, reference_version_code: code || null })}
                />
              ) : (
                <span />
              )}
              <button
                type="button"
                className="su-icon"
                aria-label={`Remove exception for ${r.account_code}`}
                onClick={() => setDraft({ ...draft, entry_forms: draft.entry_forms.filter((_, j) => j !== i) })}
              >
                ✕
              </button>
            </div>
          ))}
          {draft.entry_forms.length === 0 && <div className="su-empty">No exceptions: every account uses the default.</div>}
        </div>
        <div className="su-row" style={{ marginTop: 8 }}>
          <button
            type="button"
            className="su-btn"
            disabled={!free}
            onClick={() =>
              free &&
              setDraft({ ...draft, entry_forms: [...draft.entry_forms, { account_code: free.code, form: 'annual_even' }] })
            }
          >
            Add exception
          </button>
        </div>
      </div>
    </section>
  )
}

/** The same months a year earlier: the prior fiscal year a budget's projection counts. */
const yearEarlier = (periods: string[]) => periods.map((p) => `${Number(p.slice(0, 4)) - 1}${p.slice(4)}`)

export function Comparisons({
  draft,
  setDraft,
  rounds,
  current,
  actuals,
  periods,
  latest,
}: DraftProps & { rounds: Version[]; current: string; actuals: boolean; periods: string[]; latest: string | null }) {
  const label = (c: ComparisonColumn) =>
    c.kind === 'prior_year_actuals'
      ? 'Prior-year actuals'
      : c.kind === 'actuals'
        ? 'Actuals this year, to date'
        : c.kind === 'projection'
          ? 'Projection of actuals'
          : (rounds.find((r) => r.code === c.version_code)?.name ?? c.version_code)
  // A forecast with closed months projects its own year from them; any other round the prior year.
  const closed = closedCount(periods, draft.actuals_through)
  const prior = yearEarlier(periods)
  const proposed = latest ? (prior.filter((p) => p <= latest).at(-1) ?? null) : null
  const setThrough = (through: string | null) =>
    setDraft({
      ...draft,
      comparisons: draft.comparisons.map((x) => (x.kind === 'projection' ? { ...x, through } : x)),
    })
  const projectionThrough = (c: ComparisonColumn) =>
    closed > 0 ? (
      <span className="su-cmp-through">through {monthName(periods[closed - 1])}, the actuals through</span>
    ) : (
      <span className="su-cmp-through">
        <Select
          variant="compact"
          label="Projection counts actuals through"
          display={c.through ? `through ${monthName(c.through)}` : 'through the latest actuals'}
          value={c.through ?? ''}
          options={[
            { value: '', label: 'Latest actuals', hint: 'moves with each sync' },
            ...prior.map((p) => {
              const n = closedCount(prior, p)
              return { value: p, label: monthName(p), hint: `${n}+${12 - n}${p === latest ? ' · latest actuals' : ''}` }
            }),
          ]}
          onChange={(v) => setThrough(v || null)}
        />
        {c.through && latest && c.through > latest && (
          <span className="su-note warn">No actuals after {monthName(latest)} yet; the months up to it count as zero.</span>
        )}
      </span>
    )
  const move = (i: number, by: number) => {
    const list = [...draft.comparisons]
    ;[list[i], list[i + by]] = [list[i + by], list[i]]
    setDraft({ ...draft, comparisons: list })
  }
  const has = (c: ComparisonColumn) =>
    draft.comparisons.some((x) => x.kind === c.kind && (x.version_code ?? null) === (c.version_code ?? null))
  const choices: ComparisonColumn[] = [
    ...(actuals
      ? ([
          { kind: 'prior_year_actuals' },
          { kind: 'actuals' },
          { kind: 'projection', through: closed > 0 ? null : proposed },
        ] as ComparisonColumn[])
      : []),
    ...rounds.filter((r) => r.code !== current).map((r) => ({ kind: 'version' as const, version_code: r.code })),
  ].filter((c) => !has(c))
  return (
    <section className="su-card">
      <h3>
        <span className="su-step">4</span> Comparison columns
      </h3>
      <p className="su-hint">
        Shown before the plan, in this order. A note shows as the column’s tooltip on the sheet; actuals columns also
        say there how far their actuals go, e.g. January to June. A projection continues the booked months at their
        monthly average to the full year.{!actuals && ' Actuals appear here once they are mapped.'}
      </p>
      <div className="su-body">
        <ul className="su-list">
          {draft.comparisons.map((c, i) => (
            <li key={`${c.kind}-${c.version_code ?? ''}`} className="su-cmp">
              <span className="su-cmp-name">{label(c)}</span>
              {c.kind === 'projection' && projectionThrough(c)}
              <input
                className="su-input su-cmp-note"
                value={c.note ?? ''}
                maxLength={200}
                placeholder="Note to planners, e.g. January to June only"
                aria-label={`Note on ${label(c)}`}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    comparisons: draft.comparisons.map((x, j) => (j === i ? { ...x, note: e.target.value || null } : x)),
                  })
                }
              />
              <button type="button" className="su-icon" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}>
                ↑
              </button>
              <button
                type="button"
                className="su-icon"
                aria-label="Move down"
                disabled={i === draft.comparisons.length - 1}
                onClick={() => move(i, 1)}
              >
                ↓
              </button>
              <button
                type="button"
                className="su-icon"
                aria-label={`Remove ${label(c)}`}
                onClick={() => setDraft({ ...draft, comparisons: draft.comparisons.filter((_, j) => j !== i) })}
              >
                ✕
              </button>
            </li>
          ))}
          {draft.comparisons.length === 0 && <li className="su-empty">No comparison columns.</li>}
        </ul>
        <Select
          variant="action"
          label="Add a comparison column"
          display="Add a column"
          icon={<span className="pl-plus">+</span>}
          value=""
          disabled={choices.length === 0}
          options={choices.map((c, i) => ({ value: String(i), label: label(c) ?? '' }))}
          onChange={(i) => {
            const c = choices[Number(i)]
            if (c) setDraft({ ...draft, comparisons: [...draft.comparisons, c] })
          }}
        />
      </div>
    </section>
  )
}
