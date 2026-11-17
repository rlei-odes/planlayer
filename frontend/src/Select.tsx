import { useEffect, useEffectEvent, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { LIMIT } from './limits'

// The app's dropdown (E-13): a button that opens a styled list, instead of the browser's native
// popup. Keyboard: arrows, Home/End, Enter, Escape, Tab; typing jumps to an option, or filters
// the list when it has a search box (long lists). The list is portalled to <body>, so tables and
// scroll containers don't clip it.

export type Option<T extends string = string> = {
  value: T
  label: string
  /** Secondary text, e.g. a code's owner or a status. Searched too. */
  hint?: string
  /** A status dot class (su-dot …), shown before the label. */
  dot?: string
  /** Hierarchy depth, indents the label. */
  depth?: number
  /** Bold, e.g. a group node in an account tree. */
  strong?: boolean
  disabled?: boolean
}

type Props<T extends string> = {
  value: T | ''
  options: Option<T>[]
  onChange: (value: T) => void
  label: string // accessible name
  placeholder?: string
  /** field: form control; pill: top bar; compact: inside tables; action: a button that adds
   * something; title: a page title that switches what the page shows. */
  variant?: 'field' | 'pill' | 'compact' | 'action' | 'title'
  searchable?: boolean
  disabled?: boolean
  className?: string
  icon?: ReactNode
  /** Shown instead of the selected label, e.g. "+ Add account". */
  display?: string
  /** Opens the list at once at this box, without a trigger button: a menu for something that
   * isn't a React element, such as a grid cell. The caller unmounts it on onClose. */
  anchor?: DOMRect
  onClose?: () => void
}

type Place = { left: number; top: number; width: number; maxHeight: number; above: boolean }

const SEARCH_FROM = 12

function placeAt(r: DOMRect): Place {
  const below = window.innerHeight - r.bottom - 12
  const above = r.top - 12
  const flip = below < 220 && above > below
  const width = Math.min(Math.max(r.width, 300), window.innerWidth - 16)
  return {
    left: Math.max(8, Math.min(r.left, window.innerWidth - width - 8)),
    top: flip ? r.top - 4 : r.bottom + 4,
    width,
    maxHeight: Math.min(360, flip ? above : below),
    above: flip,
  }
}

export default function Select<T extends string>(p: Props<T>) {
  const id = useId()
  const trigger = useRef<HTMLButtonElement>(null)
  const popup = useRef<HTMLDivElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const typed = useRef({ text: '', at: 0 })
  const [open, setOpen] = useState(p.anchor !== undefined)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(() =>
    p.anchor ? Math.max(0, p.options.findIndex((o) => o.value === p.value)) : -1,
  )
  const [place, setPlace] = useState<Place | null>(() => (p.anchor ? placeAt(p.anchor) : null))

  const searchable = p.searchable ?? p.options.length > SEARCH_FROM
  const q = query.trim().toLowerCase()
  const shown = q
    ? p.options.filter((o) => `${o.label} ${o.hint ?? ''} ${o.value}`.toLowerCase().includes(q))
    : p.options
  const selected = p.options.find((o) => o.value === p.value)

  function measure() {
    const r = trigger.current?.getBoundingClientRect()
    if (r) setPlace(placeAt(r))
  }

  function show() {
    if (p.disabled) return
    measure()
    setQuery('')
    setActive(Math.max(0, p.options.findIndex((o) => o.value === p.value)))
    setOpen(true)
  }

  function close(refocus: boolean) {
    setOpen(false)
    if (refocus) trigger.current?.focus()
    p.onClose?.()
  }

  function choose(o: Option<T> | undefined) {
    if (!o || o.disabled) return
    close(true)
    if (o.value !== p.value || p.display) p.onChange(o.value)
  }

  // Close on a click outside; follow the trigger when the page scrolls or resizes.
  const anchored = p.anchor !== undefined
  const down = useEffectEvent((e: MouseEvent) => {
    const t = e.target as Node
    if (!popup.current?.contains(t) && !trigger.current?.contains(t)) close(false)
  })
  // An anchored list can't follow its box, so it closes instead.
  const move = useEffectEvent((e: Event) => {
    if (popup.current?.contains(e.target as Node)) return
    if (anchored) close(false)
    else measure()
  })
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => down(e)
    const onMove = (e: Event) => move(e)
    document.addEventListener('mousedown', onDown)
    window.addEventListener('scroll', onMove, true)
    window.addEventListener('resize', onMove)
    return () => {
      document.removeEventListener('mousedown', onDown)
      window.removeEventListener('scroll', onMove, true)
      window.removeEventListener('resize', onMove)
    }
  }, [open])

  // Focus the search box or the list when the popup opens; keep the active option in view.
  useLayoutEffect(() => {
    if (!open) return
    const box = popup.current?.querySelector('input')
    ;(box ?? list.current)?.focus({ preventScroll: true })
  }, [open])
  useLayoutEffect(() => {
    if (open) list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [open, active])

  function step(from: number, by: number): number {
    for (let i = from + by, n = 0; n < shown.length; i += by, n++) {
      const j = (i + shown.length) % shown.length
      if (!shown[j].disabled) return j
    }
    return from
  }

  function onListKey(e: KeyboardEvent) {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setActive((a) => step(a, 1))
        break
      case 'ArrowUp':
        e.preventDefault()
        setActive((a) => step(a, -1))
        break
      case 'Home':
      case 'End':
        if (searchable && e.target instanceof HTMLInputElement) break
        e.preventDefault()
        setActive(e.key === 'Home' ? step(-1, 1) : step(shown.length, -1))
        break
      case 'Enter':
        e.preventDefault()
        choose(shown[active])
        break
      case 'Escape':
        e.preventDefault()
        close(true)
        break
      case 'Tab':
        close(false)
        break
      default:
        if (!searchable && e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
          const now = Date.now()
          const t = typed.current
          t.text = now - t.at > 600 ? e.key.toLowerCase() : t.text + e.key.toLowerCase()
          t.at = now
          const hit = shown.findIndex((o) => !o.disabled && o.label.toLowerCase().startsWith(t.text))
          if (hit >= 0) setActive(hit)
        }
    }
  }

  function onTriggerKey(e: KeyboardEvent) {
    if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) {
      e.preventDefault()
      show()
    }
  }

  const variant = p.variant ?? 'field'
  const text = p.display ?? selected?.label ?? p.placeholder ?? ''
  return (
    <>
      {!anchored && (
        <button
          ref={trigger}
          type="button"
          className={`sel sel-${variant}${open ? ' open' : ''}${selected || p.display ? '' : ' empty'} ${p.className ?? ''}`}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={open ? `${id}-list` : undefined}
          aria-label={p.label}
          disabled={p.disabled}
          onClick={() => (open ? close(false) : show())}
          onKeyDown={onTriggerKey}
        >
          {p.icon}
          {!p.display && selected?.dot && <i className={`su-dot ${selected.dot}`} />}
          <span className="sel-text">{text}</span>
          {!p.display && selected?.hint && variant !== 'pill' && <span className="sel-hint">{selected.hint}</span>}
          <svg className="sel-chev" width="10" height="6" viewBox="0 0 10 6" aria-hidden="true">
            <path d="m1 1 4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
      )}
      {open &&
        place &&
        createPortal(
          <div
            ref={popup}
            className={`sel-pop${place.above ? ' above' : ''}`}
            style={{
              left: place.left,
              width: place.width,
              maxHeight: place.maxHeight,
              ...(place.above ? { bottom: window.innerHeight - place.top } : { top: place.top }),
            }}
            onKeyDown={onListKey}
          >
            {searchable && (
              <div className="sel-search">
                <svg width="14" height="14" viewBox="0 0 20 20" aria-hidden="true">
                  <circle cx="9" cy="9" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.7" />
                  <path d="m13.5 13.5 3 3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
                </svg>
                <input maxLength={LIMIT.name}
                  value={query}
                  placeholder="Search…"
                  aria-label={`Search ${p.label}`}
                  aria-controls={`${id}-list`}
                  aria-activedescendant={shown[active] ? `${id}-${active}` : undefined}
                  onChange={(e) => {
                    setQuery(e.target.value)
                    setActive(0)
                  }}
                />
              </div>
            )}
            <div
              ref={list}
              id={`${id}-list`}
              role="listbox"
              aria-label={p.label}
              tabIndex={-1}
              className="sel-list"
              aria-activedescendant={!searchable && shown[active] ? `${id}-${active}` : undefined}
            >
              {shown.map((o, i) => (
                <div
                  key={o.value}
                  id={`${id}-${i}`}
                  data-index={i}
                  role="option"
                  aria-selected={o.value === p.value}
                  aria-disabled={o.disabled || undefined}
                  className={`sel-opt${i === active ? ' active' : ''}${o.strong ? ' strong' : ''}`}
                  style={o.depth ? { paddingLeft: 10 + o.depth * 14 } : undefined}
                  onMouseEnter={() => !o.disabled && setActive(i)}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => choose(o)}
                >
                  {o.dot && <i className={`su-dot ${o.dot}`} />}
                  <span className="sel-label">{o.label}</span>
                  {o.hint && <span className="sel-hint">{o.hint}</span>}
                  {o.value === p.value && !p.display && (
                    <svg className="sel-check" width="14" height="14" viewBox="0 0 20 20" aria-hidden="true">
                      <path d="m5 10.5 3.2 3L15 6.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
                </div>
              ))}
              {shown.length === 0 && <div className="sel-none">Nothing matches “{query}”.</div>}
            </div>
          </div>,
          document.body,
        )}
    </>
  )
}
