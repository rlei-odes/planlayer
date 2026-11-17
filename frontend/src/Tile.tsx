import type { ReactNode } from 'react'

// A key figure (E-13): a large value over a small label, and a hint below. Tiles stand in a
// `.tiles` grid; on the page itself they are raised like cards, inside a card they are flat.

export default function Tile({
  value,
  label,
  hint,
  tone,
  dot,
  className,
  children,
  onClick,
  pressed,
}: {
  value: ReactNode
  label: ReactNode
  hint?: ReactNode
  /** Colours the value: add, ok or up (green), chg (amber), rem or down (red), same (muted);
   * waiting marks a tile that asks for the user's action. */
  tone?: string
  /** A status dot before the label (su-dot …). */
  dot?: string
  className?: string
  /** Below the label, e.g. a progress bar. */
  children?: ReactNode
  /** Makes the tile a button. */
  onClick?: () => void
  /** A tile that filters a list: whether its filter is on. */
  pressed?: boolean
}) {
  const classes = ['tile', tone, className].filter(Boolean).join(' ')
  const content = (
    <>
      <div className="tile-value">{value}</div>
      <div className="tile-label">
        {dot && <i className={`su-dot ${dot}`} />}
        {label}
      </div>
      {hint && <div className="tile-hint">{hint}</div>}
      {children}
    </>
  )
  return onClick ? (
    <button type="button" className={classes} aria-pressed={pressed} onClick={onClick}>
      {content}
    </button>
  ) : (
    <div className={classes}>{content}</div>
  )
}
