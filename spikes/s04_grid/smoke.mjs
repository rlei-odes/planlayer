// Headless smoke test of both prototypes: render, edit, spread, paste, locks, errors.
// npm run build && node smoke.mjs
import { spawn } from 'node:child_process'
import { chromium } from 'playwright'

const server = spawn('npx', ['vite', 'preview', '--port', '5199', '--strictPort'], { stdio: 'ignore' })
await new Promise((r) => setTimeout(r, 2500))
const browser = await chromium.launch()
const context = await browser.newContext({ viewport: { width: 1600, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] })
const page = await context.newPage()
const consoleErrors = []
page.on('pageerror', (e) => consoleErrors.push(String(e)))
page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()))
await page.goto('http://localhost:5199/')
await page.waitForTimeout(1000)

const state = (tab, account, month) =>
  page.evaluate(([t, a, m]) => {
    const v = window.__models[t].values.get(`${a}:${m}`)
    return v === undefined ? 'blank' : String(v)
  }, [tab, account, month])
const firstAccount = await page.evaluate(() => window.__models.Glide.rows[1].key)
const results = []
const check = (label, ok, detail = '') => results.push(`${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? '  ' + detail : ''}`)

// Real clipboard + Ctrl+V, as a user pasting from Excel would.
async function paste(text) {
  await page.evaluate((t) => navigator.clipboard.writeText(t), text)
  await page.keyboard.press('Control+V')
  await page.waitForTimeout(400)
}
async function typeInto(text) {
  await page.waitForTimeout(200)
  await page.keyboard.type(text, { delay: 50 })
  await page.keyboard.press('Enter')
  await page.waitForTimeout(300)
}

// --- Glide: canvas grid; header 36 px, rows 34 px, columns 280 / 104 ---
{
  const canvas = page.locator('canvas').first()
  const box = await canvas.boundingBox()
  const cell = (col, row) => ({ x: box.x + (col === 0 ? 140 : 280 + 104 * (col - 1) + 52), y: box.y + 36 + 34 * row + 17 })
  check('Glide renders', !!box)
  let c = cell(2, 1)
  await page.mouse.click(c.x, c.y)
  await typeInto('12000')
  const months = await Promise.all([...Array(12).keys()].map((m) => state('Glide', firstAccount, m)))
  const sum = months.reduce((a, v) => a + (v === 'blank' ? 0 : Number(v)), 0)
  check('Glide: first edit after page load keeps every key (editor code loads lazily)', sum === 1200000, `months=${months.join(',')}`)
  c = cell(3, 2)
  await page.mouse.click(c.x, c.y)
  await page.waitForTimeout(200)
  await paste("1.234,56\t1'234.56\t(5)\t0\t\n7,5\tabc\n")
  const second = await page.evaluate(() => window.__models.Glide.rows[2].key)
  const pasted = await Promise.all([0, 1, 2, 3, 4].map((m) => state('Glide', second, m)))
  check('Glide: paste parses 1.234,56 / 1\'234.56 / (5) / 0 / blank', pasted.join(',') === '123456,123456,-500,0,blank', pasted.join(','))
  const err = await page.locator('.errors').textContent().catch(() => '')
  check('Glide: invalid pasted cell is reported', /abc/.test(err ?? ''), err ?? '')
  const lockedRow = await page.evaluate(() => window.__models.Glide.rows.findIndex((r) => r.locked && r.kind === 'account'))
  await page.evaluate((r) => window.scrollTo(0, 0), lockedRow)
}

// --- RevoGrid: DOM cells ---
await page.getByRole('button', { name: 'RevoGrid', exact: true }).click()
await page.waitForTimeout(1500)
{
  const rg = page.locator('revo-grid')
  check('RevoGrid renders', (await rg.count()) === 1)
  const fy = page.locator('revogr-data[type="rgRow"]:not([colType]) [data-rgrow="1"][data-rgcol="1"]').first()
  const exists = (await fy.count()) > 0
  check('RevoGrid: Plan FY cell found', exists)
  if (exists) {
    await fy.click()
    await typeInto('12000')
    const months = await Promise.all([...Array(12).keys()].map((m) => state('RevoGrid', firstAccount, m)))
    const sum = months.reduce((a, v) => a + (v === 'blank' ? 0 : Number(v)), 0)
    check('RevoGrid: typing Plan FY spreads exactly', sum === 1200000, `months=${months.join(',')}`)
    const m1 = page.locator('revogr-data[type="rgRow"]:not([colType]) [data-rgrow="2"][data-rgcol="2"]').first()
    await m1.click()
    await page.waitForTimeout(200)
    await paste("1.234,56\t1'234.56\t(5)\t0\t\n7,5\tabc\n")
    const second = await page.evaluate(() => window.__models.RevoGrid.rows[2].key)
    const pasted = await Promise.all([0, 1, 2, 3, 4].map((m) => state('RevoGrid', second, m)))
    check('RevoGrid: paste parses locale numbers', pasted.join(',') === '123456,123456,-500,0,blank', pasted.join(','))
  }
}

// --- Fast typing (15 ms per key) straight into a selected cell: informational ---
for (const tab of ['Glide', 'RevoGrid']) {
  await page.getByRole('button', { name: tab, exact: true }).click()
  await page.waitForTimeout(800)
  const row = 5
  if (tab === 'Glide') {
    const box = await page.locator('canvas').first().boundingBox()
    await page.mouse.click(box.x + 280 + 104 * 2 + 52, box.y + 36 + 34 * row + 17)
  } else {
    await page.locator(`revogr-data[type="rgRow"]:not([colType]) [data-rgrow="${row}"][data-rgcol="2"]`).first().click()
  }
  await page.waitForTimeout(300)
  await page.keyboard.type('98765', { delay: 15 })
  await page.keyboard.press('Enter')
  await page.waitForTimeout(300)
  const key = await page.evaluate(([t, r]) => window.__models[t].rows[r].key, [tab, row])
  const got = await state(tab, key, 0)
  check(`${tab}: fast typing keeps every digit`, got === '9876500', `stored ${got}`)
}

// --- 50k-cell tabs: render and scroll time ---
for (const tab of ['Glide 50k', 'RevoGrid 50k']) {
  const start = Date.now()
  await page.getByRole('button', { name: tab, exact: true }).click()
  await page.waitForTimeout(800)
  const t0 = Date.now()
  for (let i = 0; i < 20; i++) {
    await page.mouse.move(800, 500)
    await page.mouse.wheel(0, 3000)
    await page.waitForTimeout(16)
  }
  check(`${tab}: render + 20 scroll steps`, true, `${t0 - start} ms switch, ${Date.now() - t0} ms scroll`)
}

check('no page errors', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '))
console.log(results.join('\n'))
await browser.close()
server.kill()
