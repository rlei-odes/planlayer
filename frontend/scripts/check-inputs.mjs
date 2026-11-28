// Every text field sets maxLength: an <input> or <textarea> in src/ that takes text must have it,
// from FIELD or LIMIT in src/limits.ts. Inputs of a type that
// takes no free text (checkbox, radio, date, month, file, number) are left out. Run by
// `npm run lint`; fails with the fields that lack it.

import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

const SRC = new URL('../src/', import.meta.url).pathname
const NO_TEXT = /\btype="(checkbox|radio|date|month|file|number)"/

const missing = []
for (const file of readdirSync(SRC).filter((f) => f.endsWith('.tsx'))) {
  const text = readFileSync(join(SRC, file), 'utf8')
  // An element from its tag to the end of its opening tag (props may hold arrows, so `=>` is
  // skipped when looking for the closing `>`).
  for (const match of text.matchAll(/<(input|textarea)\b/g)) {
    let end = match.index
    let depth = 0
    for (let i = match.index + 1; i < text.length; i++) {
      const c = text[i]
      if (c === '{') depth++
      else if (c === '}') depth--
      else if (c === '>' && depth === 0) {
        end = i
        break
      }
    }
    const tag = text.slice(match.index, end + 1)
    if (NO_TEXT.test(tag) || /\bmaxLength=/.test(tag)) continue
    const line = text.slice(0, match.index).split('\n').length
    missing.push(`src/${file}:${line}`)
  }
}

if (missing.length) {
  console.error('Text fields without maxLength (use FIELD or LIMIT from src/limits.ts):')
  for (const m of missing) console.error(`  ${m}`)
  process.exit(1)
}
