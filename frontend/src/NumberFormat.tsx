import { useEffect, useRef, useState } from 'react'
import { api, type Me } from './api'
import { example, SEPARATORS, type NumberFormat, type Scale, type Separators } from './amounts'
import { useScale } from './numberContext'
import Select from './Select'

// The number format (E-13): separators, cents, scale and units. The admin sets the installation's on the
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

/** The sheets' and overviews' scale toggle: changes the user's own scale. */
export function SheetScale() {
  const { scale, setScale } = useScale()
  return <ScaleToggle value={scale} onChange={setScale} />
}

/** A segmented choice of scale. */
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

function OnOff({ label, value, onChange }: { label: string; value: boolean; onChange: (on: boolean) => void }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {[true, false].map((on) => (
        <button key={String(on)} type="button" aria-pressed={value === on} onClick={() => onChange(on)}>
          {on ? 'Shown' : 'Hidden'}
        </button>
      ))}
    </div>
  )
}

/** Separators, cents, scale and units. */
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
        <OnOff label="Cents" value={value.decimals} onChange={(decimals) => onChange({ ...value, decimals })} />
      </div>
      <div className="nf-field">
        <span>Scale</span>
        <ScaleToggle value={value.scale} onChange={(scale) => onChange({ ...value, scale })} />
      </div>
      <div className="nf-field" title="The k or M after amounts in thousands or millions">
        <span>Units</span>
        <OnOff label="Units" value={value.units} onChange={(units) => onChange({ ...value, units })} />
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
  const custom = own.separators != null || own.decimals != null || own.scale != null || own.units != null

  /** Only what was changed becomes the user's own; the rest keeps following the default. */
  function save(next: NumberFormat | null) {
    const shown = me.number_format
    const body = next
      ? {
          separators: next.separators !== shown.separators ? next.separators : own.separators,
          decimals: next.decimals !== shown.decimals ? next.decimals : own.decimals,
          scale: next.scale !== shown.scale ? next.scale : own.scale,
          units: next.units !== shown.units ? next.units : own.units,
        }
      : { separators: null, decimals: null, scale: null, units: null }
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
