import { useCallback, useEffect, useState } from 'react'
import { api, type User } from './api'

// Users (admin): roles and accounts (ADR 0009).

type Flag = 'is_admin' | 'is_controller' | 'is_viewer' | 'disabled'
const FLAGS: [Flag, string][] = [
  ['is_admin', 'Admin'],
  ['is_controller', 'Controller'],
  ['is_viewer', 'Viewer'],
  ['disabled', 'Disabled'],
]

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([])
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState({ login: '', display_name: '', email: '' })

  const load = useCallback(() => {
    api.users().then(setUsers, (e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  useEffect(load, [load])

  async function run(task: () => Promise<unknown>) {
    try {
      await task()
      setError(null)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <section className="users">
      {error && <div className="error">{error}</div>}
      <form
        className="toolbar"
        onSubmit={(e) => {
          e.preventDefault()
          void run(async () => {
            await api.createUser({
              ...draft,
              email: draft.email || null,
              is_admin: false,
              is_controller: false,
              is_viewer: false,
            })
            setDraft({ login: '', display_name: '', email: '' })
          })
        }}
      >
        <input placeholder="Login" value={draft.login} onChange={(e) => setDraft({ ...draft, login: e.target.value })} required />
        <input
          placeholder="Name"
          value={draft.display_name}
          onChange={(e) => setDraft({ ...draft, display_name: e.target.value })}
          required
        />
        <input placeholder="E-mail" value={draft.email} onChange={(e) => setDraft({ ...draft, email: e.target.value })} />
        <button type="submit">Add user</button>
      </form>
      <table className="overview">
        <thead>
          <tr>
            <th>Login</th>
            <th>Name</th>
            <th>E-mail</th>
            {FLAGS.map(([, label]) => (
              <th key={label}>{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.login} className={u.disabled ? 'muted' : undefined}>
              <td>{u.login}</td>
              <td>{u.display_name}</td>
              <td>{u.email}</td>
              {FLAGS.map(([flag, label]) => (
                <td key={flag}>
                  <input
                    type="checkbox"
                    aria-label={`${label} ${u.login}`}
                    checked={u[flag]}
                    onChange={(e) => void run(() => api.changeUser(u.login, { [flag]: e.target.checked }))}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
