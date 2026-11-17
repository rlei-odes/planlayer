// A line's markers in the grid's first column (ADR 0017): its notes and its item list. They are
// rendered by RevoGrid's cell template; a click on one bubbles to the sheet, which opens the notes
// panel (data-notes) or the item list (data-list) for the line's key.

import type { NotesMarker } from './api'

// RevoGrid's hyperscript function, called with plain props and mixed children.
export type H = (tag: string, props: Record<string, unknown> | null, children?: unknown) => unknown

/** The notes marker's look: its classes, its title and the count of open threads it shows. */
export function noteMarker(marker: NotesMarker | null | undefined): { className: string; title: string; count: string } {
  const open = marker?.open ?? 0
  const has = open > 0 || !!marker?.notepad || (marker?.resolved ?? 0) > 0
  const classes = ['pl-mark', 'note', has ? 'on' : 'empty', marker?.task_for_me ? 'task' : ''].filter(Boolean)
  const title = !has
    ? 'Add a note'
    : [
        open ? `${open} open note${open === 1 ? '' : 's'}` : 'No open notes',
        marker?.task_for_me ? 'a task for you' : '',
        marker?.notepad ? 'a gridnote' : '',
      ]
        .filter(Boolean)
        .join(' · ')
  return { className: classes.join(' '), title, count: open > 0 ? String(open) : '' }
}

/** The notes marker: the count of open threads, accented when one is the user's task. Without
 * notes it shows only when the row is hovered, to add one. */
export function noteMark(h: H, key: string, marker: NotesMarker | null | undefined): unknown {
  const { className, title, count } = noteMarker(marker)
  return h('button', { type: 'button', class: className, 'data-notes': key, title, 'aria-label': title }, count)
}

/** The item list marker: present when the line has a list the user can see; amber when the
 * list's total and the line's year value differ. Without a list it shows on hover, to start one. */
export function listMark(h: H, key: string, has: boolean, differs: boolean): unknown {
  const title = !has ? 'Item list: start one for this line' : differs ? 'Item list · its total differs from the year value' : 'Item list'
  const classes = ['pl-mark', 'list', has ? 'on' : 'empty', differs ? 'stale' : ''].filter(Boolean)
  return h('button', { type: 'button', class: classes.join(' '), 'data-list': key, title, 'aria-label': title }, '')
}

/** The first column's header: its name, and the two markers as a legend, so a sheet without any
 * notes or lists still shows that its lines take them. */
export function headerWithMarks(h: H, name: string, withList: boolean): unknown {
  const marks = [
    h('span', { class: 'pl-mark note on legend', title: 'Notes: click the speech bubble beside a line (it shows when you point at the line)' }, ''),
    withList
      ? h('span', { class: 'pl-mark list on legend', title: 'Item lists: click the list icon beside an account (it shows when you point at the line)' }, '')
      : '',
  ]
  return h('span', { class: 'pl-head-marks' }, [h('span', null, name), h('span', { class: 'pl-marks' }, marks)])
}
