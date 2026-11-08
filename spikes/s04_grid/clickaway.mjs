// Type a value, then click another cell instead of pressing Enter: is the value kept?
import { spawn } from 'node:child_process'
import { chromium } from 'playwright'
const server = spawn('npx', ['vite', 'preview', '--port', '5199', '--strictPort'], { stdio: 'ignore' })
await new Promise((r) => setTimeout(r, 2500))
const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] })
const page = await ctx.newPage()
await page.goto('http://localhost:5199/'); await page.waitForTimeout(1000)
const stored = (tab, row, m) => page.evaluate(([t, r, mm]) => String(window.__models[t].values.get(window.__models[t].rows[r].key + ':' + mm)), [tab, row, m])
const box = await page.locator('canvas').first().boundingBox()
const g = (col, row) => [box.x + 280 + 104 * (col - 1) + 52, box.y + 36 + 34 * row + 17]
// Glide: warm up the editor once, then the real test.
await page.mouse.click(...g(3, 1)); await page.keyboard.press('Enter'); await page.keyboard.press('Escape')
await page.mouse.click(...g(4, 2)); await page.waitForTimeout(200)
await page.keyboard.type('4321', { delay: 50 }); await page.mouse.click(...g(6, 6)); await page.waitForTimeout(400)
console.log('Glide    type + click away ->', await stored('Glide', 2, 1))
// Glide paste repaint: the pasted value must be drawn without clicking away.
await page.mouse.click(...g(3, 3)); await page.waitForTimeout(200)
await page.evaluate(() => navigator.clipboard.writeText('777'))
await page.keyboard.press('Control+V'); await page.waitForTimeout(400)
await page.screenshot({ path: process.env.SHOT, clip: { x: box.x, y: box.y, width: 700, height: 200 } })
await page.getByRole('button', { name: 'RevoGrid', exact: true }).click(); await page.waitForTimeout(1500)
const cell = (r, c) => page.locator(`revogr-data[type="rgRow"]:not([colType]) [data-rgrow="${r}"][data-rgcol="${c}"]`).first()
await cell(2, 2).click(); await page.waitForTimeout(200)
await page.keyboard.type('4321', { delay: 50 }); await cell(6, 5).click(); await page.waitForTimeout(400)
console.log('RevoGrid type + click away ->', await stored('RevoGrid', 2, 0))
await browser.close(); server.kill()
