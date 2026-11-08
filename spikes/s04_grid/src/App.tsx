import { useMemo, useState } from 'react'
import fixture from './fixture.json'
import GlideView from './GlideView'
import { PlanModel, stressModel } from './model'
import RevoView from './RevoView'

const TABS = ['Glide', 'RevoGrid', 'Glide 50k', 'RevoGrid 50k'] as const
type Tab = (typeof TABS)[number]

export default function App() {
  const [tab, setTab] = useState<Tab>('Glide')
  const [errors, setErrors] = useState<string[]>([])
  // Separate models per tab, so each grid starts empty.
  const models = useMemo(
    () => ({ Glide: new PlanModel(), RevoGrid: new PlanModel(), 'Glide 50k': stressModel(3900), 'RevoGrid 50k': stressModel(3900) }),
    [],
  )
  // Exposed for the headless smoke test (smoke.mjs).
  ;(window as unknown as { __models: typeof models }).__models = models
  const model = models[tab]
  const cc = (fixture as { costCenter: { cost_center_code: string; cost_center_name: string } }).costCenter

  return (
    <div className="app">
      <header>
        <strong>S-04 grid shoot-out</strong>
        <span>
          Cost center {cc.cost_center_code} {cc.cost_center_name} · budget 2027 · PY = 2025 actuals
        </span>
        <nav>
          {TABS.map((t) => (
            <button key={t} className={t === tab ? 'active' : ''} onClick={() => { setTab(t); setErrors([]) }}>
              {t}
            </button>
          ))}
        </nav>
      </header>
      <p className="hint">
        Type in months or Plan FY (spreads by PY), or on a blue group row (spreads to its accounts). Grey rows are
        locked. Paste from Excel/LibreOffice. Blank ≠ 0. Rows: {model.rows.length.toLocaleString()} × 15 columns.
      </p>
      {errors.length > 0 && (
        <div className="errors">
          {errors.length} cell(s) refused: {errors.slice(0, 3).join(' · ')}
          {errors.length > 3 ? ' …' : ''}
        </div>
      )}
      {tab.startsWith('Glide') ? (
        <GlideView key={tab} model={model} onErrors={setErrors} />
      ) : (
        <RevoView key={tab} model={model} onErrors={setErrors} />
      )}
    </div>
  )
}
