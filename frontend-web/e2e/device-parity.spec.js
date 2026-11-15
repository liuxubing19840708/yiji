import { test, expect } from '@playwright/test'

const pageOf = (items) => ({ count: items.length, next: null, previous: null, results: items })
const pathOf = (request) => new URL(request.url()).pathname.replace(/^\/api/, '')
async function json(route, data, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
}
const session = {
  access: 'device-parity-mock-session', companyId: 1, user: { id: 9, real_name: 'Mock 设备操作员' },
  companies: [{ id: 1, name: 'Mock 制衣公司', post: { name: 'Mock 管理', is_admin: true } }],
}
async function installSession(page) {
  await page.addInitScript((value) => localStorage.setItem('yisao.web.session.v1', JSON.stringify(value)), session)
}

const ticket = { id: 31, production: 7, bed_no: 'MOCK-BED', bundle_no: 3, style_no: 'MOCK-STYLE', style_name: '中文测试款', color: '中国红', size: '中码', qty: 10, qr_code: 'MOCK-QR-31' }
const production = { id: 7, bed_no: 'MOCK-BED', style_no: 'MOCK-STYLE', style_name: '中文测试款', total_qty: 10, cut_total: 10, ticket_count: 1, done_ticket_count: 0, registered_qty: 0, is_locked: false, is_cut_done: false }

async function prepareScan(page) {
  await page.getByLabel('菲票二维码内容').fill('MOCK-QR-31')
  await page.getByRole('button', { name: '解析菲票', exact: true }).click()
  await expect(page.locator('.scan-process-select select')).toHaveValue('5')
  await expect(page.getByRole('button', { name: '确认登记', exact: true })).toBeVisible()
}

test('扫码网络失败入队并显示摘要，手动重发沿用幂等键，HTTP 400 不入队', async ({ page }) => {
  const recordRequests = []
  let failNetwork = true
  await installSession(page)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = pathOf(request)
    if (path === '/notifications') return json(route, pageOf([]))
    if (path === '/records/workbench') return json(route, { my_today: [], my_today_amount: '0.00' })
    if (path === '/scan/parse' && request.method() === 'POST') return json(route, { ticket })
    if (path === '/records/process-selector') return json(route, { results: [{ id: 5, name: 'Mock 车缝', price: '1.25' }], current_process: null, single: true })
    if (path === '/employees') return json(route, pageOf([]))
    if (path === '/records' && request.method() === 'POST') {
      recordRequests.push({ headers: request.headers(), body: request.postDataJSON() })
      if (failNetwork) {
        failNetwork = false
        return route.abort('failed')
      }
      if (recordRequests.length === 3) return json(route, { detail: 'Mock HTTP 400：登记数量超限' }, 400)
      return json(route, { id: 901, process_name: 'Mock 车缝', qty: 10, amount: '12.50' }, 201)
    }
    return json(route, { detail: `未配置 mock：${request.method()} ${path}` }, 500)
  })
  await page.goto('/web/#scan')
  await prepareScan(page)
  await page.getByRole('button', { name: '确认登记', exact: true }).click()
  const queue = page.getByLabel('待同步登记')
  await expect(queue).toBeVisible()
  await expect(queue).toContainText('床次 MOCK-BED · 3 扎 · Mock 车缝 10 件')
  await expect(page.locator('.scan-success')).toContainText('网络连接失败，登记已保存到本机待同步队列')
  const queued = await page.evaluate(() => JSON.parse(localStorage.getItem('yisao.web.scan-offline.v1.1.9')))
  expect(queued).toHaveLength(1)
  expect(queued[0].idempotencyKey).toBe(recordRequests[0].headers['idempotency-key'])
  expect(recordRequests[0].body).toMatchObject({ ticket_id: 31, process_id: 5, qty: 10 })

  await queue.getByRole('button', { name: '手动重发', exact: true }).click()
  await expect(queue).toHaveCount(0)
  await expect(page.locator('.scan-success')).toContainText('已成功同步 1 条登记')
  expect(recordRequests).toHaveLength(2)
  expect(recordRequests[1].headers['idempotency-key']).toBe(recordRequests[0].headers['idempotency-key'])
  expect(recordRequests[1].body).toEqual(recordRequests[0].body)
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('yisao.web.scan-offline.v1.1.9')))).toEqual([])

  await prepareScan(page)
  await page.getByRole('button', { name: '确认登记', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'Mock HTTP 400：登记数量超限' })).toBeVisible()
  await expect(page.getByLabel('待同步登记')).toHaveCount(0)
  expect(recordRequests).toHaveLength(3)
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('yisao.web.scan-offline.v1.1.9')))).toEqual([])
})

