import { useCallback, useEffect, useState } from 'react'
import { api, type User } from './api'
import { initials } from './names'
import PageHeader from './PageHeader'

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

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([])
  const [note, setNote] = useState<Note | null>(null)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState(BLANK)

  const load = useCallback(() => {
    api.users().then(setUsers, (e: unknown) => setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) }))
  }, [])

  useEffect(load, [load])

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
        sub={`${active} active of ${users.length}. Planners need no role: owning or deputising a cost center lets them plan it.`}
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
              <input className="su-input" value={draft.login} onChange={(e) => setDraft({ ...draft, login: e.target.value })} required />
            </label>
            <label className="su-field md-grow">
              Name
              <input
                className="su-input"
                value={draft.display_name}
                onChange={(e) => setDraft({ ...draft, display_name: e.target.value })}
                required
              />
            </label>
            <label className="su-field md-grow">
              E-mail
              <input className="su-input" type="email" value={draft.email} onChange={(e) => setDraft({ ...draft, email: e.target.value })} />
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

      <div className="tbl-card">
        <div className="tbl-scroll">
          <table className="tbl">
            <thead>
              <tr>
                <th>User</th>
                <th>E-mail</th>
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
