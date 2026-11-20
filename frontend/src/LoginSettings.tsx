import { useEffect, useState, type ReactNode } from 'react'
import { api, type LoginPage, type LoginSettings, type LoginTest } from './api'
import { LIMIT } from './limits'

// The login provider (ADR 0030), for the admin: OpenID Connect or LDAP, tested with the unsaved
// settings before they are saved. An OpenID Connect test leaves the page for the provider; the
// draft waits in sessionStorage and the callback brings the browser back with `?login_test=`.

type Method = LoginSettings['method']
type Note = { kind: 'ok' | 'error'; text: string }

const METHODS: { value: Method; label: string }[] = [
  { value: 'none', label: 'Local accounts only' },
  { value: 'oidc', label: 'OpenID Connect' },
  { value: 'ldap', label: 'LDAP' },
]
const DRAFT_KEY = 'planlayer.loginDraft'

function readDraft(): LoginSettings | null {
  try {
    const raw = window.sessionStorage.getItem(DRAFT_KEY)
    return raw ? (JSON.parse(raw) as LoginSettings) : null
  } catch {
    return null
  }
}

/** The OpenID Connect test's state the callback came back with. */
const testState = () => new URLSearchParams(window.location.search).get('login_test')

/** Off the address and out of storage, once the test's result is shown. */
function clearReturn() {
  try {
    window.sessionStorage.removeItem(DRAFT_KEY)
  } catch {
    // Nothing kept.
  }
  const params = new URLSearchParams(window.location.search)
  params.delete('login_test')
  const query = params.toString()
  window.history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`)
}

const same = (a: LoginSettings, b: LoginSettings) => JSON.stringify(a) === JSON.stringify(b)

export default function LoginSettingsCard() {
  const [page, setPage] = useState<LoginPage | null>(null)
  const [draft, setDraft] = useState<LoginSettings | null>(null)
  const [test, setTest] = useState<{ settings: LoginSettings; result: LoginTest } | null>(null)
  const [note, setNote] = useState<Note | null>(null)

  useEffect(() => {
    let live = true
    const state = testState()
    const waiting = state ? readDraft() : null
    const fail = (e: unknown) => live && setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    api.loginSettings().then((p) => {
      if (!live) return
      setPage(p)
      setDraft(waiting ?? p.settings)
      if (state && waiting)
        api.oidcTestResult(state).then((result) => {
          if (!live) return
          setTest({ settings: waiting, result })
          clearReturn()
        }, fail)
    }, fail)
    return () => {
      live = false
    }
  }, [])

  if (!page || !draft) return note ? <div className="su-note error">{note.text}</div> : null

  const locked = page.from_env
  const set = (change: Partial<LoginSettings>) => {
    setDraft({ ...draft, ...change })
    setNote(null)
  }
  const tested = test !== null && test.result.ok && same(test.settings, draft)
  const changed = !same(draft, page.settings)
  const canSave = !locked && changed && (draft.method === 'none' || tested)

  async function save() {
    if (!draft) return
    try {
      const p = await api.saveLoginSettings(draft)
      setPage(p)
      setDraft(p.settings)
      setNote({ kind: 'ok', text: 'Saved. The login page offers it from now on.' })
    } catch (e) {
      setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    }
  }

  async function testOidc() {
    if (!draft) return
    try {
      const { url } = await api.testOidc(draft)
      try {
        window.sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft))
      } catch {
        // Without storage the result still comes back; the draft is then the saved settings.
      }
      window.location.assign(url)
    } catch (e) {
      setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    }
  }

  const field = (key: keyof LoginSettings, label: string, max: number, hint?: string, placeholder?: string) => (
    <div className="su-field" key={key}>
      <label htmlFor={`ls-${key}`}>{label}</label>
      <input
        id={`ls-${key}`}
        maxLength={max}
        className="su-input"
        value={draft[key]}
        placeholder={placeholder}
        disabled={locked}
        spellCheck={false}
        aria-describedby={hint ? `ls-${key}-hint` : undefined}
        onChange={(e) => set({ [key]: e.target.value })}
      />
      {hint && (
        <span className="lg-hint" id={`ls-${key}-hint`}>
          {hint}
        </span>
      )}
    </div>
  )

  return (
    <div className="su-card">
      <h3>Login</h3>
      <p className="su-hint">How users log in besides local accounts. Local accounts, such as the first admin's, always work.</p>
      {locked && <div className="su-note fw-gap">Set by PLANLAYER_LOGIN_* environment variables; read-only here. Test still works.</div>}

      <div className="seg fw-gap" role="group" aria-label="Login method">
        {METHODS.map((m) => (
          <button key={m.value} type="button" aria-pressed={draft.method === m.value} disabled={locked} onClick={() => set({ method: m.value })}>
            {m.label}
          </button>
        ))}
      </div>

      {draft.method === 'oidc' && (
        <div className="ls-grid fw-gap">
          {field('oidc_label', 'Button', LIMIT.name, 'Shown as "Sign in with …".', 'Microsoft')}
          {field('oidc_issuer', 'Issuer', LIMIT.longText, 'Entra ID: https://login.microsoftonline.com/<tenant ID>/v2.0', 'https://')}
          {field('oidc_client_id', 'Client ID', LIMIT.longText)}
          {field('oidc_scopes', 'Scopes', LIMIT.name)}
          <div className="su-field ls-wide">
            Redirect URI
            <code className="ls-code">{page.redirect_uri}</code>
            <span className="lg-hint">
              Register it at the provider.{' '}
              {page.client_secret ? 'Confidential client: PLANLAYER_OIDC_CLIENT_SECRET is set.' : 'Public client with PKCE; no secret.'}
            </span>
          </div>
          <Fold title="Claims">
            {field('oidc_login_claim', 'Login', LIMIT.name)}
            {field('oidc_email_claim', 'E-mail', LIMIT.name)}
            {field('oidc_name_claim', 'Name', LIMIT.name)}
            {field('oidc_groups_claim', 'Groups', LIMIT.name, 'Entra ID sends group object IDs.')}
          </Fold>
        </div>
      )}

      {draft.method === 'ldap' && (
        <div className="ls-grid fw-gap">
          {field('ldap_url', 'Server', LIMIT.longText, undefined, 'ldaps://dc01.corp.example:636')}
          {field('ldap_bind_dn', 'Bind DN', LIMIT.longText, 'With {login}. Active Directory: {login}@corp.example', 'uid={login},ou=people,dc=example,dc=com')}
          {field('ldap_base_dn', 'Base DN', LIMIT.longText, undefined, 'dc=example,dc=com')}
          <Fold title="Attributes">
            {field('ldap_login_attribute', 'Login', LIMIT.name, 'Active Directory: sAMAccountName')}
            {field('ldap_email_attribute', 'E-mail', LIMIT.name)}
            {field('ldap_name_attribute', 'Name', LIMIT.name, 'Active Directory: displayName')}
          </Fold>
        </div>
      )}

      {draft.method !== 'none' && (
        <>
          <div className="ls-grid fw-gap">
            <Fold title="CA certificate">
              <div className="su-field ls-wide">
                <label htmlFor="ls-ca_pem">PEM</label>
                <textarea
                  id="ls-ca_pem"
                  maxLength={LIMIT.pem}
                  className="su-input ls-pem"
                  value={draft.ca_pem}
                  disabled={locked}
                  spellCheck={false}
                  placeholder="-----BEGIN CERTIFICATE-----"
                  aria-describedby="ls-ca_pem-hint"
                  onChange={(e) => set({ ca_pem: e.target.value })}
                />
                <span className="lg-hint" id="ls-ca_pem-hint">
                  Only for a provider signed by the company's own CA.
                </span>
              </div>
            </Fold>
          </div>
          <h4 className="ls-sub">Groups</h4>
          <p className="su-hint">A role group sets that role at every login through the provider. Empty: the role is set on the Users page.</p>
          <div className="ls-grid">
            {field('allowed_group', 'Allowed', LIMIT.longText, 'Only its members log in. Empty: everyone the provider knows.')}
            {field('admin_group', 'Admin', LIMIT.longText)}
            {field('controller_group', 'Controller', LIMIT.longText)}
            {field('viewer_group', 'Viewer', LIMIT.longText)}
            {field('hr_group', 'HR', LIMIT.longText)}
          </div>
          {draft.method === 'ldap' ? (
            <LdapTest settings={draft} onResult={(result) => setTest({ settings: draft, result })} />
          ) : (
            <div className="fw-buttons ls-left">
              <button type="button" className="su-btn" onClick={() => void testOidc()}>
                Test
              </button>
              <span className="lg-hint">Signs in at the provider with these settings. Nothing is saved, no session made.</span>
            </div>
          )}
          {test && same(test.settings, draft) && <TestResult result={test.result} />}
        </>
      )}

      {note && <div className={`su-note ${note.kind} fw-gap`}>{note.text}</div>}
      {!locked && (
        <div className="fw-buttons">
          {changed && draft.method !== 'none' && !tested && <span className="lg-hint">Test before saving.</span>}
          <button type="button" className="su-btn primary" disabled={!canSave} onClick={() => void save()}>
            Save
          </button>
        </div>
      )}
    </div>
  )
}

function Fold({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="ls-wide ls-fold">
      <summary className="ink-2">{title}</summary>
      <div className="ls-grid">{children}</div>
    </details>
  )
}

function LdapTest({ settings, onResult }: { settings: LoginSettings; onResult: (r: LoginTest) => void }) {
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  async function run() {
    setError(null)
    try {
      onResult(await api.testLdap(settings, login, password))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  return (
    <form
      className="su-row fw-gap ls-test"
      onSubmit={(e) => {
        e.preventDefault()
        void run()
      }}
    >
      <label className="su-field">
        Login
        <input maxLength={LIMIT.code} className="su-input" autoComplete="off" value={login} onChange={(e) => setLogin(e.target.value)} required />
      </label>
      <label className="su-field">
        Password
        <input
          maxLength={LIMIT.password}
          className="su-input"
          type="password"
          autoComplete="off"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </label>
      <button type="submit" className="su-btn">
        Test
      </button>
      {error && <span className="lg-hint error">{error}</span>}
    </form>
  )
}

function TestResult({ result }: { result: LoginTest }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="fw-gap">
      <ul className="ls-steps">
        {result.steps.map((s) => (
          <li key={s.label} className={s.ok === false ? 'fail' : s.ok ? 'ok' : 'info'}>
            <span className="ls-mark" aria-hidden="true">
              {s.ok === false ? '✕' : s.ok ? '✓' : '·'}
            </span>
            <strong>{s.label}</strong>
            <span>{s.detail}</span>
          </li>
        ))}
      </ul>
      {result.env && (
        <details className="ls-fold fw-gap">
          <summary className="ink-2">As environment variables</summary>
          <p className="su-hint">For the deployment's environment or env file; set there, they replace this page's settings.</p>
          <pre className="ls-env">{result.env}</pre>
          <button
            type="button"
            className="su-btn"
            onClick={() =>
              void navigator.clipboard.writeText(result.env ?? '').then(
                () => setCopied(true),
                () => setCopied(false),
              )
            }
          >
            {copied ? 'Copied' : 'Copy'}
          </button>
        </details>
      )}
    </div>
  )
}