test('AI SpeechRecognition mock 识别文字进入输入框但不自动发送', async ({ page }) => {
  let queryCount = 0
  await installSession(page)
  await page.addInitScript(() => {
    window.SpeechRecognition = class MockSpeechRecognition {
      start() { window.__mockRecognition = this; this.onstart?.() }
      stop() { this.onend?.() }
      abort() { this.onend?.() }
    }
  })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = pathOf(request)
    if (path === '/notifications') return json(route, pageOf([]))
    if (path === '/ai/report/query' && request.method() === 'POST') {
      queryCount += 1
      return json(route, { answer: '不应自动发送' })
    }
    return json(route, { detail: `未配置 mock：${request.method()} ${path}` }, 500)
  })
  await page.goto('/web/#ai')
  const input = page.getByLabel('输入业务问题')
  await input.fill('请查询')
  await page.getByRole('button', { name: '语音输入', exact: true }).click()
  await expect(page.getByRole('button', { name: '停止识别', exact: true })).toBeVisible()
  await page.evaluate(() => {
    window.__mockRecognition.onresult?.({ results: [[{ transcript: '今天完成多少件' }]] })
    window.__mockRecognition.onend?.()
  })
  await expect(input).toHaveValue('请查询 今天完成多少件')
  await expect(input).toBeEnabled()
  await expect(page.locator('.speech-feedback')).toContainText('请复核')
  await expect(page.locator('.chat-message.user')).toHaveCount(0)
  expect(queryCount).toBe(0)
})

async function installBluetooth(page) {
  await page.addInitScript(() => {
    window.__bleWrites = []
    const characteristic = {
      uuid: 'mock-write-characteristic',
      properties: { write: true, writeWithoutResponse: true },
      async writeValueWithoutResponse(chunk) { window.__bleWrites.push(Array.from(chunk)) },
    }
    const service = {
      uuid: '0000ff00-0000-1000-8000-00805f9b34fb',
      async getCharacteristics() { return [characteristic] },
    }
    const device = {
      id: 'mock-ble-printer-1', name: 'Mock BLE 打印机',
      addEventListener() {},
      gatt: {
        connected: false,
        async connect() { this.connected = true; return { async getPrimaryServices() { return [service] } } },
        disconnect() { this.connected = false },
      },
    }
    Object.defineProperty(navigator, 'bluetooth', {
      configurable: true,
      value: { async requestDevice(options) { window.__bleRequestOptions = options; return device } },
    })
  })
}
async function connectPrinter(scope) {
  await scope.getByRole('button', { name: '搜索 / 选择设备', exact: true }).click()
  await expect(scope.locator('.device-summary')).toContainText('Mock BLE 打印机')
  await scope.getByRole('button', { name: '连接', exact: true }).click()
  await expect(scope.getByRole('button', { name: '断开', exact: true })).toBeVisible()
  await expect(scope.locator('.device-summary')).toContainText('已连接设备')
}
async function writtenBytes(page) {
  return page.evaluate(() => Uint8Array.from(window.__bleWrites.flat()))
}

test('PrintingView Web Bluetooth 测试签真实 20 字节分包写入且不声称物理出纸', async ({ page }) => {
  let businessPrintReports = 0
  await installSession(page)
  await installBluetooth(page)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = pathOf(request)
    if (path === '/notifications') return json(route, pageOf([]))
    if (path === '/settings') return json(route, { default_print_mode: 'bluetooth', cloud_printer: null, print_label_style: { widthMm: 40, heightMm: 30 } })
    if (path === '/tickets/print') { businessPrintReports += 1; return json(route, { reported: 1, duplicated: [] }) }
    return json(route, { detail: `未配置 mock：${request.method()} ${path}` }, 500)
  })
  await page.goto('/web/#printing')
  const panel = page.locator('.ble-panel')
  await expect(panel).toContainText('TSPL2 测试签')
  await connectPrinter(panel)
  await panel.getByRole('button', { name: '测试打印', exact: true }).click()
  await expect(panel.locator('.print-notice')).toContainText('TSPL 测试指令已全部写入蓝牙通道')
  await expect(panel.locator('.print-notice')).toContainText('这不代表已物理出纸')
  await expect(panel.locator('.queue-item.sent')).toContainText('待现场检查')
  const chunks = await page.evaluate(() => window.__bleWrites)
  expect(chunks.length).toBeGreaterThan(5)
  expect(chunks.every((chunk) => chunk.length > 0 && chunk.length <= 20)).toBe(true)
  const command = new TextDecoder().decode(await writtenBytes(page))
  expect(command).toContain('SIZE 40 mm,30 mm')
  expect(command).toContain('WEB BLE TEST')
  expect(command).toContain('YISAO-WEB-BLE-TEST')
  expect(command).toContain('PRINT 1')
  expect(businessPrintReports).toBe(0)
})

