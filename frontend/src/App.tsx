import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { api, type Me, type Version } from './api'
import {
  CollapseIcon,
  LogoMark,
  LogoutIcon,
  MasterDataIcon,
  PlanningIcon,
  PublishIcon,
  ResponsibilitiesIcon,
  SetupIcon,
  StatusIcon,
  UsersIcon,
} from './icons'
import LoginPage from './LoginPage'
import { initials } from './names'
import Select from './Select'
import MasterDataPage from './MasterDataPage'
import PlanningPage from './PlanningPage'
import PublishPage from './PublishPage'
import ResponsibilitiesPage from './ResponsibilitiesPage'
import SetupPage from './SetupPage'
import StatusPage from './StatusPage'
import UsersPage from './UsersPage'

// The app shell (E-13): a sidebar grouped by role, a top bar with the current round and the user.
// One current round applies to every page that works on a round.

type Health = { status: 'ok' | 'unavailable'; version: string; schema_revision: string | null }
type Page = 'planning' | 'status' | 'setup' | 'publish' | 'masterdata' | 'responsibilities' | 'users'

const NAV: Record<Page, { label: string; icon: () => ReactNode }> = {
  planning: { label: 'Planning', icon: PlanningIcon },
  status: { label: 'Status', icon: StatusIcon },
  setup: { label: 'Setup', icon: SetupIcon },
  publish: { label: 'Publish', icon: PublishIcon },
  masterdata: { label: 'Master data', icon: MasterDataIcon },
  responsibilities: { label: 'Responsibilities', icon: ResponsibilitiesIcon },
  users: { label: 'Users', icon: UsersIcon },
}
const ROUND_PAGES: Page[] = ['planning', 'status', 'setup', 'publish']

function groupsFor(me: Me): { label: string; pages: Page[] }[] {
  const round: Page[] = me.is_controller ? ['setup', 'publish', 'responsibilities'] : []
  const admin: Page[] = []
  if (me.is_controller || me.is_admin) admin.push('masterdata')
  if (me.is_admin) admin.push('users')
  const groups: { label: string; pages: Page[] }[] = [
    { label: 'Plan', pages: ['planning', 'status'] },
    { label: 'Round', pages: round },
    { label: 'Admin', pages: admin },
  ]
  return groups.filter((g) => g.pages.length > 0)
}

