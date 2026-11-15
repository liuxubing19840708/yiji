import { test, expect } from '@playwright/test'

const session = {
  access: 'salary-payment-fixture',
  companyId: 9,
  user: { id: 1, real_name: '薪酬管理员' },
  companies: [{ id: 9, name: '模拟公司', post: { name: '管理员' } }],
}
const employees = [{ user_id: 7, real_name: '测试员工', post_name: '车缝', status: 1, status_display: '在职' }]
const payment = {
  id: 51, company: 9, employee: 7, employee_name: '测试员工', paid_at: '2026-11-10T09:30:00+08:00',
  method: 'bank', transaction_ref: 'BANK-EXISTING-51', note: '已有支付备注', operator: 1, operator_name: '薪酬管理员',
  amount: '20.00', status: 'active', status_display: '有效', void_reason: '', voided_by_name: '', voided_at: null,
  created_at: '2026-11-10T09:31:00+08:00',
  allocations: [{ id: 1, settlement: 91, amount: '20.00', settlement_amount: '100.00', paid_amount: '20.00', outstanding: '80.00' }],
}
const settlement = {
  id: 91, company: 9, user: 7, user_name: '测试员工', period_start: '2026-11-01', period_end: '2026-11-10',
  employee_count: 1, amount: '100.00', wage_detail: { 7: '100.00' }, status: 'settled', status_display: '已结算', created_by_name: '薪酬管理员',
}

async function installApi(page, options = {}) {
  const state = { payments: [structuredClone(payment)], createRequests: [], voidRequests: [], failList: options.failList || false }
  await page.addInitScript((value) => localStorage.setItem('yisao.web.session.v1', JSON.stringify(value)), session)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace(/^\/api/, '')
    const respond = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/notifications') return respond({ count: 0, results: [] })
    if (path === '/salary/company/employees') return respond(employees)
    if (path === '/salary/settlements') return respond({ count: 1, next: null, previous: null, results: [settlement] })
    if (path === '/salary/payments' && request.method() === 'GET') {
      if (state.failList) return respond({ detail: '没有实付工资查看权限' }, 403)
      let result = state.payments
      if (url.searchParams.get('employee_id')) result = result.filter((row) => String(row.employee) === url.searchParams.get('employee_id'))
      if (url.searchParams.get('status')) result = result.filter((row) => row.status === url.searchParams.get('status'))
      return respond({ count: result.length, next: null, previous: null, results: result })
    }
    if (path === '/salary/payments' && request.method() === 'POST') {
      const body = request.postDataJSON()
      state.createRequests.push(body)
      if (options.delayCreate) await new Promise((resolve) => setTimeout(resolve, 350))
      const created = {
        ...payment, id: 50 + state.createRequests.length, employee: body.employee_id, paid_at: body.paid_at,
        method: body.method, transaction_ref: body.transaction_ref, note: body.note, amount: body.amount,
        allocations: body.allocations.map((item, index) => ({ id: 20 + index, settlement: item.settlement_id, amount: item.amount, settlement_amount: '100.00', paid_amount: '100.00', outstanding: '0.00' })),
      }
      state.payments.unshift(created)
      return respond(created, 201)
    }
    const detailMatch = path.match(/^\/salary\/payments\/(\d+)$/)
    if (detailMatch) {
      const found = state.payments.find((row) => row.id === Number(detailMatch[1]))
      return found ? respond(found) : respond({ detail: '支付记录不存在' }, 404)
    }
    const voidMatch = path.match(/^\/salary\/payments\/(\d+)\/void$/)
    if (voidMatch && request.method() === 'POST') {
      const found = state.payments.find((row) => row.id === Number(voidMatch[1]))
      state.voidRequests.push(request.postDataJSON())
      Object.assign(found, { status: 'void', status_display: '已作废', void_reason: request.postDataJSON().reason, voided_by_name: '薪酬管理员', voided_at: '2026-11-11T10:00:00+08:00' })
      return respond(found)
    }
    return respond({ detail: `未配置夹具：${request.method()} ${path}` }, 500)
  })
  return state
}

