import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { format, parse, sum } from './amounts'
import { api, type LineNotes, type NoteEntry, type NoteLine, type NotepadRow, type Person, type Thread } from './api'
import { initials } from './names'
import Select from './Select'

// The notes of one line (ADR 0017): the line's gridnote, then its threads, open ones first.
// Everyone who reads the line writes notes; a note can @mention people who read it and assign
// the thread to one of them as a task. Threads carried from an earlier round show its name until
// someone writes to them in this round.
//
// Two displays of the same notes: a panel beside the sheet, which leaves the line in view (the
// sheets), or a dialog in the middle of the page, over a list (investments).

const WHEN = new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short' })

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

/** A note's text with its mentions highlighted. */
function Body({ note }: { note: NoteEntry }) {
  const names = note.mentions.map((m) => `@${m.name}`)
  if (names.length === 0) return <p className="nt-body">{note.body}</p>
  const pattern = new RegExp(`(${names.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`)
  return (
    <p className="nt-body">
      {note.body.split(pattern).map((part, i) =>
        names.includes(part) ? (
          <span key={i} className="nt-mention">
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </p>
  )
}

/** Writing a note: text with @mentions picked from the line's readers, and the choice to assign
 * the thread to one of them as a task. Ctrl+Enter sends. */
function Composer({
  people,
  initial = '',
  initialMentions = [],
  placeholder,
  submitLabel,
  allowAssign = true,
  onSubmit,
  onCancel,
}: {
  people: Person[]
  initial?: string
  initialMentions?: Person[]
  placeholder: string
  submitLabel: string
  allowAssign?: boolean
  onSubmit: (body: string, mentions: string[], assign: string | null) => Promise<void>
  onCancel?: () => void
}) {
  const [text, setText] = useState(initial)
  const [picked, setPicked] = useState<Person[]>(initialMentions)
  const [query, setQuery] = useState<string | null>(null)
  const [active, setActive] = useState(0)
  const [assign, setAssign] = useState(false)
  const [assignee, setAssignee] = useState('')
  const [busy, setBusy] = useState(false)
  const box = useRef<HTMLTextAreaElement>(null)
  // Where the caret goes after a mention is picked: set once the new text is rendered, before
  // the next keystroke can land.
  const caret = useRef<number | null>(null)
  useLayoutEffect(() => {
    if (caret.current === null || !box.current) return
    box.current.focus()
    box.current.setSelectionRange(caret.current, caret.current)
    caret.current = null
  }, [text])

  // Only mentions whose @Name is still in the text count.
  const mentions = picked.filter((p) => text.includes(`@${p.name}`))
  const suggestions =
    query === null
      ? []
      : people
          .filter((p) => `${p.name} ${p.login}`.toLowerCase().includes(query.toLowerCase()))
          .slice(0, 6)
  const target = mentions.find((m) => m.login === assignee) ?? mentions[0]

  // The suggestions stand on the page's top layer, at the text box: neither the panel nor the
  // dialog clips them. Below the box, or above it when there is no room below.
  const shown = suggestions.length > 0
  const [place, setPlace] = useState<{ left: number; width: number; top?: number; bottom?: number } | null>(null)
  useLayoutEffect(() => {
    if (!shown) return
    const measure = () => {
      const r = box.current?.getBoundingClientRect()
      if (!r) return
      const above = window.innerHeight - r.bottom < 240 && r.top > window.innerHeight - r.bottom
      setPlace(above ? { left: r.left, width: r.width, bottom: window.innerHeight - r.top + 2 } : { left: r.left, width: r.width, top: r.bottom + 2 })
    }
    measure()
    window.addEventListener('scroll', measure, true)
    window.addEventListener('resize', measure)
    return () => {
      window.removeEventListener('scroll', measure, true)
      window.removeEventListener('resize', measure)
    }
  }, [shown])

  function onInput(value: string) {
    setText(value)
    const caret = box.current?.selectionStart ?? value.length
    const match = /(^|\s)@([^\s@]*)$/.exec(value.slice(0, caret))
    setQuery(match ? match[2] : null)
    setActive(0)
  }

  function pick(p: Person) {
    const at = box.current?.selectionStart ?? text.length
    const before = text.slice(0, at).replace(/@([^\s@]*)$/, `@${p.name} `)
    caret.current = before.length
    setText(before + text.slice(at))
    setPicked((ps) => (ps.some((x) => x.login === p.login) ? ps : [...ps, p]))
    setQuery(null)
  }

  async function submit() {
    if (!text.trim() || busy) return
    setBusy(true)
    try {
      await onSubmit(
        text.trim(),
        mentions.map((m) => m.login),
        assign && target ? target.login : null,
      )
      setText('')
      setPicked([])
      setAssign(false)
    } finally {
      setBusy(false)
    }
  }

  function onKey(e: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (suggestions.length > 0) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        setActive((a) => (a + (e.key === 'ArrowDown' ? 1 : suggestions.length - 1)) % suggestions.length)
        return
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        pick(suggestions[active])
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault() // taken here: the dialog stays open
        setQuery(null)
        return
      }
    }
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      void submit()
    }
    if (e.key === 'Escape' && onCancel) {
      e.preventDefault()
      onCancel()
    }
  }

  return (
    <div className="nt-compose">
      <div className="nt-input">
        <textarea
          ref={box}
          className="su-input"
          rows={3}
          value={text}
          placeholder={placeholder}
          onChange={(e) => onInput(e.target.value)}
          onKeyDown={onKey}
          onBlur={() => setTimeout(() => setQuery(null), 150)}
        />
        {shown &&
          place &&
          createPortal(
            <ul className="nt-suggest" role="listbox" aria-label="Mention" style={place}>
              {suggestions.map((p, i) => (
                <li key={p.login}>
                  <button
                    type="button"
                    className={i === active ? 'active' : undefined}
                    onMouseDown={(e) => {
                      e.preventDefault()
                      pick(p)
                    }}
                  >
                    <span className="nt-avatar sm">{initials(p.name)}</span>
                    {p.name} <span className="ink-3">{p.login}</span>
                  </button>
                </li>
              ))}
            </ul>,
            document.body,
          )}
      </div>
      <div className="nt-compose-foot">
        {allowAssign && mentions.length > 0 ? (
          <label className="nt-assign">
            <input type="checkbox" checked={assign} onChange={(e) => setAssign(e.target.checked)} />
            Assign as a task to
            {mentions.length === 1 ? (
              <strong>{mentions[0].name}</strong>
            ) : (
              <Select
                variant="compact"
                label="Assignee"
                value={target?.login ?? ''}
                options={mentions.map((m) => ({ value: m.login, label: m.name }))}
                onChange={(login) => {
                  setAssignee(login)
                  setAssign(true)
                }}
              />
            )}
          </label>
        ) : (
          <span className="ink-3 nt-tip">Type @ to mention someone</span>
        )}
        <span className="nt-actions">
          {onCancel && (
            <button type="button" className="su-btn ghost sm" onClick={onCancel}>
              Cancel
            </button>
          )}
          <button type="button" className="su-btn primary sm" disabled={!text.trim() || busy} onClick={() => void submit()}>
            {busy ? 'Saving…' : submitLabel}
          </button>
        </span>
      </div>
    </div>
  )
}

