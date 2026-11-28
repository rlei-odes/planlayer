import { useEffect, useState, type FormEvent } from 'react'
import { api, type Me } from './api'
import { LogoMark } from './icons'
import { FIELD, LIMIT } from './limits'
import { initials } from './names'
import type { DemoUser, LoginOptions } from './schema'

// Login (ADR 0030): a demo's user list; the first run's admin account, with the setup code from
// the server log; otherwise the OpenID Connect provider's button, with the password form (local
// accounts, and directory accounts when LDAP is set up) behind "Use Planlayer account".

/** The error the OpenID Connect callback sent back with, taken off the address. */
function takeLoginError(): string | null {
  const params = new URLSearchParams(window.location.search)
  const error = params.get('login_error')
  if (error === null) return null
  params.delete('login_error')
  const query = params.toString()
  window.history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`)
  return error
}

export default function LoginPage({ onLogin }: { onLogin: (me: Me) => void }) {
  const [options, setOptions] = useState<LoginOptions | null>(null)
  const [error, setError] = useState<string | null>(takeLoginError)
  // With OpenID Connect, the password form opens on request: most users sign in at the provider.
  const [local, setLocal] = useState(false)

  useEffect(() => {
    api.loginOptions().then(setOptions, (e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  async function attempt(login: () => Promise<Me>) {
    setError(null)
    try {
      onLogin(await login())
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
        {options?.setup ? <h1>Create the admin account</h1> : <h1>Log in</h1>}
        {error && <div className="su-note error lg-gap">{error}</div>}
        {options?.demo && <DemoUsers onPick={(login) => void attempt(() => api.demoLogin(login))} />}
        {options?.setup && <SetupForm onSubmit={(body) => void attempt(() => api.setup(body))} />}
        {options && !options.demo && !options.setup && (
          <>
            {options.oidc && (
              <a className="su-btn primary lg-wide" href="/api/auth/oidc/login">
                Sign in with {options.oidc}
              </a>
            )}
            {options.oidc && !local ? (
              <button type="button" className="su-btn ghost lg-wide lg-local" onClick={() => setLocal(true)}>
                Use Planlayer account
              </button>
            ) : (
              <>
                {options.oidc && <div className="lg-or">Planlayer account</div>}
                <PasswordForm
                  primary={!options.oidc}
                  autoFocus={!!options.oidc}
                  onSubmit={(login, password) => void attempt(() => api.passwordLogin(login, password))}
                />
              </>
            )}
          </>
        )}
      </section>
    </div>
  )
}

function DemoUsers({ onPick }: { onPick: (login: string) => void }) {
  const [users, setUsers] = useState<DemoUser[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.demoUsers().then(setUsers, (e: unknown) => setError(String(e)))
  }, [])
  return (
    <>
      <p>Choose a user to continue.</p>
      {error && <div className="su-note error">{error}</div>}
      {users?.length === 0 && (
        <div className="su-note">
          No users yet. Fill the demo with <code>planlayer seed-demo</code>.
        </div>
      )}
      <ul className="login-users">
        {users?.map((u) => (
          <li key={u.login}>
            <button type="button" onClick={() => onPick(u.login)}>
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
      <div className="login-foot">Demo installation: anyone can log in as any user, without a password.</div>
    </>
  )
}

function PasswordForm({
  primary,
  autoFocus,
  onSubmit,
}: {
  primary: boolean
  autoFocus: boolean
  onSubmit: (login: string, password: string) => void
}) {
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  function submit(e: FormEvent) {
    e.preventDefault()
    onSubmit(login, password)
  }
  return (
    <form className="lg-form" onSubmit={submit}>
      <label className="su-field">
        Login
        <input
          maxLength={LIMIT.code}
          className="su-input"
          autoComplete="username"
          autoFocus={autoFocus}
          value={login}
          onChange={(e) => setLogin(e.target.value)}
          required
        />
      </label>
      <label className="su-field">
        Password
        <input
          maxLength={LIMIT.password}
          className="su-input"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </label>
      <button type="submit" className={`su-btn ${primary ? 'primary' : ''} lg-wide`}>
        Log in
      </button>
    </form>
  )
}

const MIN_PASSWORD = 12

function SetupForm({ onSubmit }: { onSubmit: (body: { code: string; login: string; display_name: string; email: string | null; password: string }) => void }) {
  const [code, setCode] = useState('')
  const [login, setLogin] = useState('')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const mismatch = repeat !== '' && repeat !== password
  function submit(e: FormEvent) {
    e.preventDefault()
    if (password !== repeat) return
    onSubmit({ code, login, display_name: name, email: email || null, password })
  }
  return (
    <form className="lg-form" onSubmit={submit}>
      <p className="lg-hint">
        The setup code is in the server log (<code>docker compose logs app</code>). This local account stays the way in if the login provider fails.
      </p>
      <label className="su-field">
        Setup code
        <input
          maxLength={LIMIT.code}
          className="su-input lg-code"
          autoComplete="off"
          spellCheck={false}
          placeholder="XXXX-XXXX-XXXX-XXXX"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          required
        />
      </label>
      <label className="su-field">
        Login
        <input maxLength={LIMIT.code} className="su-input" autoComplete="username" value={login} onChange={(e) => setLogin(e.target.value)} required />
      </label>
      <label className="su-field">
        Name
        <input maxLength={FIELD.person} className="su-input" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} required />
      </label>
      <label className="su-field">
        E-mail
        <input maxLength={LIMIT.email} className="su-input" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>
      <div className="su-field">
        <label htmlFor="lg-password">Password</label>
        <input
          id="lg-password"
          maxLength={LIMIT.password}
          minLength={MIN_PASSWORD}
          className="su-input"
          type="password"
          autoComplete="new-password"
          aria-describedby="lg-password-hint"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <span className="lg-hint" id="lg-password-hint">
          At least {MIN_PASSWORD} characters.
        </span>
      </div>
      <div className="su-field">
        <label htmlFor="lg-repeat">Repeat password</label>
        <input
          id="lg-repeat"
          maxLength={LIMIT.password}
          className="su-input"
          type="password"
          autoComplete="new-password"
          aria-invalid={mismatch}
          aria-describedby={mismatch ? 'lg-repeat-hint' : undefined}
          value={repeat}
          onChange={(e) => setRepeat(e.target.value)}
          required
        />
        {mismatch && (
          <span className="lg-hint error" id="lg-repeat-hint">
            The passwords differ.
          </span>
        )}
      </div>
      <button type="submit" className="su-btn primary lg-wide" disabled={mismatch}>
        Create admin
      </button>
    </form>
  )
}