// Per-browser conveniences; storage may be unavailable (private mode), which only loses the memory.
function remembered(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}
function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    // not remembered; nothing else depends on it
  }
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [me, setMe] = useState<Me | null | undefined>(undefined) // undefined: not known yet
  const [versions, setVersions] = useState<Version[]>([])
  const [version, setVersion] = useState('')
  const [page, setPage] = useState<Page>(() => (window.location.hash.slice(1) as Page) || 'planning')
  // Collapsed by default on narrow screens, so the grid and the setup preview keep their width.
  const [collapsed, setCollapsed] = useState(() => {
    const stored = remembered('planlayer.sidebar')
    return stored ? stored === 'collapsed' : window.innerWidth < 1360
  })
  const [error, setError] = useState<string | null>(null)

  const checkHealth = useCallback(() => {
    fetch('/api/health')
      .then((r) => r.json() as Promise<Health>)
      .then(setHealth, () => setHealth({ status: 'unavailable', version: '', schema_revision: null }))
  }, [])
  useEffect(checkHealth, [checkHealth])

  useEffect(() => {
    if (health?.status !== 'ok') return
    api.me().then(setMe, () => setMe(null))
    const ended = () => setMe(null)
    window.addEventListener('planlayer:unauthorized', ended)
    return () => window.removeEventListener('planlayer:unauthorized', ended)
  }, [health])

  const loadVersions = useCallback((select?: string) => {
    api.versions().then(
      (vs) => {
        setVersions(vs)
        setVersion((current) => {
          const wanted = select ?? (current || remembered('planlayer.round'))
          return vs.some((v) => v.code === wanted) ? wanted! : (vs[0]?.code ?? '')
        })
      },
      (e: unknown) => setError(e instanceof Error ? e.message : String(e)),
    )
  }, [])

  useEffect(() => {
    if (me) loadVersions()
  }, [me, loadVersions])

  useEffect(() => {
    if (version) remember('planlayer.round', version)
  }, [version])

  function go(next: Page) {
    setPage(next)
    window.location.hash = next
  }

  function toggleSidebar() {
    setCollapsed((c) => {
      remember('planlayer.sidebar', c ? 'expanded' : 'collapsed')
      return !c
    })
  }

  async function logout() {
    await api.logout().catch(() => undefined)
    setMe(null)
  }

  if (health?.status === 'unavailable') {
    return (
      <div className="center">
        <div className="login-card">
          <div className="sb-brand">
            <LogoMark />
            <span>Planlayer</span>
          </div>
          <h1>The server isn’t reachable</h1>
          <p>The app couldn’t reach its server or the server couldn’t reach its database. Try again in a moment.</p>
          <button type="button" className="su-btn primary" onClick={checkHealth}>
            Try again
          </button>
        </div>
      </div>
    )
  }
  if (me === null) return <LoginPage onLogin={setMe} />
  if (!me || !health) return null

  const groups = groupsFor(me)
  const pages = groups.flatMap((g) => g.pages)
  const current = pages.includes(page) ? page : 'planning'
  const round = versions.find((v) => v.code === version)
  const roles = [me.is_admin && 'Admin', me.is_controller && 'Controller', me.is_viewer && 'Viewer'].filter(Boolean)
  const roleText = roles.join(', ')
  const contact = me.email ?? me.login
  const subtitle = roleText && roleText !== me.display_name ? roleText : contact !== me.display_name ? contact : ''
  const needsRound = ROUND_PAGES.includes(current) && current !== 'setup'

  return (
    <div className={`sh${collapsed ? ' collapsed' : ''}`}>
      <aside className="sb" aria-label="Navigation">
        <div className="sb-brand">
          <LogoMark />
          <span>Planlayer</span>
        </div>
        <nav>
          {groups.map((g) => (
            <div key={g.label} className="sb-group">
              <div className="sb-label">{g.label}</div>
              {g.pages.map((p) => {
                const { label, icon: PageIcon } = NAV[p]
                return (
                  <button
                    type="button"
                    key={p}
                    className="sb-item"
                    aria-current={current === p ? 'page' : undefined}
                    title={collapsed ? label : undefined}
                    onClick={() => go(p)}
                  >
                    <PageIcon />
                    <span>{label}</span>
                  </button>
                )
              })}
            </div>
          ))}
        </nav>
        <div className="sb-foot">
          <span className="sb-health" title={`Planlayer ${health.version}, schema ${health.schema_revision ?? '–'}`}>
            <i />
            <span>
              <span>v{health.version}</span>
              <span>schema {health.schema_revision ?? '–'}</span>
            </span>
          </span>
          <button
            type="button"
            className="su-icon"
            onClick={toggleSidebar}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <CollapseIcon collapsed={collapsed} />
          </button>
        </div>
      </aside>

      <div className="sh-main">
        <header className="tb">
          {ROUND_PAGES.includes(current) && versions.length > 0 && (
            <div className="tb-round">
              <span>Round</span>
              <Select
                variant="pill"
                label="Round"
                value={version}
                onChange={setVersion}
                options={versions.map((v) => ({
                  value: v.code,
                  label: v.name,
                  hint: `${v.status} · FY ${v.fiscal_year}`,
                  dot: v.status,
                }))}
              />
            </div>
          )}
          <div className="tb-user">
            <span className="tb-avatar" aria-hidden="true">
              {initials(me.display_name)}
            </span>
            <span className="tb-who">
              <strong>{me.display_name}</strong>
              {subtitle && <span>{subtitle}</span>}
            </span>
            <button type="button" className="su-icon" onClick={() => void logout()} aria-label="Log out" title="Log out">
              <LogoutIcon />
            </button>
          </div>
        </header>

        <main className="page">
          {error && <div className="su-note error">{error}</div>}
          {needsRound && versions.length === 0 && (
            <div className="su-card empty-state">
              <h3>No planning round yet</h3>
              <p className="su-hint">
                {me.is_controller
                  ? 'Create the first round under Setup: it decides what is planned and in what form.'
                  : 'The controller creates the round; it appears here once it exists.'}
              </p>
              {me.is_controller && (
                <button type="button" className="su-btn primary" onClick={() => go('setup')}>
                  Go to Setup
                </button>
              )}
            </div>
          )}
          {version && current === 'planning' && <PlanningPage key={`${me.login}/${version}`} version={version} />}
          {version && current === 'status' && (
            <StatusPage key={`${me.login}/${version}`} version={version} me={me} onRoundChanged={loadVersions} />
          )}
          {current === 'setup' && <SetupPage rounds={versions} code={version} onRoundsChanged={loadVersions} />}
          {round && current === 'publish' && <PublishPage key={round.code} round={round} />}
          {current === 'masterdata' && <MasterDataPage me={me} />}
          {current === 'responsibilities' && <ResponsibilitiesPage />}
          {current === 'users' && <UsersPage />}
        </main>
      </div>
    </div>
  )
}
