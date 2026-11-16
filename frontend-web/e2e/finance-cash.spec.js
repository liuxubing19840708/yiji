import { test, expect } from '@playwright/test'

const session = {
  access: 'finance-cash-mock-session',
  companyId: 1,
  companies: [{ id: 1, name: 'Mock 工厂', post: { name: '财务测试', permissions: ['finance.manage', 'finance.view', 'salary.view'] } }],
  user: { real_name: '测试员', phone: '00000000000' },
}

const supplier = { id: 7, code: 'SUP-MOCK', name: 'Mock 供应商' }
const purchase = { id: 11, order_no: 'PO-MOCK-001', supplier_name: supplier.name, items: [] }
const payable = {
  id: 101, payable_no: 'AP-MOCK-001', supplier: supplier.id, supplier_name: supplier.name,
  purchase: purchase.id, amount: '500.00', paid_amount: '200.00', outstanding: '300.00',
  status: 'partial', status_display: '部分付款', payable_at: '2026-11-01', due_at: '2026-11-30',
  remark: '', created_at: '2026-11-01T02:00:00Z',
}
const payment = {
  id: 201, payable: payable.id, payable_no: payable.payable_no, supplier: supplier.id,
  supplier_name: supplier.name, amount: '100.00', paid_at: '2026-11-10T03:00:00Z',
  method: 'bank', transaction_ref: 'BANK-MOCK-001', status: 'active', status_display: '有效',
  remark: '', voided_at: null, void_reason: '', created_at: '2026-11-10T03:00:00Z',
}
const entry = {
  id: 301, entry_no: 'CE-MOCK-001', direction: 'expense', direction_display: '费用支出',
  category: '水电费', amount: '80.00', occurred_at: '2026-11-09T04:00:00Z', counterparty: 'Mock 收款方',
  method: '银行转账', reference_no: 'BILL-MOCK-001', status: 'active', status_display: '有效',
  remark: '', voided_at: null, void_reason: '', created_at: '2026-11-09T04:00:00Z',
}

function pageOf(items) { return { count: items.length, next: null, previous: null, results: items } }
async function json(route, data, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
}
async function installMocks(page, handler) {
  await page.addInitScript((value) => localStorage.setItem('yisao.web.session.v1', JSON.stringify(value)), session)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace(/^\/api/, '')
    if (path === '/notifications') return json(route, pageOf([]))
    if (path === '/suppliers') return json(route, pageOf([supplier]))
    if (path === '/purchase-orders') return json(route, pageOf([purchase]))
    if (handler) return handler(route, request, url, path)
    return json(route, { detail: `未配置 mock：${request.method()} ${path}` }, 500)
  })
}

test('应付列表、创建、详情与重复提交保护', async ({ page }) => {
  const errors = []
  const posts = []
  const payables = [payable]
  page.on('pageerror', (error) => errors.push(error.message))
  await installMocks(page, async (route, request, url, path) => {
    if (path === '/finance/payables' && request.method() === 'GET') return json(route, pageOf(payables))
    if (path === '/finance/payables' && request.method() === 'POST') {
      posts.push(request.postDataJSON())
      const body = request.postDataJSON()
      const created = { ...payable, id: 102, payable_no: 'AP-MOCK-002', purchase: body.purchase, amount: body.amount, paid_amount: '0.00', outstanding: body.amount, status: 'unpaid', status_display: '未付', payable_at: body.payable_at, due_at: body.due_at }
      payables.unshift(created)
      await new Promise((resolve) => setTimeout(resolve, 120))
      return json(route, created, 201)
    }
    const detail = path.match(/^\/finance\/payables\/(\d+)$/)
    if (detail) return json(route, payables.find((item) => item.id === Number(detail[1])))
    return json(route, { detail: `unexpected ${path}` }, 500)
  })
  await page.goto('/web/#finance/payables')
  await expect(page.getByRole('table', { name: '采购应付列表' })).toContainText('AP-MOCK-001')
  await page.getByRole('button', { name: '新建应付', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '新建采购应付' })
  await dialog.getByLabel('供应商主档（可选）').selectOption(String(supplier.id))
  await dialog.getByLabel('关联采购单（可选）').selectOption(String(purchase.id))
  await dialog.getByLabel('应付金额（元）').fill('123.45')
  await dialog.getByLabel('应付日期', { exact: true }).fill('2026-11-11')
  await dialog.getByLabel('到期日期（可选）').fill('2026-12-11')
  await dialog.getByRole('button', { name: '建立应付', exact: true }).evaluate((button) => { button.click(); button.click() })
  await expect(page.getByRole('dialog', { name: /应付详情 AP-MOCK-002/ })).toBeVisible()
  await expect(page.getByRole('dialog', { name: /应付详情 AP-MOCK-002/ })).toContainText('¥123.45')
  expect(posts).toHaveLength(1)
  expect(posts[0]).toMatchObject({ supplier: 7, purchase: 11, amount: 123.45, payable_at: '2026-11-11', due_at: '2026-12-11' })
  expect(errors).toEqual([])
})

