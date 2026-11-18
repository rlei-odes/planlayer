import { useEffect, useRef, useState } from 'react'
import { api, type Me } from './api'
import { example, SEPARATORS, type NumberFormat, type Scale, type Separators } from './amounts'
import Select from './Select'

// The number format (E-13): separators, cents and scale. The admin sets the installation's on the
// settings page; each user may choose their own from the top bar.

const SCALES: { value: Scale; label: string; hint: string }[] = [
  { value: 'full', label: '1', hint: 'Full amounts' },
  { value: 'k', label: 'k', hint: 'In thousands, three significant digits: 10.5k, 123k' },
  { value: 'M', label: 'M', hint: 'In millions, three significant digits: 1.23M' },
]

const SEPARATOR_OPTIONS = (Object.keys(SEPARATORS) as Separators[]).map((s) => ({
  value: s,
  label: SEPARATORS[s].example,
}))

/** A segmented choice of scale; on the planning sheet too. */
export function ScaleToggle({ value, onChange }: { value: Scale; onChange: (s: Scale) => void }) {
  return (
    <div className="seg nf-scale" role="group" aria-label="Scale">
      {SCALES.map((s) => (
        <button key={s.value} type="button" aria-pressed={value === s.value} title={s.hint} onClick={() => onChange(s.value)}>
          {s.label}
        </button>
      ))}
    </div>
  )
}

/** Separators, cents and scale, with an example of each. */
export function NumberFormatFields({ value, onChange }: { value: NumberFormat; onChange: (f: NumberFormat) => void }) {
  return (
    <div className="nf-fields">
      <div className="nf-field">
        <span>Separators</span>
        <Select
          label="Separators"
          value={value.separators}
          options={SEPARATOR_OPTIONS}
          onChange={(separators) => onChange({ ...value, separators })}
        />
      </div>
      <div className="nf-field">
        <span>Cents</span>
        <div className="seg" role="group" aria-label="Cents">
          {[true, false].map((on) => (
            <button key={String(on)} type="button" aria-pressed={value.decimals === on} onClick={() => onChange({ ...value, decimals: on })}>
              {on ? 'Shown' : 'Hidden'}
            </button>
          ))}
        </div>
      </div>
      <div className="nf-field">
        <span>Scale</span>
        <ScaleToggle value={value.scale} onChange={(scale) => onChange({ ...value, scale })} />
      </div>
    </div>
  )
}

/** The top bar's number format: the user's own choices over the installation's. */
export function NumberFormatMenu({ me, onMe }: { me: Me; onMe: (me: Me) => void }) {
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node) && !(e.target as Element).closest?.('.sel-pop')) setOpen(false)
    }
    const escape = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  const own = me.number_own
  const custom = own.separators != null || own.decimals != null || own.scale != null

  /** Only what was changed becomes the user's own; the rest keeps following the default. */
  function save(next: NumberFormat | null) {
    const shown = me.number_format
    const body = next
      ? {
          separators: next.separators !== shown.separators ? next.separators : own.separators,
          decimals: next.decimals !== shown.decimals ? next.decimals : own.decimals,
          scale: next.scale !== shown.scale ? next.scale : own.scale,
        }
      : { separators: null, decimals: null, scale: null }
    api.setOwnNumberFormat(body).then(
      (m) => {
        setError(null)
        onMe(m)
      },
      (e: unknown) => setError(e instanceof Error ? e.message : String(e)),
    )
  }

  return (
    <div className="nf-menu" ref={box}>
      <button
        type="button"
        className="nf-trigger"
        aria-expanded={open}
        title="Number format"
        onClick={() => setOpen((o) => !o)}
      >
        {SEPARATORS[me.number_format.separators].example}
      </button>
      {open && (
        <div className="nf-pop" role="dialog" aria-label="Number format">
          <h4>Number format</h4>
          <p className="su-hint">
            {custom ? 'Your own choice.' : 'The installation’s default.'} Display only: values are typed and stored
            exactly.
          </p>
          <NumberFormatFields value={me.number_format} onChange={save} />
          <p className="nf-example">{example(me.number_format)}</p>
          {error && <div className="su-note error">{error}</div>}
          {custom && (
            <button type="button" className="su-btn" onClick={() => save(null)}>
              Use the default
            </button>
          )}
        </div>
      )}
    </div>
  )
}
