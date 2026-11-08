import { useEffect, useState } from 'react'
import { api, type Me } from './api'

// Login (T-22). Until OIDC and LDAPS exist, the development login picks a user without a password.

export default function LoginPage({ onLogin }: { onLogin: (me: Me) => void }) {
  const [devLogin, setDevLogin] = useState<boolean | null>(null)
  const [users, setUsers] = useState<{ login: string; display_name: string }[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.loginOptions().then((o) => {
      setDevLogin(o.dev_login)
      if (o.dev_login) api.devUsers().then(setUsers, (e: unknown) => setError(String(e)))
    }, (e: unknown) => setError(String(e)))
  }, [])

  async function login(name: string) {
    try {
      onLogin(await api.devLogin(name))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <section className="login">
      <h2>Log in</h2>
      {error && <div className="error">{error}</div>}
      {devLogin === false && <p>No login method is configured yet.</p>}
      {devLogin && (
        <>
          <p className="muted">Development login: choose a user. Refused in production.</p>
          {users.length === 0 && <p>No users yet. Create them with `planlayer seed-dev-users`.</p>}
          <ul className="users">
            {users.map((u) => (
              <li key={u.login}>
                <button type="button" onClick={() => void login(u.login)}>
                  {u.display_name}
                </button>{' '}
                <span className="muted">{u.login}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