function NoteView({
  note,
  people,
  onEdit,
  onDelete,
}: {
  note: NoteEntry
  people: Person[]
  onEdit: (body: string, mentions: string[]) => Promise<void>
  onDelete: () => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  return (
    <div className="nt-note">
      <span className="nt-avatar" aria-hidden="true">
        {initials(note.author.name)}
      </span>
      <div className="nt-note-main">
        <div className="nt-note-head">
          <strong>{note.author.name}</strong>
          <span className="ink-3">
            {WHEN.format(new Date(note.created_at))}
            {note.edited_at && ' · edited'}
          </span>
          {note.mine && !editing && (
            <span className="nt-note-tools">
              <button type="button" className="nt-link" onClick={() => setEditing(true)}>
                Edit
              </button>
              {confirming ? (
                <>
                  <button type="button" className="nt-link danger" onClick={() => void onDelete()}>
                    Delete
                  </button>
                  <button type="button" className="nt-link" onClick={() => setConfirming(false)}>
                    Keep
                  </button>
                </>
              ) : (
                <button type="button" className="nt-link" onClick={() => setConfirming(true)}>
                  Delete
                </button>
              )}
            </span>
          )}
        </div>
        {editing ? (
          <Composer
            people={people}
            initial={note.body}
            initialMentions={note.mentions}
            placeholder="Note"
            submitLabel="Save"
            allowAssign={false}
            onSubmit={async (body, mentions) => {
              await onEdit(body, mentions)
              setEditing(false)
            }}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <Body note={note} />
        )}
      </div>
    </div>
  )
}

function ThreadView({
  thread,
  people,
  focused,
  act,
}: {
  thread: Thread
  people: Person[]
  focused: boolean
  act: {
    reply: (thread: number, body: string, mentions: string[], assign: string | null) => Promise<void>
    resolve: (thread: number, resolved: boolean) => Promise<void>
    edit: (note: number, body: string, mentions: string[]) => Promise<void>
    remove: (note: number) => Promise<void>
  }
}) {
  const [replying, setReplying] = useState(false)
  const resolved = thread.resolved_at !== null
  const first = thread.notes[0]
  // A first note's deletion takes the thread with it, as in a document's comment threads.
  const ref = useRef<HTMLElement>(null)
  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: 'nearest' })
  }, [focused])
  return (
    <article ref={ref} className={`nt-thread${resolved ? ' resolved' : ''}${focused ? ' focused' : ''}`}>
      <div className="nt-thread-head">
        {thread.carried_from && (
          <span className="su-pill neutral" title="Open since an earlier round; writing to it here makes it this round’s.">
            {thread.carried_from}
          </span>
        )}
        {thread.assignee && !resolved && <span className="su-pill nt-task">Task · {thread.assignee.name}</span>}
        {resolved && (
          <span className="ink-3">
            Resolved{thread.resolved_by ? ` by ${thread.resolved_by.name}` : ''} ·{' '}
            {WHEN.format(new Date(thread.resolved_at!))}
          </span>
        )}
        <button
          type="button"
          className="su-btn ghost sm nt-resolve"
          onClick={() => void act.resolve(thread.thread_id, !resolved)}
          title={resolved ? 'Open the thread again' : 'Mark as done: it no longer carries into later rounds'}
        >
          {resolved ? 'Reopen' : 'Resolve'}
        </button>
      </div>
      {thread.notes.map((n) => (
        <NoteView
          key={n.note_id}
          note={n}
          people={people}
          onEdit={(body, mentions) => act.edit(n.note_id, body, mentions)}
          onDelete={() => act.remove(n.note_id)}
        />
      ))}
      {first?.mine && thread.notes.length > 1 && (
        <p className="nt-hint ink-3">Deleting your first note deletes the whole thread.</p>
      )}
      {!resolved &&
        (replying ? (
          <Composer
            people={people}
            placeholder="Reply…"
            submitLabel="Reply"
            onSubmit={async (body, mentions, assign) => {
              await act.reply(thread.thread_id, body, mentions, assign)
              setReplying(false)
            }}
            onCancel={() => setReplying(false)}
          />
        ) : (
          <button type="button" className="nt-link nt-reply" onClick={() => setReplying(true)}>
            Reply
          </button>
        ))}
    </article>
  )
}

