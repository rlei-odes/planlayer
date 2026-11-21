import { useState } from 'react'
import { api, type LoginPage } from './api'
import { LIMIT } from './limits'

/** The OpenID Connect client secret: saved sealed with the pay key (ADR 0029), never shown
 * again. PLANLAYER_OIDC_CLIENT_SECRET, when set, is used instead. */
export default function ClientSecret({ page, onSaved }: { page: LoginPage; onSaved: (p: LoginPage) => void }) {
  const [secret, setSecret] = useState('')
  const [error, setError] = useState<string | null>(null)
  const fromEnv = page.client_secret === 'env'

  async function save(value: string | null) {
    setError(null)
    try {
      onSaved(await api.saveClientSecret(value))
      setSecret('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const hint = fromEnv
    ? 'Set by PLANLAYER_OIDC_CLIENT_SECRET.'
    : page.client_secret === 'saved'
      ? 'Saved, encrypted. A new one replaces it.'
      : 'Empty: a public client with PKCE. Okta, Entra ID and Google need one.'

  return (
    <div className="su-field ls-wide">
      <label htmlFor="ls-client-secret">Client secret</label>
      <div className="ls-secret">
        <input
          id="ls-client-secret"
          type="password"
          autoComplete="new-password"
          maxLength={LIMIT.longText}
          className="su-input"
          value={secret}
          disabled={fromEnv}
          placeholder={page.client_secret === 'saved' ? '••••••••' : undefined}
          aria-describedby="ls-client-secret-hint"
          onChange={(e) => setSecret(e.target.value)}
        />
        <button type="button" className="su-btn" disabled={fromEnv || !secret.trim()} onClick={() => void save(secret)}>
          Save secret
        </button>
        {page.client_secret === 'saved' && (
          <button type="button" className="su-btn ghost" onClick={() => void save(null)}>
            Remove
          </button>
        )}
      </div>
      <span className={`lg-hint${error ? ' error' : ''}`} id="ls-client-secret-hint">
        {error ?? hint}
      </span>
    </div>
  )
}
