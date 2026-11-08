import { useCallback, useEffect, useState } from 'react'
import { api, type Version } from './api'
import PlanningPage from './PlanningPage'
import StatusPage from './StatusPage'

type Health = { status: 'ok' | 'unavailable'; version: string; schema_revision: string | null }
type Page = 'planning' | 'status'

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [versions, setVersions] = useState<Version[]>([])
  const [version, setVersion] = useState('')
  const [page, setPage] = useState<Page>(() => (window.location.hash === '#status' ? 'status' : 'planning'))
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.json() as Promise<Health>)
      .then(setHealth, () => setHealth({ status: 'unavailable', version: '', schema_revision: null }))
  }, [])

  const loadVersions = useCallback(() => {
    api.versions().then(
      (vs) => {
        setVersions(vs)
        setVersion((current) => (vs.some((v) => v.code === current) ? current : (vs[0]?.code ?? '')))
      },
      (e: unknown) => setError(e instanceof Error ? e.message : String(e)),
    )
  }, [])

  useEffect(() => {
    if (health?.status === 'ok') loadVersions()
  }, [health, loadVersions])

  function go(next: Page) {
    setPage(next)
    window.location.hash = next
  }

  return (
    <div className="app">
      <header>
        <h1>Planlayer</h1>
        <nav>
          {(['planning', 'status'] as const).map((p) => (
            <button type="button" key={p} className={page === p ? 'tab active' : 'tab'} onClick={() => go(p)}>
              {p === 'planning' ? 'Planning' : 'Status'}
            </button>
          ))}
        </nav>
        {versions.length > 0 && (
          <label>
            Round{' '}
            <select value={version} onChange={(e) => setVersion(e.target.value)}>
              {versions.map((v) => (
                <option key={v.code} value={v.code}>
                  {v.name} · {v.status}
                </option>
              ))}
            </select>
          </label>
        )}
        {health && (
          <span className={`health ${health.status}`}>
            {health.status === 'ok' ? `v${health.version} · schema ${health.schema_revision ?? '–'}` : 'backend unavailable'}
          </span>
        )}
      </header>
      {error && <div className="error">{error}</div>}
      {health?.status === 'ok' && versions.length === 0 && (
        <p>No planning round yet. Create one with `planlayer create-version`.</p>
      )}
      {version && page === 'planning' && <PlanningPage key={version} version={version} />}
      {version && page === 'status' && <StatusPage key={version} version={version} onRoundChanged={loadVersions} />}
    </div>
  )
}