/** The line's gridnote: rows of a text and an amount, their sum, and Insert total. Enter adds a
 * row. */
function NotepadView({
  data,
  canInsert,
  onSave,
  onInsert,
}: {
  data: LineNotes
  canInsert: boolean
  onSave: (rows: NotepadRow[]) => Promise<void>
  onInsert: (total: string) => Promise<void>
}) {
  const pad = data.notepad
  const [rows, setRows] = useState<NotepadRow[] | null>(null) // null: not editing
  // Enter in a row adds one below it and moves there, as in a spreadsheet.
  const table = useRef<HTMLTableElement>(null)
  const focusRow = useRef<number | null>(null)
  useLayoutEffect(() => {
    if (focusRow.current === null) return
    table.current?.querySelectorAll<HTMLInputElement>('tbody tr')[focusRow.current]?.querySelector('input')?.focus()
    focusRow.current = null
  }, [rows])
  function newRowAfter(i: number) {
    focusRow.current = i + 1
    setRows((rs) => {
      const next = [...(rs ?? [])]
      next.splice(i + 1, 0, { text: '', amount: null })
      return next
    })
  }
  const onEnter = (i: number) => (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      newRowAfter(i)
    }
  }
  const editing = rows !== null
  const shown = rows ?? pad?.rows ?? []
  const total = sum(shown.map((r) => parse(r.amount ?? '') ?? null))
  const invalid = shown.some((r) => r.amount && parse(r.amount) === undefined)
  if (!pad && !editing) {
    return data.can_write ? (
      <button
        type="button"
        className="su-btn ghost sm nt-pad-add"
        title="A small grid of text and amounts next to the plan value, with a total you can insert as the year value"
        onClick={() => {
          focusRow.current = 0
          setRows([{ text: '', amount: null }])
        }}
      >
        + Gridnote
      </button>
    ) : null
  }
  function change(i: number, patch: Partial<NotepadRow>) {
    setRows((rs) => (rs ?? []).map((r, j) => (j === i ? { ...r, ...patch } : r)))
  }
  return (
    <section className="nt-pad">
      <div className="nt-pad-head">
        <strong>Gridnote</strong>
        {pad && !editing && (
          <span className="ink-3">
            {pad.changed_by.name} · {WHEN.format(new Date(pad.changed_at))}
          </span>
        )}
        {pad?.carried_from && !editing && <span className="su-pill neutral">{pad.carried_from}</span>}
      </div>
      <table className="nt-pad-table" ref={table}>
        <tbody>
          {shown.map((r, i) => (
            <tr key={i}>
              <td>
                {editing ? (
                  <input
                    className="su-input"
                    value={r.text}
                    placeholder="What"
                    onChange={(e) => change(i, { text: e.target.value })}
                    onKeyDown={onEnter(i)}
                  />
                ) : (
                  r.text
                )}
              </td>
              <td className="num">
                {editing ? (
                  <input
                    className={`su-input num${r.amount && parse(r.amount) === undefined ? ' invalid' : ''}`}
                    value={r.amount ?? ''}
                    placeholder="0.00"
                    inputMode="decimal"
                    onChange={(e) => change(i, { amount: e.target.value || null })}
                    onKeyDown={onEnter(i)}
                  />
                ) : (
                  format(r.amount)
                )}
              </td>
              {editing && (
                <td className="nt-pad-x">
                  <button
                    type="button"
                    className="nt-link"
                    aria-label="Remove row"
                    onClick={() => setRows((rs) => (rs ?? []).filter((_, j) => j !== i))}
                  >
                    ×
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td>Total</td>
            <td className="num">{editing ? format(total ?? '0') : format(pad?.total ?? null)}</td>
            {editing && <td />}
          </tr>
        </tfoot>
      </table>
      <div className="nt-pad-foot">
        {editing ? (
          <>
            <button type="button" className="nt-link" onClick={() => newRowAfter(shown.length - 1)}>
              + Row
            </button>
            <span className="nt-actions">
              <button type="button" className="su-btn ghost sm" onClick={() => setRows(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="su-btn primary sm"
                disabled={invalid}
                onClick={() =>
                  void onSave(shown.map((r) => ({ text: r.text, amount: r.amount ? parse(r.amount) ?? null : null }))).then(() =>
                    setRows(null),
                  )
                }
              >
                Save gridnote
              </button>
            </span>
          </>
        ) : (
          <>
            {data.can_write && (
              <button type="button" className="nt-link" onClick={() => setRows(pad?.rows ?? [])}>
                Edit
              </button>
            )}
            {canInsert && pad && (
              <button
                type="button"
                className="su-btn sm"
                title="Write the total as this line’s year value, spread as its badge says. The gridnote and the plan aren’t linked afterwards."
                onClick={() => void onInsert(pad.total)}
              >
                Insert total
              </button>
            )}
          </>
        )}
      </div>
    </section>
  )
}

export default function NotesPanel({
  version,
  line,
  title,
  sub,
  display = 'panel',
  notepad = true,
  canInsert = false,
  focusThread,
  onInsertTotal,
  onChanged,
  onClose,
}: {
  version: string
  line: NoteLine
  title: string
  /** A line below the title. */
  sub?: string
  display?: 'panel' | 'dialog'
  /** The line takes a gridnote (an investment doesn't). */
  notepad?: boolean
  /** The line's year total is editable here: the gridnote's total can be inserted. */
  canInsert?: boolean
  focusThread?: number | null
  onInsertTotal?: (total: string) => Promise<void>
  /** Notes changed: the page reloads its markers. */
  onChanged: () => void
  onClose: () => void
}) {
  const [data, setData] = useState<LineNotes | null>(null)
  const [people, setPeople] = useState<Person[]>([])
  const [error, setError] = useState<string | null>(null)
  const [showResolved, setShowResolved] = useState(false)

  // The line by its fields: the sheet passes a new object on every render.
  const { module, cost_center: costCenter, account, work_center: workCenter, activity_type: activityType, investment } = line
  const load = useCallback(() => {
    const ref = { module, cost_center: costCenter, account, work_center: workCenter, activity_type: activityType, investment }
    api.lineNotes(version, ref).then(setData, (e: unknown) => setError(errorText(e)))
  }, [version, module, costCenter, account, workCenter, activityType, investment])
  useEffect(load, [load])
  useEffect(() => {
    // Who can be mentioned: those who read the cost center (an investment's, too).
    if (costCenter) api.people(costCenter, module).then(setPeople, (e: unknown) => setError(errorText(e)))
  }, [costCenter, module])


  /** Run a change, then reload the panel and the sheet's markers; errors show in the panel. */
  async function run(change: () => Promise<unknown>) {
    setError(null)
    try {
      await change()
      load()
      onChanged()
    } catch (e) {
      setError(errorText(e))
      throw e
    }
  }

  const act = {
    reply: (thread: number, body: string, mentions: string[], assign: string | null) =>
      run(() => api.reply(version, thread, body, mentions, assign)),
    resolve: (thread: number, resolved: boolean) => run(() => api.resolveThread(version, thread, resolved)),
    edit: (note: number, body: string, mentions: string[]) => run(() => api.editNote(version, note, body, mentions)),
    remove: (note: number) => run(() => api.deleteNote(note)),
  }

  const open = data?.threads.filter((t) => t.resolved_at === null) ?? []
  const resolved = data?.threads.filter((t) => t.resolved_at !== null) ?? []
  const focusedResolved = resolved.some((t) => t.thread_id === focusThread)

  const content = (
    <>
      <header className="nt-head">
        <div>
          <div className="su-eyebrow">Notes</div>
          <h3>{title}</h3>
          {sub && <div className="nt-sub ink-3">{sub}</div>}
        </div>
        <button type="button" className="su-icon" aria-label="Close notes" title="Close (Esc)" onClick={onClose}>
          ×
        </button>
      </header>
      {error && <div className="su-note error">{error}</div>}
      {data && (
        <div className="nt-scroll">
          {notepad && (
            <NotepadView
              data={data}
              canInsert={canInsert && data.can_write && !!onInsertTotal}
              onSave={(rows) => run(() => api.saveNotepad(version, line, rows))}
              onInsert={(total) =>
                (onInsertTotal?.(total) ?? Promise.resolve()).catch((e: unknown) => {
                  setError(errorText(e))
                })
              }
            />
          )}
          {open.map((t) => (
            <ThreadView key={t.thread_id} thread={t} people={people} focused={t.thread_id === focusThread} act={act} />
          ))}
          {open.length === 0 && <p className="ink-3 nt-empty">No open notes on this {module === 'capex' ? 'investment' : 'line'}.</p>}
          <Composer
            people={people}
            placeholder="New note: an explanation, a question, a to-do…"
            submitLabel="Add note"
            onSubmit={(body, mentions, assign) => run(() => api.startThread(version, line, body, mentions, assign))}
          />
          {resolved.length > 0 && (
            <div className="nt-resolved">
              <button type="button" className="nt-link" onClick={() => setShowResolved((s) => !s)}>
                {showResolved || focusedResolved ? 'Hide' : 'Show'} {resolved.length} resolved
              </button>
              {(showResolved || focusedResolved) &&
                resolved.map((t) => (
                  <ThreadView key={t.thread_id} thread={t} people={people} focused={t.thread_id === focusThread} act={act} />
                ))}
            </div>
          )}
        </div>
      )}
    </>
  )
  // Keys stay in the notes: the sheet's grid listens on the whole page and would take Tab and the
  // arrows away from the fields. Escape closes them, unless a field uses it; the dialog also
  // closes from an empty field, as nothing typed is lost.
  const onKeyDown = (e: ReactKeyboardEvent) => {
    e.stopPropagation()
    if (e.key !== 'Escape' || e.defaultPrevented) return
    const field = (e.target as HTMLElement).closest<HTMLInputElement | HTMLTextAreaElement>('textarea, input')
    if (!field || (display === 'dialog' && field.value === '')) onClose()
  }

  if (display === 'dialog')
    return (
      <div className="il-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
        {/* The dialog takes the focus when it opens, so Escape reaches it. */}
        <div className="il-dialog nt-dialog" role="dialog" aria-modal="true" aria-label={`Notes on ${title}`} tabIndex={-1} autoFocus onKeyDown={onKeyDown}>
          {content}
        </div>
      </div>
    )
  return (
    <aside className="nt-panel" aria-label={`Notes on ${title}`} onKeyDown={onKeyDown}>
      {content}
    </aside>
  )
}
