import { useCallback, useEffect, useState } from 'react'
import { api, type User } from './api'
import { initials } from './names'
import PageHeader from './PageHeader'
import { FIELD, LIMIT } from './limits'

// Users (admin): roles and accounts (ADR 0009). Roles combine; planners need no role, their rights
// come from cost center ownership and deputies.

type Flag = 'is_admin' | 'is_controller' | 'is_viewer' | 'is_hr'
type Note = { kind: 'ok' | 'error'; text: string }
const ROLES: [Flag, string, string][] = [
  ['is_admin', 'Admin', 'users and connections'],
  ['is_controller', 'Controller', 'sets up and runs rounds'],
  ['is_viewer', 'Viewer', 'reads every cost center'],
  ['is_hr', 'HR', 'reads and confirms pay'],
]
const BLANK = { login: '', display_name: '', email: '' }

/** Which switches the login provider's groups overwrite, and which stay as set here. */
function groupNote(grouped: string[]): string {
  const rest = ROLES.map(([, label]) => label).filter((l) => !grouped.includes(l))
  const list = (labels: string[]) => labels.join(labels.length === 2 ? ' and ' : ', ')
  const own = rest.length ? ` ${list(rest)} ${rest.length === 1 ? 'stays' : 'stay'} as set here.` : ''
  return `For users who sign in through the login provider, ${list(grouped)} ${grouped.length === 1 ? 'follows' : 'follow'} their groups there: a change here is overwritten at their next sign-in.${own}`
}

/** How the user logs in: a local password, the provider they logged in through, or both. */
function loginKind(u: User, providerLabel: string): string {
  const provider = u.provider === 'oidc' ? providerLabel : u.provider === 'ldap' ? 'LDAP' : ''
  return [u.local ? 'Local' : '', provider].filter(Boolean).join(' · ') || '—'
}