test('付款展示后端超额校验，成功后按原因作废', async ({ page }) => {
  const payments = [{ ...payment }]
  let voidBody = null
  await installMocks(page, async (route, request, url, path) => {
    if (path === '/finance/payables') return json(route, pageOf([payable]))
    if (path === '/finance/supplier-payments' && request.method() === 'GET') return json(route, pageOf(payments))
    if (path === '/finance/supplier-payments' && request.method() === 'POST') {
      const body = request.postDataJSON()
      if (Number(body.amount) > Number(payable.outstanding)) return json(route, { amount: ['超额付款：应付 500.00 元，已付 200.00 元，本笔 400.00 元'] }, 400)
      return json(route, { ...payment, amount: body.amount }, 201)
    }
    if (path === `/finance/supplier-payments/${payment.id}/void`) {
      voidBody = request.postDataJSON()
      payments[0] = { ...payments[0], status: 'voided', status_display: '已作废', void_reason: voidBody.reason, voided_at: '2026-11-11T05:00:00Z' }
      return json(route, payments[0])
    }
    return json(route, { detail: `unexpected ${path}` }, 500)
  })
  await page.goto('/web/#finance/payments')
  await expect(page.getByRole('table', { name: '供应商付款列表' })).toContainText('BANK-MOCK-001')
  await page.getByRole('button', { name: '登记付款', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '登记供应商付款' })
  await dialog.getByLabel('关联应付单（可选）').selectOption(String(payable.id))
  await expect(dialog.locator('.selected-payable')).toContainText(supplier.name)
  await dialog.getByLabel('付款金额（元）').fill('400')
  await dialog.getByRole('button', { name: '确认付款', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('超额付款')
  await dialog.getByLabel('付款金额（元）').fill('100')
  await dialog.getByRole('button', { name: '确认付款', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await page.getByRole('button', { name: /作废 Mock 供应商 付款 100.00 元/ }).click()
  const voidDialog = page.getByRole('dialog', { name: '作废供应商付款' })
  await expect(voidDialog.getByRole('button', { name: '确认作废' })).toBeDisabled()
  await voidDialog.getByLabel('作废原因（必填）').fill('重复付款，银行已退回')
  await voidDialog.getByRole('button', { name: '确认作废' }).click()
  await expect(page.getByRole('table', { name: '供应商付款列表' })).toContainText('重复付款，银行已退回')
  expect(voidBody).toEqual({ reason: '重复付款，银行已退回' })
})

test('费用与收入筛选、登记、空态及作废', async ({ page }) => {
  const entries = [{ ...entry }]
  let lastQuery = null
  await installMocks(page, async (route, request, url, path) => {
    if (path === '/finance/cash-entries' && request.method() === 'GET') {
      lastQuery = Object.fromEntries(url.searchParams)
      const direction = url.searchParams.get('direction')
      return json(route, pageOf(direction ? entries.filter((item) => item.direction === direction) : entries))
    }
    if (path === '/finance/cash-entries' && request.method() === 'POST') {
      const body = request.postDataJSON()
      const created = { ...entry, id: 302, entry_no: 'CE-MOCK-002', ...body, direction_display: '其他收入', status: 'active', status_display: '有效' }
      entries.unshift(created)
      return json(route, created, 201)
    }
    if (path === `/finance/cash-entries/${entry.id}/void`) {
      const index = entries.findIndex((item) => item.id === entry.id)
      entries[index] = { ...entries[index], status: 'voided', status_display: '已作废', void_reason: request.postDataJSON().reason }
      return json(route, entries[index])
    }
    return json(route, { detail: `unexpected ${path}` }, 500)
  })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/web/#finance/entries')
  await expect(page.locator('.cash-mobile-list')).toContainText('水电费')
  await expect(page.locator('.cash-mobile-list')).toBeVisible()
  await page.getByLabel('收支方向').selectOption('income')
  await expect(page.getByText('当前条件下暂无费用支出或其他收入')).toBeVisible()
  expect(lastQuery.direction).toBe('income')
  await page.getByRole('button', { name: '登记其他收入', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '登记其他收入' })
  await dialog.getByLabel('类别', { exact: true }).fill('利息收入')
  await dialog.getByLabel('金额（元）').fill('25.50')
  await dialog.getByLabel('收支方式（可选）').fill('银行转账')
  await dialog.getByRole('button', { name: '确认登记', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await page.getByLabel('收支方向').selectOption('expense')
  await expect(page.locator('.cash-mobile-list')).toContainText('水电费')
  await page.locator('.cash-mobile-card').filter({ hasText: 'CE-MOCK-001' }).getByRole('button', { name: '作废记录' }).click()
  await page.getByLabel('作废原因（必填）').fill('凭证录入重复')
  await page.getByRole('button', { name: '确认作废' }).click()
  await expect(page.locator('.cash-mobile-card').filter({ hasText: 'CE-MOCK-001' })).toContainText('凭证录入重复')
})

test('现金流按日按月查询，明确非利润表并展示权限错误', async ({ page }) => {
  const requests = []
  let deny = false
  const totals = { customer_receipts: '1000.00', other_income: '50.00', salary_payments: '300.00', supplier_payments: '200.00', expenses: '100.00', income: '1050.00', outflow: '600.00', net: '450.00' }
  await installMocks(page, async (route, request, url, path) => {
    if (path === '/finance/cash-flow') {
      if (deny) return json(route, { detail: '缺少 salary.view 权限，不能查看含工资的经营现金流' }, 403)
      const params = Object.fromEntries(url.searchParams)
      requests.push(params)
      return json(route, { ...params, rows: [{ period: params.group_by === 'day' ? '2026-11-01' : '2026-11-01', ...totals }], totals, note: '经营现金流按实际业务时间汇总；作废流水不计。本报表不是利润表。' })
    }
    return json(route, { detail: `unexpected ${path}` }, 500)
  })
  await page.goto('/web/#finance/cashflow')
  await expect(page.getByRole('note')).toContainText('经营现金流不是利润表')
  await expect(page.getByRole('table', { name: '经营现金流按月明细' })).toContainText('¥450.00')
  await page.getByLabel('开始日期', { exact: true }).fill('2026-11-01')
  await page.getByLabel('结束日期', { exact: true }).fill('2026-11-11')
  await page.getByLabel('现金流汇总周期').selectOption('day')
  await page.getByRole('button', { name: '查询现金流', exact: true }).click()
  await expect(page.getByRole('table', { name: '经营现金流按日明细' })).toBeVisible()
  expect(requests.at(-1)).toMatchObject({ date_from: '2026-11-01', date_to: '2026-11-11', group_by: 'day' })
  deny = true
  await page.getByLabel('现金流汇总周期').selectOption('month')
  await page.getByRole('button', { name: '查询现金流', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('缺少 salary.view 权限')
  await expect(page.getByRole('note')).toContainText('本报表不是利润表')
})
