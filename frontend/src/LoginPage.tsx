import { useEffect, useState } from 'react'
import { api, type Me } from './api'
import { LogoMark } from './icons'
import { initials } from './names'
import type { DevUser } from './schema'

// Login (T-22). Until OIDC and LDAPS exist, the development login picks a user without a password.

export default function LoginPage({ onLogin }: { onLogin: (me: Me) => void }) {
  const [devLogin, setDevLogin] = useState<boolean | null>(null)
  const [users, setUsers] = useState<DevUser[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.loginOptions().then(
      (o) => {
        setDevLogin(o.dev_login)
        if (o.dev_login) api.devUsers().then(setUsers, (e: unknown) => setError(String(e)))
      },
      (e: unknown) => setError(String(e)),
    )
  }, [])

  async function login(name: string) {
    try {
      onLogin(await api.devLogin(name))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="center">
      <section className="login-card" aria-label="Log in">
        <div className="sb-brand">
          <LogoMark />
          <span>Planlayer</span>
        </div>
        <h1>Log in</h1>
        {error && <div className="su-note error">{error}</div>}
        {devLogin === false && <p>No login method is configured yet.</p>}
        {devLogin && (
          <>
            <p>Choose a user to continue.</p>
            {users.length === 0 && (
              <div className="su-note">
                No users yet. Create them with <code>planlayer seed-dev-users</code>.
              </div>
            )}
            <ul className="login-users">
              {users.map((u) => (
                <li key={u.login}>
                  <button type="button" onClick={() => void login(u.login)}>
                    <span className="tb-avatar" aria-hidden="true">
                      {initials(u.display_name)}
                    </span>
                    <span className="tb-who">
                      <strong>{u.display_name}</strong>
                      <span>{u.login}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <div className="login-foot">Development login, without a password. Refused in production.</div>
          </>
        )}
      </section>
    </div>
  )
}
