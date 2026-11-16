import { test, expect } from '@playwright/test'

const readOnlySession = {
  access: 'read-only-visibility',
  companyId: 1,
  user: { id: 1, real_name: '只读员工' },
  companies: [{ id: 1, name: '只读工厂', post: { name: '只读岗', effective_perms: ['finance.view', 'shipment.view', 'warehouse.view', 'production.view'] } }],
}
const emptyPage = { count: 0, next: null, previous: null, results: [] }

async function install(page) {
  await page.addInitScript((value) => localStorage.setItem('yisao.web.session.v1', JSON.stringify(value)), readOnlySession)
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace(/^\/api/, '')
    const body = path === '/notifications' ? { count: 0, results: [] } : emptyPage
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })
}

test('只读岗位隐藏资金、出货、仓库和生产写入口', async ({ page }) => {
  await install(page)

  await page.goto('/web/#finance/payables')
  await expect(page.getByRole('button', { name: '新建应付', exact: true })).toHaveCount(0)
  await page.goto('/web/#finance/payments')
  await expect(page.getByRole('button', { name: '登记付款', exact: true })).toHaveCount(0)
  await page.goto('/web/#finance/entries')
  await expect(page.getByRole('button', { name: '登记费用支出', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '登记其他收入', exact: true })).toHaveCount(0)

  await page.goto('/web/#shipments')
  await expect(page.getByRole('button', { name: '新建出货单', exact: true })).toHaveCount(0)

  await page.goto('/web/#warehouse/purchases')
  await expect(page.getByRole('button', { name: '新增采购单', exact: true })).toHaveCount(0)
  await page.goto('/web/#warehouse/receipts')
  await expect(page.getByRole('button', { name: '新建暂收', exact: true })).toHaveCount(0)
  await page.goto('/web/#warehouse/stockIns')
  await expect(page.getByRole('button', { name: '新增入库单', exact: true })).toHaveCount(0)
  await page.goto('/web/#warehouse/issues')
  await expect(page.getByRole('button', { name: '＋ 新建领料', exact: true })).toHaveCount(0)

  await page.goto('/web/#production')
  await expect(page.getByRole('button', { name: '新建床次', exact: true })).toHaveCount(0)
})
