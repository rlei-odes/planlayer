import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { api, type Me, type MyThread, type Version } from './api'
import HomePage, { type HomeTarget, type Sheet } from './HomePage'
import CapacityPage from './CapacityPage'
import ForwardingPage from './ForwardingPage'
import {
  CapacityIcon,
  ForwardingIcon,
  HomeIcon,
  CollapseIcon,
  ItemListIcon,
  LogoMark,
  LogoutIcon,
  MasterDataIcon,
  PlanningIcon,
  PublishIcon,
  ResponsibilitiesIcon,
  SetupIcon,
  SettingsIcon,
  StatusIcon,
  UsersIcon,
  WorkforceIcon,
} from './icons'
import ItemListsPage from './ItemListsPage'
import LoginPage from './LoginPage'
import { initials } from './names'
import Select from './Select'
import MasterDataPage from './MasterDataPage'
import PlanningPage from './PlanningPage'
import PublishPage from './PublishPage'
import ResponsibilitiesPage from './ResponsibilitiesPage'
import SetupPage from './SetupPage'
import StatusPage from './StatusPage'
import SettingsPage from './SettingsPage'
import UsersPage from './UsersPage'
import WorkforcePage from './WorkforcePage'

// The app shell (E-13): a sidebar grouped by role, a top bar with the current round and the user.
// One current round applies to every page that works on a round.

type Health = { status: 'ok' | 'unavailable'; version: string; schema_revision: string | null }
type Page =
  | 'home'
  | 'capacity'
  | 'workforce'
  | 'planning'
  | 'status'
  | 'lists'
  | 'setup'
  | 'forwarding'
  | 'publish'
  | 'masterdata'
  | 'responsibilities'
  | 'users'
  | 'settings'

const NAV: Record<Page, { label: string; icon: () => ReactNode }> = {
  home: { label: 'Home', icon: HomeIcon },
  capacity: { label: 'Capacity', icon: CapacityIcon },
  workforce: { label: 'Workforce', icon: WorkforceIcon },
  planning: { label: 'Planning', icon: PlanningIcon },
  status: { label: 'Status', icon: StatusIcon },
  lists: { label: 'My item lists', icon: ItemListIcon },
  setup: { label: 'Setup', icon: SetupIcon },
  forwarding: { label: 'Forwarding', icon: ForwardingIcon },
  publish: { label: 'Publish', icon: PublishIcon },
  masterdata: { label: 'Master data', icon: MasterDataIcon },
  responsibilities: { label: 'Responsibilities', icon: ResponsibilitiesIcon },
  users: { label: 'Users', icon: UsersIcon },
  settings: { label: 'Settings', icon: SettingsIcon },
}
const ROUND_PAGES: Page[] = ['capacity', 'workforce', 'planning', 'status', 'setup', 'forwarding', 'publish']

function groupsFor(me: Me): { label: string; pages: Page[] }[] {
  const round: Page[] = me.is_controller ? ['setup', 'forwarding', 'publish', 'responsibilities'] : []
  const admin: Page[] = []
  if (me.is_controller || me.is_admin) admin.push('masterdata')
  if (me.is_admin) admin.push('users', 'settings')
  // Planning and status need cost centers to read: a role, ownership, a deputy grant, a capacity
  // planner grant or an approver assignment. The admin role alone gives none (ADR 0009). Capacity
  // and workforce come first, the usual order of work (ADR 0013, ADR 0020).
  const plan: Page[] =
    me.is_controller || me.is_viewer || me.reads_cost_centers ? ['capacity', 'workforce', 'planning', 'status', 'lists'] : []
  // Home comes first for everyone: what waits for the user and what they plan (ADR 0021).
  const groups: { label: string; pages: Page[] }[] = [
    { label: '', pages: ['home'] },
    { label: 'Plan', pages: plan },
    { label: 'Round', pages: round },
    { label: 'Admin', pages: admin },
  ]
  return groups.filter((g) => g.pages.length > 0)
}

