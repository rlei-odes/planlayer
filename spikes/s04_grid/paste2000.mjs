// Paste a 167 x 12 block (2,004 cells, German number format) into each 58k-cell tab.
import { spawn } from 'node:child_process'
import { chromium } from 'playwright'
const server = spawn('npx', ['vite', 'preview', '--port', '5199', '--strictPort'], { stdio: 'ignore' })
await new Promise((r) => setTimeout(r, 2500))
const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] })
const page = await ctx.newPage()
await page.goto('http://localhost:5199/'); await page.waitForTimeout(800)
const block = Array.from({ length: 167 }, (_, r) => Array.from({ length: 12 }, (_, m) => `${r + 1}.${String(m).padStart(3, '0')},${String(m).padStart(2, '0')}`).join('\t')).join('\n')
for (const tab of ['Glide 50k', 'RevoGrid 50k']) {
  await page.getByRole('button', { name: tab, exact: true }).click(); await page.waitForTimeout(1200)
  if (tab.startsWith('Glide')) {
    const box = await page.locator('canvas').first().boundingBox()
    await page.mouse.click(box.x + 280 + 104 * 2 + 52, box.y + 36 + 34 * 1 + 17)
  } else {
    await page.locator('revogr-data[type="rgRow"]:not([colType]) [data-rgrow="1"][data-rgcol="2"]').first().click()
  }
  await page.waitForTimeout(300)
  await page.evaluate((t) => navigator.clipboard.writeText(t), block)
  const t0 = Date.now()
  await page.keyboard.press('Control+V')
  // Wait until the last pasted cell is in the model.
  const ok = await page.waitForFunction(([t]) => {
    const m = window.__models[t]
    const rows = m.rows.slice(1, 1 + 167)
    return rows.every((r, i) => r.kind !== 'account' || m.values.get(`${r.key}:11`) !== undefined)
  }, [tab], { timeout: 20000 }).then(() => true, () => false)
  const ms = Date.now() - t0
  const sample = await page.evaluate(([t]) => { const m = window.__models[t]; return String(m.values.get(`${m.rows[1].key}:0`)) + ' / ' + String(m.values.get(`${m.rows[2].key}:11`)) }, [tab])
  console.log(`${tab}: ${ok ? 'complete' : 'INCOMPLETE'} in ${ms} ms, first/last sample ${sample}`)
}
await browser.close(); server.kill()
