import { useEffect, useState } from 'react'
import PlanningPage from './PlanningPage'

type Health = { status: 'ok' | 'unavailable'; version: string; schema_revision: string | null }

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.json() as Promise<Health>)
      .then(setHealth, () => setHealth({ status: 'unavailable', version: '', schema_revision: null }))
  }, [])

  return (
    <div className="app">
      <header>
        <h1>Planlayer</h1>
        {health && (
          <span className={`health ${health.status}`}>
            {health.status === 'ok' ? `v${health.version} · schema ${health.schema_revision ?? '–'}` : 'backend unavailable'}
          </span>
        )}
      </header>
      {health?.status === 'ok' && <PlanningPage />}
    </div>
  )
}
