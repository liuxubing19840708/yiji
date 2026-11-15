import { test, expect } from '@playwright/test'

const scenarios = [
  ['应收', 'finance/receivables', '/api/finance/receivables'],
  ['款式', 'master/styles', '/api/styles'],
  ['报价', 'quotations', '/api/quotation/quotations'],
  ['质检', 'quality', '/api/quality/records'],
  ['外发', 'outsource', '/api/outsource/orders'],
  ['订单成本', 'costs/costs', '/api/cost/orders'],
  ['订单损益', 'costs/pnl', '/api/cost/orders/pnl'],
  ['操作日志', 'settings/audit', '/api/audit-logs'],
  ['客户端日志', 'settings/client', '/api/client-logs'],
]

function row(id) {
  return {
    id,
    receivable_no: `AR-${id}`,
    quote_no: `Q-${id}`,
    no: `OS-${id}`,
    order_no: `ORDER-${id}`,
    customer_name: '隔离测试客户',
    style_no: 'STYLE-01',
    style_name: '测试款',
    amount: '100.00',
    received_amount: '0.00',
    outstanding: '100.00',
    total_price: '200.00',
    material_total: '50.00',
    profit_rate: '20.00',
    quote_date: '2026-11-08',
    status: 'settled',
    status_display: '已结清',
    order_status: 'done',
    ticket_bed_no: `BED-${id}`,
    ticket_bundle_no: 1,
    ticket_color: '黑',
    ticket_size: 'L',
    ticket_qty: 10,
    defect_qty: 1,
    received_qty: 1,
    inspected_at: '2026-11-08T08:00:00Z',
    created_at: '2026-11-08T08:00:00Z',
    computed_at: '2026-11-08T08:00:00Z',
    send_qty: 10,
    send_date: '2026-11-08',
    unit_price: '2.00',
    supplier_name: '隔离外协厂',
    message: `隔离日志 ${id}`,
    type_display: '测试类型',
    level: 'info',
    qty: 100,
    output_qty: 100,
    revenue: '1000.00',
    material_cost: '100.00',
    labor_cost: '100.00',
    outsourcing_cost: '0.00',
    overhead_cost: '0.00',
    total_cost: '200.00',
    unit_cost: '2.00',
    gross_profit: '800.00',
    gross_margin: '80.00',
    ar_amount: '1000.00',
    ar_received: '0.00',
    ar_gap: '1000.00',
    real_profit: '-200.00',
  }
}

async function mockPage(page, target, requests, failSecondPageOnce = true) {
  let failed = false
  await page.addInitScript(() => {
    localStorage.setItem('yisao.web.session.v1', JSON.stringify({
      access: 'pagination-test',
      companyId: 1,
      user: { id: 1, real_name: '分页测试员' },
      companies: [{ id: 1, name: '隔离工厂', status: 1, company_no: '100001', post: { name: '管理员', is_admin: true, permissions: [] } }],
    }))
  })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname !== target) return route.fulfill({ json: { count: 0, results: [] } })
    const pageNumber = Number(url.searchParams.get('page') || 1)
    requests.push(pageNumber)
    if (pageNumber === 2 && failSecondPageOnce && !failed) {
      failed = true
      return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: '第二页暂时失败' }) })
    }
    const start = (pageNumber - 1) * 20 + 1
    const end = Math.min(21, start + 19)
    const results = Array.from({ length: Math.max(0, end - start + 1) }, (_, index) => row(start + index))
    return route.fulfill({ json: { count: 21, results } })
  })
}

for (const [label, hash, target] of scenarios) {
  test(`${label}手机加载更多保留首屏、失败回滚页码并可重试`, async ({ page }) => {
    const requests = []
    await page.setViewportSize({ width: 390, height: 844 })
    await mockPage(page, target, requests)
    await page.goto(`/web/#${hash}`)
    const cards = page.locator('.mobile-cards .mobile-row')
    await expect(cards).toHaveCount(20)
    await expect(page.locator('.desktop-pagination')).toBeHidden()
    const more = page.locator('.mobile-load-more')
    await more.getByRole('button', { name: '加载更多', exact: true }).click()
    await expect(more).toContainText('第二页暂时失败')
    await expect(cards).toHaveCount(20)
    expect(requests).toEqual([1, 2])
    await more.getByRole('button', { name: '重试', exact: true }).click()
    await expect(cards).toHaveCount(21)
    await expect(more).toHaveText('没有更多了')
    expect(requests).toEqual([1, 2, 2])
  })
}

test('桌面应收继续使用分页器替换当前页', async ({ page }) => {
  const requests = []
  await page.setViewportSize({ width: 1440, height: 900 })
  await mockPage(page, '/api/finance/receivables', requests, false)
  await page.goto('/web/#finance/receivables')
  await expect(page.locator('.desktop-table tbody tr')).toHaveCount(20)
  await expect(page.locator('.desktop-table')).toContainText('AR-1')
  await expect(page.locator('.mobile-load-more')).toBeHidden()
  await page.locator('.desktop-pagination').getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.locator('.desktop-table tbody tr')).toHaveCount(1)
  await expect(page.locator('.desktop-table')).toContainText('AR-21')
  await expect(page.locator('.desktop-table').getByText('AR-1', { exact: true })).toHaveCount(0)
  expect(requests).toEqual([1, 2])
})
