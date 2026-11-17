// Screenshots of the main pages, logged in through the development login.
// Usage: node shots.mjs [base URL] [width]   (default http://localhost:5173, 1440 pixels wide)
// Only the names given in SHOTS (comma-separated) are taken, e.g. SHOTS=capex,status.

import { mkdir } from 'node:fs/promises'
import { chromium } from 'playwright'

const base = process.argv[2] ?? 'http://localhost:5173'
const out = new URL('./out/', import.meta.url).pathname
const viewport = { width: Number(process.argv[3] ?? 1440), height: 900 }
const only = process.env.SHOTS?.split(',')

const CONTROLLER = 'controller@nordwerk.example'
const ADMIN = 'admin@nordwerk.example'

// One entry per screenshot: the user who sees the page, its route in App.tsx (the hash) and,
// for pages that work on a round, the round to show (stored the way the app remembers it).
const shots = [
  { name: 'home', user: CONTROLLER, page: 'home' },
  { name: 'capacity', user: CONTROLLER, page: 'capacity', round: 'B2027' },
  { name: 'workforce', user: CONTROLLER, page: 'workforce', round: 'W2026' },
  { name: 'personnel', user: CONTROLLER, page: 'personnel', round: 'ZT26' },
  { name: 'planning', user: CONTROLLER, page: 'planning', round: 'B2027' },
  { name: 'capex', user: CONTROLLER, page: 'capex', round: 'B2026' },
  { name: 'status', user: CONTROLLER, page: 'status', round: 'B2027' },
  { name: 'lists', user: CONTROLLER, page: 'lists' },
  { name: 'setup', user: CONTROLLER, page: 'setup', round: 'B2027' },
  { name: 'forwarding', user: CONTROLLER, page: 'forwarding', round: 'B2027' },
  { name: 'publish', user: CONTROLLER, page: 'publish', round: 'B2027' },
  { name: 'masterdata', user: CONTROLLER, page: 'masterdata' },
  { name: 'responsibilities', user: CONTROLLER, page: 'responsibilities' },
  { name: 'users', user: ADMIN, page: 'users' },
  { name: 'settings', user: ADMIN, page: 'settings' },
]

await mkdir(out, { recursive: true })
const browser = await chromium.launch()
try {
  for (const shot of shots.filter((s) => !only || only.includes(s.name))) {
    const context = await browser.newContext({ viewport })
    const res = await context.request.post(`${base}/api/auth/dev-login`, { data: { login: shot.user } })
    if (!res.ok()) throw new Error(`Dev login as ${shot.user} failed: ${res.status()}`)
    if (shot.round) await context.addInitScript((r) => localStorage.setItem('planlayer.round', r), shot.round)
    const page = await context.newPage()
    await page.goto(`${base}/#${shot.page}`)
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(500) // the grids lay out after their data arrives
    await page.screenshot({ path: `${out}${shot.name}.png` })
    console.log(`${shot.name}.png`)
    await context.close()
  }
} finally {
  await browser.close()
}
