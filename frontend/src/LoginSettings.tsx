import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api, type LoginPage, type LoginSettings, type LoginTest } from './api'
import { LIMIT } from './limits'

// The login provider (ADR 0030), for the admin: OpenID Connect or LDAP, tested with the unsaved
// settings before they are saved. The draft is kept in sessionStorage until it is saved, so it
// outlasts a switch to another page and an OpenID Connect test, which leaves for the provider
// and comes back with `?login_test=`.

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

function keepDraft(draft: LoginSettings | null) {
  try {
    if (draft) window.sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft))
    else window.sessionStorage.removeItem(DRAFT_KEY)
  } catch {
    // Without storage, an unsaved draft is lost when the page is left.
  }
}

/** The OpenID Connect test's state the callback came back with. */
const testState = () => new URLSearchParams(window.location.search).get('login_test')

/** Off the address, once the test's result is shown. */
function clearReturn() {
  const params = new URLSearchParams(window.location.search)
  params.delete('login_test')
  const query = params.toString()
  window.history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`)
}

const same = (a: LoginSettings, b: LoginSettings) => JSON.stringify(a) === JSON.stringify(b)

// What reaches the provider: a change here is tested before it is saved. The button's label and
// the groups can't keep anyone out (local accounts always log in), so they save without a test.
const CONNECTION: (keyof LoginSettings)[] = [
  'method',
  'oidc_issuer',
  'oidc_client_id',
  'oidc_scopes',
  'oidc_login_claim',
  'oidc_email_claim',
  'oidc_name_claim',
  'oidc_groups_claim',
  'ldap_url',
  'ldap_bind_dn',
  'ldap_base_dn',
  'ldap_login_attribute',
  'ldap_email_attribute',
  'ldap_name_attribute',
  'ca_pem',
]
const sameConnection = (a: LoginSettings, b: LoginSettings) => CONNECTION.every((k) => (a[k] ?? '') === (b[k] ?? ''))

export default function LoginSettingsCard() {
  const [page, setPage] = useState<LoginPage | null>(null)
  const [draft, setDraft] = useState<LoginSettings | null>(null)
  const [test, setTest] = useState<{ settings: LoginSettings; result: LoginTest } | null>(null)
  const [note, setNote] = useState<Note | null>(null)
  const [fromEnv, setFromEnv] = useState(false)
  const [testing, setTesting] = useState(false)
  // The OpenID Connect test running in a popup: its state, and the settings it tries.
  const running = useRef<{ state: string; settings: LoginSettings; popup: Window } | null>(null)

  // The popup's page tells this one when the test is done; closing it early ends the wait too.
  useEffect(() => {
    const finish = (state: string) => {
      const run = running.current
      if (!run || run.state !== state) return
      running.current = null
      setTesting(false)
      api.oidcTestResult(state).then(
        (result) => setTest({ settings: run.settings, result }),
        () => setNote({ kind: 'error', text: 'The test window closed before the provider answered.' }),
      )
    }
    const onMessage = (e: MessageEvent) => {
      const state = (e.data as { planlayerLoginTest?: unknown } | null)?.planlayerLoginTest
      if (e.origin === window.location.origin && typeof state === 'string') finish(state)
    }
    const watch = window.setInterval(() => {
      const run = running.current
      if (run?.popup.closed) finish(run.state)
    }, 1000)
    window.addEventListener('message', onMessage)
    return () => {
      window.removeEventListener('message', onMessage)
      window.clearInterval(watch)
    }
  }, [])

  useEffect(() => {
    let live = true
    const state = testState()
    const kept = readDraft()
    const fail = (e: unknown) => live && setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    api.loginSettings().then((p) => {
      if (!live) return
      setPage(p)
      if (kept && !same(kept, p.settings)) setDraft(kept)
      else if (p.suggested) {
        setDraft(p.suggested)
        setFromEnv(true)
      } else setDraft(p.settings)
      if (state && kept)
        api.oidcTestResult(state).then((result) => {
          if (!live) return
          setTest({ settings: kept, result })
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
    const next = { ...draft, ...change }
    setDraft(next)
    keepDraft(same(next, page.settings) ? null : next)
    setNote(null)
  }
  const tested = test !== null && test.result.ok && same(test.settings, draft)
  const changed = !same(draft, page.settings)
  const needsTest = draft.method !== 'none' && !sameConnection(draft, page.settings) && !tested
  const canSave = !locked && changed && !needsTest

  async function save() {
    if (!draft) return
    try {
      const p = await api.saveLoginSettings(draft)
      setPage(p)
      setDraft(p.settings)
      keepDraft(null)
      setFromEnv(false)
      setNote({ kind: 'ok', text: 'Saved. The login page offers it from now on.' })
    } catch (e) {
      setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    }
  }

  async function testOidc() {
    if (!draft) return
    const settings = draft
    // Opened at the click, before any request: browsers block a popup opened later.
    const popup = window.open('', 'planlayer-login-test', 'popup,width=520,height=700')
    try {
      const { url, state } = await api.testOidc(settings)
      if (popup) {
        running.current = { state, settings, popup }
        setTesting(true)
        setNote(null)
        popup.location.href = url
      } else {
        // Popups blocked: the test runs in this window and comes back to Settings.
        keepDraft(settings)
        window.location.assign(url)
      }
    } catch (e) {
      popup?.close()
      setNote({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    }
  }

  // The groups or app roles the last test found, offered in the group fields.
  const seen = test?.result.groups ?? []
  const field = (key: keyof LoginSettings, label: string, max: number, hint?: string, placeholder?: string, list?: string) => (
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
        list={list}
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
      <p className="su-hint">How users log in besides local accounts. Local accounts, such as the first admin's, always work.</p>
      {locked && <div className="su-note fw-gap">Set by PLANLAYER_LOGIN_* environment variables; read-only here. Test still works.</div>}
      {changed && !locked && (
        <div className="su-note warn fw-gap">Unsaved changes{fromEnv ? ', filled in from PLANLAYER_LOGIN_* variables' : ''}</div>
      )}

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
            {field('oidc_groups_claim', 'Groups', LIMIT.name, 'Entra ID: roles, for app roles by name; groups sends object IDs.')}
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
          <p className="su-hint">
            A role group sets that role at every login through the provider. Empty: the role is set on the Users page.{' '}
            {seen.length > 0 ? `${seen.length} found by the last test; pick them in the fields.` : 'Test to pick from the groups the provider sends.'}
          </p>
          <datalist id="ls-groups">
            {seen.map((g) => (
              <option key={g} value={g} />
            ))}
          </datalist>
          <div className="ls-grid">
            {field('allowed_group', 'Allowed', LIMIT.longText, 'Only its members log in. Empty: everyone the provider knows.', undefined, 'ls-groups')}
            {field('admin_group', 'Admin', LIMIT.longText, undefined, undefined, 'ls-groups')}
            {field('controller_group', 'Controller', LIMIT.longText, undefined, undefined, 'ls-groups')}
            {field('viewer_group', 'Viewer', LIMIT.longText, undefined, undefined, 'ls-groups')}
            {field('hr_group', 'HR', LIMIT.longText, undefined, undefined, 'ls-groups')}
          </div>
          {draft.method === 'ldap' ? (
            <LdapTest settings={draft} onResult={(result) => setTest({ settings: draft, result })} />
          ) : (
            <div className="fw-buttons ls-left">
              <button type="button" className="su-btn" disabled={testing} onClick={() => void testOidc()}>
                {testing ? 'Testing…' : 'Test'}
              </button>
              <span className="lg-hint">
                {testing ? 'Sign in in the window that opened.' : 'Signs in at the provider in a small window. Nothing is saved, no session made.'}
              </span>
            </div>
          )}
          {test && same(test.settings, draft) && <TestResult result={test.result} />}
        </>
      )}

      {note && <div className={`su-note ${note.kind} fw-gap`}>{note.text}</div>}
      {!locked && (
        <div className="fw-buttons">
          {changed && needsTest && <span className="lg-hint">The connection changed: test before saving.</span>}
          {changed && tested && <span className="lg-hint warn">Tested, not saved yet: the login page offers it once saved.</span>}
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
