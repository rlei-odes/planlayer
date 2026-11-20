// Screenshots of the main pages, logged in through the demo login (PLANLAYER_DEMO).
// Usage: node shots.mjs [--website] [base URL] [width]   (default http://localhost:5173, 1440 wide)
// Only the names given in SHOTS (comma-separated) are taken, e.g. SHOTS=capex,status.
//
// Without --website: one light screenshot per page in out/, for reviews. With --website: the
// website's highlights, each as a pair in out/website/, <name>-light.png and <name>-dark.png, for
// the website to show the one matching its visitor's colour scheme. The browser states the scheme;
// the app follows it, as it does for a user who hasn't chosen a theme.

import { mkdir } from 'node:fs/promises'
import { chromium } from 'playwright'

const args = process.argv.slice(2)
const website = args.includes('--website')
const [base = 'http://localhost:5173', width = '1440'] = args.filter((a) => !a.startsWith('--'))
const out = new URL(website ? './out/website/' : './out/', import.meta.url).pathname
const viewport = { width: Number(width), height: 900 }
const schemes = website ? ['light', 'dark'] : ['light']
const only = process.env.SHOTS?.split(',')

const CONTROLLER = 'controller@nordwerk.example'
const ADMIN = 'admin@nordwerk.example'
const OWNER = 'anna.keller@nordwerk.example' // plans DE1010 Machining and DE1020 Quality Assurance
const HR = 'hr@nordwerk.example'

// The number format every screenshot shows, whatever the user chose: each user's own choice is
// set for the shot and put back afterwards.
const NUMBER_FORMAT = { separators: 'comma_dot', decimals: false, scale: 'full', units: true }

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

// The website's highlights (docs_local/website/copy.md), on the development data (seed-dev-plans,
// seed-dev-notes, seed-dev-capex, seed-dev-workforce, seed-dev-item-lists). `unit` is the cost center the page opens
// with; `prepare` brings the page into the state shown, without saving anything.
const highlights = [
  { name: 'home', user: CONTROLLER, page: 'home' },
  { name: 'planning', user: OWNER, page: 'planning', round: 'B2026', unit: 'DE1020' },
  // Her own lists, the controller's shared with her cost center, Luca Meier's where she is deputy;
  // one of hers open over them.
  {
    name: 'item-lists',
    user: OWNER,
    page: 'lists',
    prepare: (page) => page.locator('.il-row', { hasText: 'DE1010' }).filter({ hasText: '6300' }).click(),
  },
  {
    name: 'notes',
    user: OWNER,
    page: 'planning',
    round: 'B2026',
    unit: 'DE1010',
    prepare: (page) => page.locator('.pl-mark.note.task').first().click(),
  },
  { name: 'setup', user: CONTROLLER, page: 'setup', round: 'B2026' },
  // Wider: at 1440 the table's last column (the row actions) scrolls out of its card.
  { name: 'status', user: CONTROLLER, page: 'status', round: 'B2026', width: 1600 },
  {
    name: 'investments',
    user: CONTROLLER,
    page: 'capex',
    round: 'B2026',
    // The first project holds lines added in walkthroughs: it is closed, and the open project below
    // it selected, which shows the bar for confirming several at once.
    prepare: async (page) => {
      await page.locator('.cx-toggle').first().click()
      await page.getByRole('button', { name: 'Select ERP upgrade' }).click()
    },
  },
  {
    name: 'investments-overview',
    user: CONTROLLER,
    page: 'capex',
    round: 'B2026',
    prepare: (page) => page.getByRole('tab', { name: 'Overview' }).click(),
  },
  { name: 'capacity', user: CONTROLLER, page: 'capacity', round: 'B2026', unit: 'DE1010' },
  { name: 'workforce', user: CONTROLLER, page: 'workforce', round: 'W2026', unit: 'DE1010' },
  {
    name: 'personnel',
    user: HR,
    page: 'personnel',
    round: 'ZT26',
    unit: 'DE1010',
    // One employee's pay open: salary, extras and the costs they make, month by month.
    prepare: (page) => page.locator('tr', { hasText: 'Ben Carter' }).getByRole('button', { name: /^(Edit|View)$/ }).click(),
  },
  {
    name: 'publish',
    user: CONTROLLER,
    page: 'publish',
    round: 'B2026',
    prepare: (page) => page.getByRole('button', { name: 'Preview publish' }).click(),
  },
]

await mkdir(out, { recursive: true })
const browser = await chromium.launch()
try {
  for (const shot of (website ? highlights : shots).filter((s) => !only || only.includes(s.name))) {
    for (const colorScheme of schemes) {
      const context = await browser.newContext({ viewport: { ...viewport, width: shot.width ?? viewport.width }, colorScheme })
      const res = await context.request.post(`${base}/api/auth/demo-login`, { data: { login: shot.user } })
      if (!res.ok()) throw new Error(`Dev login as ${shot.user} failed: ${res.status()}`)
      const own = (await (await context.request.get(`${base}/api/me`)).json()).number_own
      const setFormat = async (data) => {
        const r = await context.request.put(`${base}/api/me/number-format`, { data })
        if (!r.ok()) throw new Error(`Setting the number format of ${shot.user} failed: ${r.status()}`)
      }
      await setFormat(NUMBER_FORMAT)
      try {
        await context.addInitScript(([round, unit]) => {
          if (round) localStorage.setItem('planlayer.round', round)
          if (unit) localStorage.setItem('planlayer.costCenter', unit)
        }, [shot.round, shot.unit])
        const page = await context.newPage()
        await page.goto(`${base}/#${shot.page}`)
        await page.waitForLoadState('networkidle')
        await page.waitForTimeout(500) // the grids lay out after their data arrives
        if (shot.prepare) {
          await shot.prepare(page)
          await page.waitForLoadState('networkidle')
          await page.waitForTimeout(500)
        }
        const file = website ? `${shot.name}-${colorScheme}.png` : `${shot.name}.png`
        await page.screenshot({ path: `${out}${file}` })
        console.log(file)
      } finally {
        await setFormat(own)
        await context.close()
      }
    }
  }
} finally {
  await browser.close()
}
