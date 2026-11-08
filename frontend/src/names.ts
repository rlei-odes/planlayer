// Display helpers for people.

/** "Anna Keller" → "AK"; "head.pumps@…" → "HP". */
export function initials(name: string): string {
  const words = name.split('@')[0].split(/[\s._-]+/).filter(Boolean)
  return words
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join('')
}
