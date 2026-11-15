import { test, expect } from '@playwright/test'

const phone = process.env.WEB_PHONE
const password = process.env.WEB_PASSWORD

test.beforeEach(async () => {
  test.skip(!phone || !password, '设置 WEB_PHONE/WEB_PASSWORD 后运行隔离环境复杂写链路测试')
})

async function login(page, route) {
  await page.goto(`/web/#${route}`)
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.topbar h1')).not.toHaveText('登录')
}

async function call(page, method, path, body) {
  return page.evaluate(async ({ method, path, body }) => {
    const session = JSON.parse(localStorage.getItem('yisao.web.session.v1'))
    const response = await fetch(`/api${path}`, {
      method,
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${session.access}`,
        'X-Company-Id': String(session.companyId),
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const text = await response.text()
    let data = null
    try { data = text ? JSON.parse(text) : null } catch { data = text }
    return { status: response.status, data }
  }, { method, path, body })
}

function expectStatus(result, status) {
  expect(result.status, JSON.stringify(result.data)).toBe(status)
  return result.data
}

async function createWork(page, suffix, price = '1.25', reuseProcess = false) {
  const process = reuseProcess
    ? expectStatus(await call(page, 'GET', '/processes?page_size=1'), 200).results[0]
    : expectStatus(await call(page, 'POST', '/processes', {
      name: `Web复杂链路工序${suffix}`, default_price: price,
      is_fixed: false, is_key: false, hide_price: false, sort: 1,
    }), 201)
  const customers = expectStatus(await call(page, 'GET', '/customers?page_size=1'), 200)
  const style = expectStatus(await call(page, 'POST', '/styles', {
    style_no: `WEB-CX-S${suffix}`, name: 'Web 复杂写链路测试款',
    customer: customers.results[0].id, fabric: '测试面料', remark: '', craft_note: '',
    images: [], craft_images: [], video: '',
    process_items: [{ process_id: process.id, price, sort: 1, allow_skip: true }],
  }), 201)
  const production = expectStatus(await call(page, 'POST', '/productions', {
    bed_no: `WEB-CX-B${suffix}`, style: style.id, customer: customers.results[0].id,
    order_type: '自产', fabric: '测试面料', source_order_no: '', plan_qty: 8,
    bom_bypass: true, bypass_reason: 'Web 可丢弃隔离副本复杂写链路测试',
  }), 201)
  const tickets = expectStatus(await call(page, 'POST', '/tickets/batch', {
    production_id: production.id, bundle_limit: 5,
    items: [{ color: '测试蓝', size: 'M', qty: 3 }, { color: '测试蓝', size: 'L', qty: 5 }],
  }), 201)
  expect(tickets).toHaveLength(2)
  return { process, style, production, tickets }
}

test('批量登记逐扎部分成功，成功扎保留且失败扎显示超限原因', async ({ page }) => {
  test.setTimeout(90000)
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('dialog', (dialog) => dialog.accept())
  await page.setViewportSize({ width: 1440, height: 1000 })
  await login(page, 'batch')
  const suffix = Date.now().toString().slice(-9)
  const work = await createWork(page, suffix, '0.80', true)
  const seeded = expectStatus(await call(page, 'POST', '/records', {
    ticket_id: work.tickets[1].id, process_id: work.process.id, qty: 5,
  }), 201)

  // 床次是在页面初始化后通过 API 准备的，刷新让批量登记重新拉取下拉选项。
  await page.reload()
  await expect(page.locator('.welcome h2')).toHaveText('批量协助登记')
  await page.getByLabel('生产床次').selectOption(String(work.production.id))
  const processSelect = page.getByLabel('登记工序')
  await expect(processSelect.locator(`option[value="${work.process.id}"]`)).toBeAttached()
  await processSelect.selectOption(String(work.process.id))
  const ticketTable = page.locator('.batch-ticket-card')
  await expect(ticketTable.locator('.batch-ticket-row')).toHaveCount(2)
  const firstQty = ticketTable.locator(`input[data-ticket-id="${work.tickets[0].id}"]`)
  const secondQty = ticketTable.locator(`input[data-ticket-id="${work.tickets[1].id}"]`)
  await ticketTable.getByRole('button', { name: '一键填剩余', exact: true }).click()
  await expect(firstQty).toHaveValue('3')
  await expect(secondQty).toHaveValue('0')
  // 第二扎已登记 5 件，仍填 5 件以验证后端部分成功及超限原因；entries 必须携带逐扎 qty。
  await secondQty.fill('5')
  const [response] = await Promise.all([
    page.waitForResponse((item) => item.url().endsWith('/api/records/batch-register') && item.request().method() === 'POST'),
    page.getByRole('button', { name: '提交批量登记（已填 2 扎）', exact: true }).click(),
  ])
  expect(response.status()).toBe(200)
  const requestEntries = response.request().postDataJSON().entries
  expect(requestEntries).toHaveLength(2)
  expect(requestEntries).toEqual(expect.arrayContaining([
    { ticket_id: work.tickets[0].id, qty: 3 },
    { ticket_id: work.tickets[1].id, qty: 5 },
  ]))
  const result = await response.json()
  expect(result.total).toBe(2)
  expect(result.ok).toBe(1)
  expect(result.fail).toBe(1)
  const success = result.results.find((item) => item.ticket_id === work.tickets[0].id)
  const failure = result.results.find((item) => item.ticket_id === work.tickets[1].id)
  expect(success.ok).toBe(true)
  expect(Number(success.qty)).toBe(3)
  expect(Number(success.amount)).toBe(2.4)
  expect(failure.ok).toBe(false)
  expect(failure.error).toContain('登记数量超限')
  expect(failure.error).toContain('已登记 5 件')

  const resultTable = page.locator('.batch-result-card').filter({ has: page.getByRole('heading', { name: '登记结果：成功 1 扎 · 失败 1 扎', exact: true }) })
  await expect(resultTable.locator('tbody tr')).toHaveCount(2)
  await expect(resultTable.locator('tbody tr').filter({ has: page.getByRole('cell', { name: String(work.tickets[0].bundle_no), exact: true }) })).toContainText('3 / 2.40')
  await expect(resultTable.locator('tbody tr').filter({ has: page.getByRole('cell', { name: String(work.tickets[1].bundle_no), exact: true }) })).toContainText('登记数量超限')
  const successRecords = expectStatus(await call(page, 'GET', `/records?ticket_id=${work.tickets[0].id}`), 200)
  const seededRecords = expectStatus(await call(page, 'GET', `/records?ticket_id=${work.tickets[1].id}`), 200)
  expect(successRecords.results).toHaveLength(1)
  expect(seededRecords.results).toHaveLength(1)
  expect(successRecords.results[0].id).toBe(success.record_id)
  expect(successRecords.results[0].qty).toBe(3)
  expect(seededRecords.results[0].id).toBe(seeded.id)
  expect(seededRecords.results[0].qty).toBe(5)
  expect(errors).toEqual([])
})

test('工资申请→审核→发放→结算单→薪资条保留计件明细', async ({ page }) => {
  test.setTimeout(90000)
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('dialog', (dialog) => dialog.accept())
  await page.setViewportSize({ width: 1440, height: 1000 })
  await login(page, 'salary')
  const uid = await page.evaluate(() => JSON.parse(localStorage.getItem('yisao.web.session.v1')).user.id)
  expectStatus(await call(page, 'PUT', `/salary/employee/${uid}/config`, { salary_type: 'process' }), 200)
  const suffix = Date.now().toString().slice(-9)
  const note = `Web复杂工资链路${suffix}`
  const work = await createWork(page, suffix, '1.25')
  const record = expectStatus(await call(page, 'POST', '/records', {
    ticket_id: work.tickets[0].id, process_id: work.process.id, qty: 3,
  }), 201)
  expect(Number(record.price)).toBe(1.25)

  await expect(page.locator('.welcome h2')).toHaveText('薪酬核算')
  const periodStart = await page.getByLabel('开始日期', { exact: true }).inputValue()
  const periodEnd = await page.getByLabel('结束日期', { exact: true }).inputValue()
  await page.getByRole('button', { name: '申请结算', exact: true }).click()
  const applyDialog = page.locator('.modal').filter({ has: page.getByRole('heading', { name: '申请结算', exact: true }) })
  await applyDialog.getByLabel('申请备注').fill(note)
  const [applyResponse] = await Promise.all([
    page.waitForResponse((item) => item.url().endsWith('/api/salary/settle-applies') && item.request().method() === 'POST'),
    applyDialog.getByRole('button', { name: '确认提交', exact: true }).click(),
  ])
  expect(applyResponse.status(), await applyResponse.text()).toBe(201)
  const apply = await applyResponse.json()
  expect(apply.user).toBe(uid)
  expect(apply.status).toBe(0)
  expect(Number(apply.amount)).toBeGreaterThanOrEqual(3.75)

  await page.getByRole('button', { name: '结算申请', exact: true }).click()
  const applyRow = page.locator('.desktop-table tbody tr, .table-card tbody tr').filter({ hasText: note })
  await expect(applyRow).toHaveCount(1)
  await expect(applyRow).toContainText('待审核')
  const [reviewResponse] = await Promise.all([
    page.waitForResponse((item) => item.url().endsWith(`/api/salary/settle-applies/${apply.id}/review`) && item.request().method() === 'PUT'),
    applyRow.getByRole('button', { name: '通过', exact: true }).click(),
  ])
  expect(reviewResponse.status()).toBe(200)
  expect((await reviewResponse.json()).status).toBe(1)
  await expect(applyRow).toContainText('待发放')
  const [payResponse] = await Promise.all([
    page.waitForResponse((item) => item.url().endsWith(`/api/salary/settle-applies/${apply.id}/pay`) && item.request().method() === 'PUT'),
    applyRow.getByRole('button', { name: '发放', exact: true }).click(),
  ])
  expect(payResponse.status(), await payResponse.text()).toBe(200)
  const paid = await payResponse.json()
  expect(paid.status).toBe(2)
  expect(paid.paid_at).toBeTruthy()
  await expect(applyRow).toContainText('已发放')
  await expect(applyRow.getByRole('button', { name: '发放', exact: true })).toHaveCount(0)

  const settlements = expectStatus(await call(page, 'GET', '/salary/settlements?page_size=100'), 200)
  const settlement = settlements.results.find((item) => item.user === uid && item.period_start === periodStart && item.period_end === periodEnd && Number(item.amount) === Number(paid.amount))
  expect(settlement, JSON.stringify(settlements.results)).toBeTruthy()
  expect(settlement.employee_count).toBe(1)
  expect(settlement.status).toBe('settled')
  const claimed = expectStatus(await call(page, 'GET', `/records?ticket_id=${work.tickets[0].id}`), 200)
  expect(claimed.results[0].settlement).toBe(settlement.id)

  await page.getByRole('button', { name: '结算单', exact: true }).click()
  const settlementRow = page.locator('.table-card tbody tr').filter({ hasText: `#${settlement.id}` })
  await expect(settlementRow).toHaveCount(1)
  await settlementRow.getByRole('button', { name: '薪资条', exact: true }).click()
  const payslipDialog = page.locator('.modal').filter({ has: page.getByRole('heading', { name: '薪资条', exact: true }) })
  await expect(payslipDialog.locator('.spinner')).toHaveCount(0)
  await expect(payslipDialog).toContainText(`${periodStart} 至 ${periodEnd}`)
  const payslipRow = payslipDialog.locator('tbody tr').filter({ hasText: work.process.name })
  await expect(payslipRow).toHaveCount(1)
  await expect(payslipRow).toContainText(`${work.process.name} 3 件 / ¥3.75`)
  expect(errors).toEqual([])
})

test('采购单创建→草稿单头编辑→确认完成→终态页面与接口均只读', async ({ page }) => {
  test.setTimeout(90000)
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('dialog', (dialog) => dialog.accept())
  await page.setViewportSize({ width: 1440, height: 1000 })
  await login(page, 'warehouse')
  const suffix = Date.now().toString().slice(-9)
  const supplier = `Web隔离供应商${suffix}`
  await expect(page.locator('.welcome h2')).toHaveText('仓库与追溯')
  await page.getByRole('button', { name: '采购单', exact: true }).click()
  await page.getByRole('button', { name: '新增采购单', exact: true }).click()
  const editor = page.getByRole('dialog', { name: '采购单', exact: true })
  await editor.getByLabel('供应商名称（合同为文本字段）').fill(supplier)
  await editor.getByLabel('备注', { exact: true }).fill(`采购草稿${suffix}`)
  await editor.getByLabel('面料 / 物料名称 *').fill('Web 隔离针织面料')
  await editor.getByLabel('数量 *').fill('12.5')
  await editor.getByLabel('单价', { exact: true }).fill('8.20')
  const [createResponse] = await Promise.all([
    page.waitForResponse((item) => item.url().endsWith('/api/purchase-orders') && item.request().method() === 'POST'),
    editor.getByRole('button', { name: '创建采购单', exact: true }).click(),
  ])
  expect(createResponse.status(), await createResponse.text()).toBe(201)
  const purchase = await createResponse.json()
  expect(purchase.status).toBe('draft')
  expect(purchase.items).toHaveLength(1)
  expect(Number(purchase.items[0].qty)).toBe(12.5)
  expect(Number(purchase.total_qty)).toBe(12.5)

  const purchaseRow = page.locator('.desktop-table tbody tr').filter({ hasText: purchase.order_no })
  await expect(purchaseRow).toHaveCount(1)
  await purchaseRow.getByRole('button', { name: '编辑', exact: true }).click()
  await editor.getByLabel('备注', { exact: true }).fill(`采购单头已编辑${suffix}`)
  const [updateResponse] = await Promise.all([
    page.waitForResponse((item) => item.url().endsWith(`/api/purchase-orders/${purchase.id}`) && item.request().method() === 'PUT'),
    editor.getByRole('button', { name: '保存单头', exact: true }).click(),
  ])
  expect(updateResponse.status(), await updateResponse.text()).toBe(200)
  await expect(editor).toHaveCount(0)
  await expect(purchaseRow).toContainText(`采购单头已编辑${suffix}`)
  await expect(purchaseRow).toContainText('12.5')

  const [confirmResponse] = await Promise.all([
    page.waitForResponse((item) => item.url().endsWith(`/api/purchase-orders/${purchase.id}/confirm`) && item.request().method() === 'POST'),
    purchaseRow.getByRole('button', { name: '确认', exact: true }).click(),
  ])
  expect(confirmResponse.status(), await confirmResponse.text()).toBe(200)
  expect((await confirmResponse.json()).status).toBe('done')
  await expect(purchaseRow).toContainText('已完成')
  await expect(purchaseRow.getByRole('button', { name: '确认', exact: true })).toHaveCount(0)
  await expect(purchaseRow.getByRole('button', { name: '删除', exact: true })).toHaveCount(0)
  await purchaseRow.getByRole('button', { name: '详情', exact: true }).click()
  await expect(editor.getByLabel('供应商名称（合同为文本字段）')).toBeDisabled()
  await expect(editor.getByLabel('备注', { exact: true })).toBeDisabled()
  await expect(editor.getByRole('button', { name: '保存单头', exact: true })).toHaveCount(0)
  await expect(editor.locator('tbody')).toContainText('12.5')
  const rejected = await call(page, 'PUT', `/purchase-orders/${purchase.id}`, { supplier_name: '终态不应修改', remark: '终态不应修改' })
  expect(rejected.status).toBe(400)
  const unchanged = expectStatus(await call(page, 'GET', `/purchase-orders/${purchase.id}`), 200)
  expect(unchanged.supplier_name).toBe(supplier)
  expect(unchanged.remark).toBe(`采购单头已编辑${suffix}`)
  expect(errors).toEqual([])
})