/** What the user plans: every cost center as controller, else owned ones and deputy grants. */
function plans(u: User): string {
  if (u.is_controller) return 'every cost center'
  const parts = [u.owns ? `owner of ${u.owns}` : '', u.deputy ? `deputy on ${u.deputy}` : ''].filter(Boolean)
  return parts.length ? parts.join(' · ') : '—'
}

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([])
  const [note, setNote] = useState<Note | null>(null)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState(BLANK)

  const load = useCallback(() => {
    api.users().then(setUsers, (e: unknown) => setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) }))
  }, [])

  useEffect(load, [load])

  // Roles the login provider's groups set (ADR 0030): taken anew at every login through it.
  const [grouped, setGrouped] = useState<string[]>([])
  const [providerLabel, setProviderLabel] = useState('OpenID Connect')
  useEffect(() => {
    api.loginSettings().then(
      (p) => {
        const s = p.settings
        if (s.oidc_label) setProviderLabel(s.oidc_label)
        if (s.method === 'none') return
        const groups: Record<Flag, string | undefined> = { is_admin: s.admin_group, is_controller: s.controller_group, is_viewer: s.viewer_group, is_hr: s.hr_group }
        setGrouped(ROLES.filter(([flag]) => groups[flag]).map(([, label]) => label))
      },
      (e: unknown) => setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) }),
    )
  }, [])

  async function run(task: () => Promise<unknown>, done: string) {
    try {
      await task()
      setNote({ kind: 'ok', text: done })
      load()
      return true
    } catch (e) {
      setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
      return false
    }
  }

  const active = users.filter((u) => !u.disabled).length
  return (
    <div className="su us">
      <PageHeader
        eyebrow="Users"
        title="People and roles"
        sub={`${active} active of ${users.length}. Planners need no role: they plan the cost centers they own (the source's owner, matched by e-mail) or are deputy on, both set on Responsibilities.`}
        actions={
          <button type="button" className="su-btn primary" onClick={() => setAdding((a) => !a)}>
            Add user
          </button>
        }
      />

      {adding && (
        <form
          className="su-card md-add rs-gap"
          onSubmit={(e) => {
            e.preventDefault()
            void run(
              () => api.createUser({ ...draft, email: draft.email || null, is_admin: false, is_controller: false, is_viewer: false, is_hr: false }),
              `${draft.login} added.`,
            ).then((ok) => ok && setDraft(BLANK))
          }}
        >
          <h3>New user</h3>
          <div className="su-row md-add-row">
            <label className="su-field">
              Login
              <input maxLength={LIMIT.code} className="su-input" value={draft.login} onChange={(e) => setDraft({ ...draft, login: e.target.value })} required />
            </label>
            <label className="su-field md-grow">
              Name
              <input maxLength={FIELD.person}
                className="su-input"
                value={draft.display_name}
                onChange={(e) => setDraft({ ...draft, display_name: e.target.value })}
                required
              />
            </label>
            <label className="su-field md-grow">
              E-mail
              <input maxLength={LIMIT.email} className="su-input" type="email" value={draft.email} onChange={(e) => setDraft({ ...draft, email: e.target.value })} />
            </label>
            <button type="button" className="su-btn ghost" onClick={() => setAdding(false)}>
              Cancel
            </button>
            <button type="submit" className="su-btn primary">
              Add user
            </button>
          </div>
        </form>
      )}
      {note && <div className={`su-note ${note.kind} rs-gap`}>{note.text}</div>}
      {grouped.length > 0 && (
        <div className="su-note rs-gap">
          {groupNote(grouped)}
        </div>
      )}

      <div className="tbl-card">
        <div className="tbl-scroll">
          <table className="tbl">
            <thead>
              <tr>
                <th>User</th>
                <th>E-mail</th>
                <th title="Cost centers the user plans, from Responsibilities">Plans</th>
                <th title="How the user logs in: a local password, or the login provider">Source</th>
                {ROLES.map(([, label]) => (
                  <th key={label} className="us-role-h">
                    {label}
                  </th>
                ))}
                <th />
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.login} className={u.disabled ? 'us-disabled' : undefined}>
                  <td>
                    <span className="us-user">
                      <span className="tb-avatar" aria-hidden="true">
                        {initials(u.display_name)}
                      </span>
                      <span className="tb-who">
                        <strong>{u.display_name}</strong>
                        <span>{u.login}</span>
                      </span>
                    </span>
                  </td>
                  <td className="muted">{u.email}</td>
                  <td className="muted">{plans(u)}</td>
                  <td className="muted" title={u.local || u.provider ? undefined : 'Has no local password and has not logged in through the provider yet'}>
                    {loginKind(u, providerLabel)}
                  </td>
                  {ROLES.map(([flag, label, what]) => (
                    <td key={flag} className="us-role">
                      <label className="us-switch" title={`${label}: ${what}`}>
                        <input
                          type="checkbox"
                          role="switch"
                          aria-label={`${label} ${u.login}`}
                          checked={u[flag]}
                          onChange={(e) =>
                            void run(
                              () => api.changeUser(u.login, { [flag]: e.target.checked }),
                              `${u.login} is ${e.target.checked ? 'now' : 'no longer'} ${label.toLowerCase()}.`,
                            )
                          }
                        />
                        <i />
                      </label>
                    </td>
                  ))}
                  <td className="right">
                    {u.disabled ? (
                      <span className="btn-row">
                        <span className="su-pill overdue">Disabled</span>
                        <button
                          type="button"
                          className="su-btn ghost sm"
                          onClick={() => void run(() => api.changeUser(u.login, { disabled: false }), `${u.login} can log in again.`)}
                        >
                          Enable
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="su-btn ghost sm"
                        title="Ends their sessions; their history stays"
                        onClick={() => void run(() => api.changeUser(u.login, { disabled: true }), `${u.login} is disabled and logged out.`)}
                      >
                        Disable
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