test('支付列表筛选、真实结算分摊创建、重复提交防护与详情', async ({ page }) => {
  page.on('pageerror', (error) => console.error('PAGE ERROR:', error.message))
  page.on('console', (message) => { if (message.type() === 'error') console.error('CONSOLE:', message.text()) })
  const state = await installApi(page, { delayCreate: true })
  await page.goto('/web/#salary/payments')
  await expect(page.getByRole('heading', { name: '薪酬核算', exact: true })).toBeVisible()
  await expect(page.locator('.payment-desktop tbody tr')).toHaveCount(1)
  await page.getByLabel('筛选员工').selectOption('7')
  await page.getByRole('combobox', { name: '支付状态', exact: true }).selectOption('void')
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  await expect(page.getByText('当前条件下暂无支付记录', { exact: true })).toBeVisible()
  await page.getByRole('combobox', { name: '支付状态', exact: true }).selectOption('active')
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  await expect(page.locator('.payment-desktop tbody tr')).toHaveCount(1)

  await page.getByRole('button', { name: '登记实付', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '登记实付工资' })
  await dialog.getByRole('combobox', { name: '员工', exact: true }).selectOption('7')
  await dialog.getByLabel('实付金额（元）').fill('79.99')
  await dialog.getByLabel('外部流水号', { exact: true }).fill('BANK-NEW-100')
  await dialog.getByRole('combobox', { name: '支付方式', exact: true }).selectOption('bank')
  await dialog.getByLabel('备注', { exact: true }).fill('本次银行实付')
  await dialog.getByLabel('分摊到已有结算单').check()
  await expect(dialog.getByRole('combobox', { name: '选择可支付结算单', exact: true })).toContainText('可付 ¥80.00')
  await dialog.getByRole('combobox', { name: '选择可支付结算单', exact: true }).selectOption('91')
  await dialog.getByRole('button', { name: '添加分摊', exact: true }).click()
  await expect(dialog.getByText('分摊金额合计必须等于实付金额', { exact: true })).toHaveCount(0)
  await dialog.getByRole('button', { name: '确认登记实付', exact: true }).click()
  await expect(dialog.getByRole('alert')).toHaveText('分摊金额合计必须等于实付金额')
  expect(state.createRequests).toHaveLength(0)
  await dialog.getByLabel('实付金额（元）').fill('80.00')
  const createResponse = page.waitForResponse((response) => response.url().endsWith('/api/salary/payments') && response.request().method() === 'POST')
  await dialog.getByRole('button', { name: '确认登记实付', exact: true }).click()
  await expect(dialog.getByRole('button', { name: '正在提交…', exact: true })).toBeDisabled()
  await dialog.getByRole('button', { name: '正在提交…', exact: true }).click({ force: true })
  await createResponse
  await expect(dialog).toHaveCount(0)
  expect(state.createRequests).toHaveLength(1)
  expect(state.createRequests[0]).toMatchObject({ employee_id: 7, amount: '80.00', transaction_ref: 'BANK-NEW-100', allocations: [{ settlement_id: 91, amount: '80.00' }] })
  await expect(page.locator('.payment-desktop tbody tr')).toHaveCount(2)

  await page.locator('.payment-desktop tbody tr').first().getByRole('button', { name: '详情', exact: true }).click()
  const detail = page.getByRole('dialog', { name: '支付详情' })
  await expect(detail).toContainText('BANK-NEW-100')
  await expect(detail).toContainText('结算单 #91')
  await expect(detail.getByRole('button', { name: '编辑', exact: true })).toHaveCount(0)
  await expect(detail.getByRole('button', { name: '删除', exact: true })).toHaveCount(0)
})

test('明确创建无结算支付，并通过必填原因作废而不删除记录', async ({ page }) => {
  const state = await installApi(page)
  await page.goto('/web/#salary/payments')
  await page.getByRole('button', { name: '登记实付', exact: true }).click()
  const create = page.getByRole('dialog', { name: '登记实付工资' })
  await create.getByRole('combobox', { name: '员工', exact: true }).selectOption('7')
  await create.getByLabel('实付金额（元）').fill('35.50')
  await create.getByLabel('外部流水号', { exact: true }).fill('CASH-NO-SETTLEMENT-1')
  await create.getByRole('combobox', { name: '支付方式', exact: true }).selectOption('cash')
  await create.getByLabel('无结算支付（不抵扣任何结算单）').check()
  await create.getByRole('button', { name: '确认登记实付', exact: true }).click()
  await expect(create).toHaveCount(0)
  expect(state.createRequests[0].allocations).toEqual([])

  const row = page.locator('.payment-desktop tbody tr').filter({ hasText: 'CASH-NO-SETTLEMENT-1' })
  await row.getByRole('button', { name: '作废', exact: true }).click()
  const voidDialog = page.getByRole('dialog', { name: '作废支付' })
  await voidDialog.getByRole('button', { name: '确认作废', exact: true }).click()
  expect(state.voidRequests).toHaveLength(0)
  await voidDialog.getByLabel('作废原因', { exact: true }).fill('银行退回，重新付款')
  await voidDialog.getByRole('button', { name: '确认作废', exact: true }).click()
  await expect(voidDialog).toHaveCount(0)
  expect(state.voidRequests).toEqual([{ reason: '银行退回，重新付款' }])
  await expect(row).toContainText('已作废')
  await expect(row.getByRole('button', { name: '详情', exact: true })).toBeVisible()
  await row.getByRole('button', { name: '详情', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '支付详情' })).toContainText('银行退回，重新付款')
})

test('移动端支付卡片与无权限、空态不依赖真实服务', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 })
  const state = await installApi(page)
  await page.goto('/web/#salary/payments')
  await expect(page.locator('.payment-card')).toHaveCount(1)
  await expect(page.locator('.payment-desktop')).toBeHidden()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  await page.getByRole('combobox', { name: '支付状态', exact: true }).selectOption('void')
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  await expect(page.getByText('当前条件下暂无支付记录', { exact: true })).toBeVisible()
  expect(state.createRequests).toHaveLength(0)

  await page.unroute('**/api/**')
  await installApi(page, { failList: true })
  await page.reload()
  await expect(page.getByRole('alert')).toContainText('没有实付工资查看权限')
})
