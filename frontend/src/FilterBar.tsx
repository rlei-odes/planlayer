import type { ReactNode } from 'react'
import { LIMIT } from './limits'

// The bar above a list (E-13): a search box, filters and toggles on the left, counts and the
// list's own actions on the right (after a `cp-grow` spacer).

export default function FilterBar({ children }: { children: ReactNode }) {
  return <div className="filter-bar">{children}</div>
}

export function FilterSearch({
  value,
  onChange,
  placeholder,
  label,
}: {
  value: string
  onChange: (value: string) => void
  placeholder: string
  /** Accessible name. */
  label: string
}) {
  return (
    <div className="filter-search">
      <svg width="14" height="14" viewBox="0 0 20 20" aria-hidden="true">
        <circle cx="9" cy="9" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.7" />
        <path d="m13.5 13.5 3 3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      </svg>
      <input maxLength={LIMIT.name} type="search" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} aria-label={label} />
    </div>
  )
}
