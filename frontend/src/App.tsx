import { useCallback, useEffect, useState } from 'react'
import { api, type Me, type Version } from './api'
import LoginPage from './LoginPage'
import MasterDataPage from './MasterDataPage'
import PlanningPage from './PlanningPage'
import PublishPage from './PublishPage'
import ResponsibilitiesPage from './ResponsibilitiesPage'
import SetupPage from './SetupPage'
import StatusPage from './StatusPage'
import UsersPage from './UsersPage'

type Health = { status: 'ok' | 'unavailable'; version: string; schema_revision: string | null }
type Page = 'planning' | 'status' | 'setup' | 'publish' | 'masterdata' | 'responsibilities' | 'users'

const LABEL: Record<Page, string> = {
  planning: 'Planning',
  status: 'Status',
  setup: 'Setup',
  publish: 'Publish',
  masterdata: 'Master data',
  responsibilities: 'Responsibilities',
  users: 'Users',
}

function pagesFor(me: Me): Page[] {
  const pages: Page[] = ['planning', 'status']
  if (me.is_controller) pages.push('setup', 'publish')
  if (me.is_controller || me.is_admin) pages.push('masterdata')
  if (me.is_controller) pages.push('responsibilities')
  if (me.is_admin) pages.push('users')
  return pages
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [me, setMe] = useState<Me | null | undefined>(undefined) // undefined: not known yet
  const [versions, setVersions] = useState<Version[]>([])
  const [version, setVersion] = useState('')
  const [page, setPage] = useState<Page>(() => (window.location.hash.slice(1) as Page) || 'planning')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.json() as Promise<Health>)
      .then(setHealth, () => setHealth({ status: 'unavailable', version: '', schema_revision: null }))
  }, [])

  useEffect(() => {
    if (health?.status !== 'ok') return
    api.me().then(setMe, () => setMe(null))
    const ended = () => setMe(null)
    window.addEventListener('planlayer:unauthorized', ended)
    return () => window.removeEventListener('planlayer:unauthorized', ended)
  }, [health])

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
    if (me) loadVersions()
  }, [me, loadVersions])

  function go(next: Page) {
    setPage(next)
    window.location.hash = next
  }

  async function logout() {
    await api.logout().catch(() => undefined)
    setMe(null)
  }

  const pages = me ? pagesFor(me) : []
  const current = pages.includes(page) ? page : 'planning'
  const roles = me ? [me.is_admin && 'admin', me.is_controller && 'controller', me.is_viewer && 'viewer'].filter(Boolean) : []
  return (
    <div className="app">
      <header>
        <h1>Planlayer</h1>
        {me && (
          <nav>
            {pages.map((p) => (
              <button type="button" key={p} className={current === p ? 'tab active' : 'tab'} onClick={() => go(p)}>
                {LABEL[p]}
              </button>
            ))}
          </nav>
        )}
        {me && versions.length > 0 && (current === 'planning' || current === 'status') && (
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
        {me && (
          <span className="me">
            {me.display_name}
            {roles.length > 0 && <span className="muted"> ({roles.join(', ')})</span>}{' '}
            <button type="button" onClick={() => void logout()}>
              Log out
            </button>
          </span>
        )}
      </header>
      {error && <div className="error">{error}</div>}
      {me === null && <LoginPage onLogin={setMe} />}
      {me && versions.length === 0 && (current === 'planning' || current === 'status') && (
        <p>No planning round yet. {me.is_controller ? 'Create one under Setup.' : 'The controller creates one.'}</p>
      )}
      {me && version && current === 'planning' && <PlanningPage key={`${me.login}/${version}`} version={version} />}
      {me && version && current === 'status' && (
        <StatusPage key={`${me.login}/${version}`} version={version} me={me} onRoundChanged={loadVersions} />
      )}
      {me && current === 'setup' && <SetupPage onRoundsChanged={loadVersions} />}
      {me && current === 'publish' && <PublishPage />}
      {me && current === 'masterdata' && <MasterDataPage me={me} />}
      {me && current === 'responsibilities' && <ResponsibilitiesPage />}
      {me && current === 'users' && <UsersPage />}
    </div>
  )
}
