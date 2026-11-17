import type { ReactNode } from 'react'

// The head of every page (E-13): a small label above the title, the title (or a picker that
// switches what the page shows, such as the cost center of a sheet), a line below it, and the
// page's actions and tabs on the right.

export default function PageHeader({
  eyebrow,
  title,
  sub,
  actions,
}: {
  eyebrow?: ReactNode
  /** Text, or an element that stands in for the title. */
  title: ReactNode
  sub?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="pg-head">
      <div className="pg-title">
        {eyebrow && <div className="su-eyebrow">{eyebrow}</div>}
        {typeof title === 'string' ? <h2 className="su-title">{title}</h2> : title}
        {sub && <div className="pg-sub">{sub}</div>}
      </div>
      {actions && <div className="pg-actions">{actions}</div>}
    </div>
  )
}
