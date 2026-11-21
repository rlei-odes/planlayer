import { useEffect, useState } from 'react'
import { api } from './api'
import type * as S from './schema'

const SHOWN_MS = 20_000
const SEEN_KEY = 'planlayer.update-notice' // the version already announced in this browser session

function seen(version: string): boolean {
  try {
    return sessionStorage.getItem(SEEN_KEY) === version
  } catch {
    return false // storage blocked: announce again
  }
}

function markSeen(version: string): void {
  try {
    sessionStorage.setItem(SEEN_KEY, version)
  } catch {
    // storage blocked: the notice may show again on the next load
  }
}

/** For admins: a newer release, once per browser session, gone after 20 seconds. */
export default function UpdateNotice() {
  const [release, setRelease] = useState<S.ReleaseOut | null>(null)

  useEffect(() => {
    let alive = true
    api.release().then(
      (r) => {
        if (alive && r.latest && !seen(r.latest)) {
          markSeen(r.latest)
          setRelease(r)
        }
      },
      (e: unknown) => console.warn('release check:', e),
    )
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    if (!release) return
    const timer = window.setTimeout(() => setRelease(null), SHOWN_MS)
    return () => window.clearTimeout(timer)
  }, [release])

  if (!release) return null
  return (
    <div className="update-notice" role="status">
      <span>
        Planlayer {release.latest} is available <span className="update-notice-now">(running {release.current})</span>
      </span>
      <a href={release.upgrade_url} target="_blank" rel="noopener noreferrer">
        How to upgrade
      </a>
      <button type="button" aria-label="Close" onClick={() => setRelease(null)}>
        ×
      </button>
    </div>
  )
}
