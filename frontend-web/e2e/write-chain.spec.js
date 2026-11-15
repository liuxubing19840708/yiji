import { test, expect } from '@playwright/test'

const phone = process.env.WEB_PHONE
const password = process.env.WEB_PASSWORD

test.beforeEach(async () => {
  test.skip(!phone || !password, '设置 WEB_PHONE/WEB_PASSWORD 后运行隔离环境写链路测试')
})

async function cleanup(page, path) {
  return page.evaluate(async (apiPath) => {
    const session = JSON.parse(localStorage.getItem('yisao.web.session.v1'))
    const response = await fetch(`/api${apiPath}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${session.access}`, 'X-Company-Id': String(session.companyId) },
    })
    return { status: response.status, body: await response.text() }
  }, path)
}

test('订单→开床编菲→打印预览→扫菲→撤销→删除全链路', async ({ page }) => {
  test.setTimeout(120000)
  const errors = []
  const cleanupErrors = []
  let orderId, productionId, recordId
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('dialog', (dialog) => dialog.accept())
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/web/#orders')
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.welcome h2')).toHaveText('订单管理')

  try {
    await page.getByRole('button', { name: '新建订单', exact: true }).click()
    const orderDialog = page.getByRole('dialog', { name: '订单编辑' })
    await expect(orderDialog.locator('select').first().locator('option').nth(1)).toBeAttached()
    await orderDialog.locator('select').first().selectOption({ index: 1 })
    const fixedProcesses = await page.evaluate(async () => {
      const session = JSON.parse(localStorage.getItem('yisao.web.session.v1'))
      const response = await fetch('/api/processes?is_fixed=1&page_size=1', {
        headers: { Authorization: `Bearer ${session.access}`, 'X-Company-Id': String(session.companyId) },
      })
      return response.json()
    })
    expect(fixedProcesses.results.length).toBeGreaterThan(0)
    const selectedProcessId = fixedProcesses.results[0].id
    await orderDialog.getByPlaceholder('颜色').fill('测试蓝')
    await orderDialog.getByPlaceholder('尺码').fill('M')
    await orderDialog.locator('input[type=number]').first().fill('2')
    await orderDialog.getByRole('button', { name: '添加工序', exact: true }).click()
    await orderDialog.locator('select').last().selectOption(String(selectedProcessId))
    await orderDialog.locator('input[type=number]').nth(1).fill('0.85')
    const [orderResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith('/api/orders') && response.request().method() === 'POST'),
      orderDialog.getByRole('button', { name: '保存订单', exact: true }).click(),
    ])
    expect(orderResponse.status()).toBe(201)
    const order = await orderResponse.json()
    orderId = order.id

    await page.locator('.side-nav').getByRole('button', { name: '生产床次', exact: true }).click()
    await page.getByRole('button', { name: '新建床次', exact: true }).click()
    const productionEditor = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: '新建床次', exact: true }) })
    const bedNo = `W${Date.now().toString().slice(-9)}`
    await productionEditor.getByLabel('床次号 *').fill(bedNo)
    await expect(productionEditor.getByLabel('关联订单').locator(`option[value="${orderId}"]`)).toBeAttached()
    await productionEditor.getByLabel('关联订单').selectOption(String(orderId))
    await expect(productionEditor.getByLabel('款式 *')).toHaveValue(String(order.style))
    await expect(productionEditor.getByLabel('款式 *')).toBeDisabled()
    await expect(productionEditor.getByLabel('计划裁货数')).toHaveValue('2')
    await expect(productionEditor.locator('.production-process-editor select')).toHaveValue(String(selectedProcessId))
    const inheritedPrice = productionEditor.locator('.production-process-editor input[aria-label="工价"]')
    await expect.poll(async () => Number(await inheritedPrice.inputValue())).toBe(0.85)
    await productionEditor.getByText('无生效 BOM 时申请留痕放行').click()
    await productionEditor.getByLabel('放行原因').fill('Web 隔离环境全链路自动测试')
    const [productionResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith('/api/productions') && response.request().method() === 'POST'),
      productionEditor.getByRole('button', { name: '保存床次', exact: true }).click(),
    ])
    expect(productionResponse.status()).toBe(201)
    productionId = (await productionResponse.json()).id
    const productionDetail = page.getByRole('dialog', { name: `床次 ${bedNo}` })
    await expect(productionDetail).toBeVisible()
    await expect(productionDetail.locator('.spinner')).toHaveCount(0)

    await productionDetail.getByRole('button', { name: '编菲加扎', exact: true }).click()
    const ticketEditor = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: '编菲加扎', exact: true }) })
    await expect(ticketEditor.locator('.spinner')).toHaveCount(0)
    await expect(ticketEditor.getByLabel('颜色', { exact: true })).toHaveValue('测试蓝')
    await expect(ticketEditor.getByLabel('尺码', { exact: true })).toHaveValue('M')
    await expect(ticketEditor.locator('.production-bundle-editor input[aria-label="数量"]')).toHaveValue('2')
    await expect(ticketEditor.locator('input[placeholder="颜色"], input[placeholder="尺码"]')).toHaveCount(0)
    await ticketEditor.getByLabel('每扎上限').fill('2')
    const [ticketResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith('/api/tickets/batch') && response.request().method() === 'POST'),
      ticketEditor.getByRole('button', { name: '生成菲票', exact: true }).click(),
    ])
    expect(ticketResponse.status()).toBe(201)
    expect((await ticketResponse.json()).length).toBe(1)
    await expect(productionDetail.locator('tbody tr')).toHaveCount(1)

    await productionDetail.getByRole('button', { name: '打印菲票', exact: true }).click()
    const printDialog = page.getByRole('dialog', { name: '菲票打印预览' })
    await expect(printDialog).toBeVisible()
    await expect(printDialog.locator('img')).toHaveCount(1)
    await printDialog.getByRole('button', { name: '关闭', exact: true }).click()

    await productionDetail.getByRole('button', { name: '锁定床次', exact: true }).click()
    await expect(productionDetail.getByRole('button', { name: '解锁床次', exact: true })).toBeVisible()
    await productionDetail.getByRole('button', { name: '解锁床次', exact: true }).click()
    await expect(productionDetail.getByRole('button', { name: '锁定床次', exact: true })).toBeVisible()
    await productionDetail.getByRole('button', { name: '关闭', exact: true }).click()

    await page.locator('.side-nav').getByRole('button', { name: '扫菲', exact: true }).click()
    await page.getByRole('button', { name: '床次＋扎号', exact: true }).click()
    await page.getByPlaceholder('例如：C001').fill(bedNo)
    await page.getByPlaceholder('例如：1').fill('1')
    await page.getByRole('button', { name: '查询菲票', exact: true }).click()
    const processSelect = page.locator('#register-card select[required]')
    await expect(processSelect.locator(`option[value="${selectedProcessId}"]`)).toBeAttached()
    await processSelect.selectOption(String(selectedProcessId))
    const [recordResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith('/api/records') && response.request().method() === 'POST'),
      page.getByRole('button', { name: '确认登记', exact: true }).click(),
    ])
    const recordBody = await recordResponse.text()
    expect(recordResponse.status(), recordBody).toBe(201)
    recordId = JSON.parse(recordBody).id
    await expect(page.locator('#register-card').getByRole('status')).toContainText('登记成功')

    await page.locator('.side-nav').getByRole('button', { name: '计件记录', exact: true }).click()
    const recordRow = page.locator('.desktop-table tbody tr').filter({ hasText: bedNo }).first()
    await expect(recordRow).toBeVisible()
    const [undoResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith(`/api/records/${recordId}`) && response.request().method() === 'DELETE'),
      recordRow.getByRole('button', { name: '撤销', exact: true }).click(),
    ])
    expect(undoResponse.ok()).toBe(true)
    recordId = null
    await expect(page.locator('.desktop-table tbody tr').filter({ hasText: bedNo })).toHaveCount(0)

    await page.locator('.side-nav').getByRole('button', { name: '生产床次', exact: true }).click()
    await page.getByPlaceholder('搜索床次号、款号或客户').fill(bedNo)
    await expect(page.locator('.desktop-table tbody tr').filter({ hasText: bedNo })).toHaveCount(1)
    await page.locator('.desktop-table tbody tr').filter({ hasText: bedNo }).click()
    const [deleteProductionResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith(`/api/productions/${productionId}`) && response.request().method() === 'DELETE'),
      productionDetail.getByRole('button', { name: '删除床次', exact: true }).click(),
    ])
    expect(deleteProductionResponse.ok()).toBe(true)
    productionId = null

    await page.locator('.side-nav').getByRole('button', { name: '订单管理', exact: true }).click()
    await page.getByPlaceholder('搜索订单号或客户').fill(order.order_no)
    const orderRow = page.locator('.desktop-table tbody tr').filter({ hasText: order.order_no })
    await expect(orderRow).toHaveCount(1)
    await orderRow.click()
    const orderDetail = page.getByRole('dialog', { name: `订单 ${order.order_no}` })
    await orderDetail.getByRole('button', { name: '完成订单', exact: true }).click()
    await expect(orderDetail).toHaveCount(0)
    await orderRow.click()
    const [deleteOrderResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith(`/api/orders/${orderId}`) && response.request().method() === 'DELETE'),
      orderDetail.getByRole('button', { name: '删除', exact: true }).click(),
    ])
    expect(deleteOrderResponse.ok()).toBe(true)
    orderId = null
    expect(errors).toEqual([])
  } finally {
    for (const [id, path] of [[recordId, `/records/${recordId}`], [productionId, `/productions/${productionId}`], [orderId, `/orders/${orderId}`]]) {
      if (!id) continue
      const result = await cleanup(page, path)
      if (!result.status || result.status >= 400) cleanupErrors.push(`${path}: ${result.status} ${result.body}`)
    }
  }
  expect(cleanupErrors).toEqual([])
})
