// The colour theme (light or dark): the user's choice, saved in this browser, or the system's
// when they haven't chosen. public/theme.js applies it before the first paint; the top bar's
// toggle changes it here.

export type ThemeChoice = 'system' | 'light' | 'dark'

const KEY = 'planlayer.theme'
const DARK = '(prefers-color-scheme: dark)'

export function savedTheme(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system' // storage unavailable: follow the system
  }
}

export function saveTheme(choice: ThemeChoice) {
  try {
    if (choice === 'system') localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, choice)
  } catch {
    // not saved; the choice holds for this page only
  }
}

/** The theme a choice shows now. */
export function resolvedTheme(choice: ThemeChoice): 'light' | 'dark' {
  return choice === 'system' ? (window.matchMedia(DARK).matches ? 'dark' : 'light') : choice
}

/** Show the choice, and follow the system while it is the choice; returns the cleanup. */
export function applyTheme(choice: ThemeChoice): () => void {
  const show = () => {
    document.documentElement.dataset.theme = resolvedTheme(choice)
  }
  show()
  if (choice !== 'system') return () => undefined
  const media = window.matchMedia(DARK)
  media.addEventListener('change', show)
  return () => media.removeEventListener('change', show)
}
