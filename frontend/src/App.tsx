import { useEffect, useState } from 'react'

// Mirrors planlayer.api.app.Health. Replaced by the generated API client (F-04).
type Health = {
  status: 'ok' | 'unavailable'
  version: string
  schema_revision: string | null
}

type State =
  | { kind: 'loading' }
  | { kind: 'loaded'; health: Health }
  | { kind: 'failed'; message: string }

export default function App() {
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    fetch('/api/health')
      .then(async (response) => {
        const health = (await response.json()) as Health
        setState({ kind: 'loaded', health })
      })
      .catch((error: unknown) => {
        setState({ kind: 'failed', message: String(error) })
      })
  }, [])

  return (
    <main>
      <h1>Planlayer</h1>
      {state.kind === 'loading' && <p>Checking the backend…</p>}
      {state.kind === 'failed' && <p className="error">Backend not reachable: {state.message}</p>}
      {state.kind === 'loaded' && (
        <dl>
          <dt>Backend</dt>
          <dd className={state.health.status}>{state.health.status}</dd>
          <dt>Version</dt>
          <dd>{state.health.version}</dd>
          <dt>Schema revision</dt>
          <dd>{state.health.schema_revision ?? 'not migrated'}</dd>
        </dl>
      )}
    </main>
  )
}