test('TicketPrintDialog 中文真实菲票生成位图并经可写 GATT 分包发送', async ({ page }) => {
  test.setTimeout(90000)
  let businessPrintReports = 0
  await installSession(page)
  await installBluetooth(page)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = pathOf(request)
    if (path === '/notifications') return json(route, pageOf([]))
    if (path === '/productions' && request.method() === 'GET') return json(route, pageOf([production]))
    if (path === '/productions/7') return json(route, production)
    if (path === '/productions/7/bundles') return json(route, pageOf([ticket]))
    if (path === '/settings') return json(route, { print_label_style: { widthMm: 30, heightMm: 40, fields: ['styleNo', 'bedNo', 'bundleNo', 'color', 'size', 'qty', 'company'] } })
    if (path === '/tickets/print') { businessPrintReports += 1; return json(route, { reported: 1, duplicated: [] }) }
    return json(route, { detail: `未配置 mock：${request.method()} ${path}` }, 500)
  })
  await page.goto('/web/#production')
  await page.locator('.desktop-table tbody tr').filter({ hasText: 'MOCK-BED' }).click()
  const productionDialog = page.getByRole('dialog', { name: '床次 MOCK-BED' })
  await productionDialog.getByRole('button', { name: '打印菲票', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '菲票打印预览' })
  await expect(dialog.locator('.ticket-label')).toContainText('中国红 / 中码')
  const preview = dialog.getByAltText('中文菲票位图打印预览')
  await expect(preview).toBeVisible()
  expect(await preview.getAttribute('src')).toMatch(/^data:image\/png;base64,/)
  await connectPrinter(dialog.locator('.ble-panel'))
  await dialog.getByRole('button', { name: '通过蓝牙发送当前菲票', exact: true }).click()
  await expect(dialog.locator('.print-notice')).toContainText('中文菲票位图已全部写入蓝牙通道', { timeout: 30000 })
  await expect(dialog.locator('.print-notice')).toContainText('这不代表已物理出纸')
  const chunks = await page.evaluate(() => window.__bleWrites)
  expect(chunks.length).toBeGreaterThan(20)
  expect(chunks.every((chunk) => chunk.length > 0 && chunk.length <= 20)).toBe(true)
  const bytes = await writtenBytes(page)
  const head = new TextDecoder().decode(bytes.slice(0, 100))
  expect(head).toContain('SIZE 30 mm,40 mm')
  expect(head).toContain('BITMAP 0,0,')
  expect(head).not.toContain('CODEPAGE 936')
  const jobs = await page.evaluate(() => JSON.parse(localStorage.getItem('yisao.web.ble-print-jobs.v1.1')))
  expect(jobs[0]).toMatchObject({ kind: 'web-ble-ticket-bitmap', ticketId: 31, status: 'sent' })
  expect(jobs[0].label).toContain('中国红/中码')
  expect(businessPrintReports).toBe(0)
})

test('handover_timeout 通知标读后回跳交接中心', async ({ page }) => {
  let readBody
  await installSession(page)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = pathOf(request)
    if (path === '/notifications' && request.method() === 'GET') return json(route, pageOf([{ id: 41, code: 'handover_timeout', title: 'Mock 交接超时', message: 'MOCK-HO-501 已超时', is_read: 0, created_at: '2026-11-15T01:00:00Z' }]))
    if (path === '/notifications/41/read' && request.method() === 'POST') { readBody = request.postDataJSON(); return json(route, { id: 41, is_read: 1 }) }
    if (path === '/handovers/' && request.method() === 'GET') return json(route, pageOf([]))
    return json(route, { detail: `未配置 mock：${request.method()} ${path}` }, 500)
  })
  await page.goto('/web/#notifications')
  await page.getByRole('button', { name: /Mock 交接超时.*前往交接中心/ }).click()
  await expect(page).toHaveURL(/#handovers$/)
  await expect(page.locator('.welcome h2')).toHaveText('通用责任交接')
  expect(readBody).toEqual({})
})