function pageOfHash(): Page {
  const hash = window.location.hash.slice(1)
  return hash && hash !== 'notes' ? (hash as Page) : 'home' // #notes: the page Home replaced
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
  const [page, setPage] = useState<Page>(pageOfHash)
  // The browser's back and forward buttons move between pages through the hash.
  useEffect(() => {
    const follow = () => setPage(pageOfHash())
    window.addEventListener('hashchange', follow)
    return () => window.removeEventListener('hashchange', follow)
  }, [])
  // A planning sheet wider than the usual content width (more comparison columns) widens the
  // content area, top bar included, so the grid never needs a horizontal scrollbar to fit.
  const [sheetWidth, setSheetWidth] = useState<number | null>(null)
  // Collapsed by default on narrow screens, so the grid and the setup preview keep their width.
  const [collapsed, setCollapsed] = useState(() => {
    const stored = remembered('planlayer.sidebar')
    return stored ? stored === 'collapsed' : window.innerWidth < 1360
  })
  const [error, setError] = useState<string | null>(null)
  // The cost center shown on the capacity and planning pages: switching pages keeps it.
  const [costCenter, setCostCenter] = useState(() => remembered('planlayer.costCenter') ?? '')
  // A line whose notes a sheet opens once loaded (from Home), and the open tasks for the badge.
  const [focus, setFocus] = useState<{ account?: string; key?: string; thread: number } | null>(null)
  const [tasks, setTasks] = useState(0)

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

  useEffect(() => {
    if (costCenter) remember('planlayer.costCenter', costCenter)
  }, [costCenter])

  function go(next: Page) {
    setPage(next)
    window.location.hash = next
  }

  // The badge counts open tasks for the user (note tasks, transfers to accept); refreshed when
  // they move between pages, and by Home itself after a decision.
  useEffect(() => {
    if (me) api.home().then((h) => setTasks(h.tasks), () => setTasks(0))
  }, [me, page])

  /** From Home: a cost center's sheet in a round. */
  function openSheet(round: string, cc: string, sheet: Sheet) {
    setVersion(round)
    setCostCenter(cc)
    go(sheet)
  }

  function openRoundPage(round: string, target: HomeTarget) {
    setVersion(round)
    go(target)
  }

  /** Open a thread from Home: its round, its sheet, its cost center, then its line's notes. */
  function openThread(t: MyThread) {
    setVersion(t.round_code)
    setCostCenter(t.cost_center_code)
    setFocus(
      t.module === 'capacity'
        ? { key: t.line_code.replace(' / ', '|'), thread: t.thread_id }
        : { account: t.line_code, thread: t.thread_id },
    )
    go(t.module === 'capacity' ? 'capacity' : 'planning')
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
  const current: Page | undefined = pages.includes(page) ? page : pages[0]
  const round = versions.find((v) => v.code === version)
  const roles = [me.is_admin && 'Admin', me.is_controller && 'Controller', me.is_viewer && 'Viewer'].filter(Boolean)
  const roleText = roles.join(', ')
  const contact = me.email ?? me.login
  const subtitle = roleText && roleText !== me.display_name ? roleText : contact !== me.display_name ? contact : ''
  const needsRound = !!current && ROUND_PAGES.includes(current) && current !== 'setup'

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
              {g.label && <div className="sb-label">{g.label}</div>}
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
                    {p === 'home' && tasks > 0 && (
                      <b className="sb-badge" title={`${tasks} open task${tasks === 1 ? '' : 's'} for you`}>
                        {tasks}
                      </b>
                    )}
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

      <div
        className="sh-main"
        style={sheetWidth ? ({ '--content-max': `max(var(--content-base), ${sheetWidth + 48}px)` } as CSSProperties) : undefined}
      >
        <header className="tb">
          <div className="tb-in">
            {!!current && ROUND_PAGES.includes(current) && versions.length > 0 && (
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
          </div>
        </header>

        <main className="page">
          {error && <div className="su-note error">{error}</div>}
          {!current && (
            <div className="su-card empty-state">
              <h3>Nothing to show yet</h3>
              <p className="su-hint">
                You don’t plan or approve any cost center, and you have no role. Planners get their cost centers from
                ownership or as deputies, set by the controller; the Viewer role reads every cost center. Ask your
                controller or admin.
              </p>
            </div>
          )}
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
          {version && current === 'capacity' && (
            <CapacityPage
              key={`${me.login}/${version}`}
              version={version}
              unit={costCenter}
              onUnit={setCostCenter}
              controller={me.is_controller}
              onWidth={setSheetWidth}
              focus={focus?.key ? { key: focus.key, thread: focus.thread } : null}
              onFocused={() => setFocus(null)}
            />
          )}
          {version && current === 'workforce' && (
            <WorkforcePage
              key={`${me.login}/${version}`}
              version={version}
              unit={costCenter}
              onUnit={setCostCenter}
              onWidth={setSheetWidth}
            />
          )}
          {version && current === 'planning' && (
            <PlanningPage
              key={`${me.login}/${version}`}
              version={version}
              unit={costCenter}
              onUnit={setCostCenter}
              onWidth={setSheetWidth}
              focus={focus?.account ? { account: focus.account, thread: focus.thread } : null}
              onFocused={() => setFocus(null)}
            />
          )}
          {version && current === 'status' && (
            <StatusPage key={`${me.login}/${version}`} version={version} me={me} onRoundChanged={loadVersions} />
          )}
          {current === 'home' && (
            <HomePage me={me} onOpenThread={openThread} onOpenSheet={openSheet} onGo={openRoundPage} onChanged={setTasks} />
          )}
          {current === 'lists' && <ItemListsPage />}
          {current === 'setup' && <SetupPage rounds={versions} code={version} onRoundsChanged={loadVersions} />}
          {version && current === 'forwarding' && <ForwardingPage key={version} version={version} />}
          {round && current === 'publish' && <PublishPage key={round.code} round={round} />}
          {current === 'masterdata' && <MasterDataPage me={me} />}
          {current === 'responsibilities' && <ResponsibilitiesPage />}
          {current === 'users' && <UsersPage />}
          {current === 'settings' && <SettingsPage />}
        </main>
      </div>
    </div>
  )
}
