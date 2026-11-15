import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'

const phone = process.env.WEB_PHONE
const password = process.env.WEB_PASSWORD

test.beforeEach(async () => {
  test.skip(!phone || !password, '设置 WEB_PHONE/WEB_PASSWORD 后运行隔离环境动态报表测试')
})

test('动态报表设计器执行全公司汇总并导出同一份计划', async ({ page }) => {
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/web/#ai')
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.topbar h1')).not.toHaveText('登录')

  await page.getByRole('tab', { name: '报表设计器', exact: true }).click()
  await expect(page.getByLabel('数据集', { exact: true })).toHaveValue('piecework_records')
  await expect(page.locator('.designer-loading')).toHaveCount(0)
  await expect(page.locator('.designer-card .form-error')).toHaveCount(0)

  // 不选分组时，即使当前没有登记，也应返回一行全公司汇总，便于验证执行链路。
  const dimensionSection = page.locator('.designer-section').filter({ has: page.getByRole('heading', { name: /分组维度/ }) })
  const processDimension = dimensionSection.getByLabel('工序', { exact: true })
  if (await processDimension.isChecked()) await processDimension.uncheck()
  await page.getByRole('button', { name: '全部时间', exact: true }).click()
  const [executeResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith('/api/ai/report/execute') && response.request().method() === 'POST'),
    page.getByRole('button', { name: '执行查询', exact: true }).click(),
  ])
  expect(executeResponse.status()).toBe(200)
  await expect(page.locator('.dynamic-report-table')).toBeVisible()
  await expect(page.locator('.dynamic-report-table thead')).toContainText('登记件数')
  await expect(page.locator('.dynamic-report-table tbody tr')).toHaveCount(1)
  await expect(page.locator('.query-scope')).toContainText('排除已撤销登记')

  // 保存的是计划而非结果；打开时走独立执行端点重新校验，结束后删除测试数据。
  const savedName = `E2E私有报表-${Date.now()}`
  await page.getByLabel('保存报表名称', { exact: true }).fill(savedName)
  const [saveResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith('/api/ai/report/saved') && response.request().method() === 'POST'),
    page.getByRole('button', { name: '保存当前', exact: true }).click(),
  ])
  expect(saveResponse.status()).toBe(201)
  const savedId = (await saveResponse.json()).id
  await page.getByLabel('已保存报表', { exact: true }).selectOption(String(savedId))
  const [openResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith(`/api/ai/report/saved/${savedId}/execute`) && response.request().method() === 'POST'),
    page.getByRole('button', { name: '打开并执行', exact: true }).click(),
  ])
  expect(openResponse.status()).toBe(200)
  page.once('dialog', (dialog) => dialog.accept())
  const [deleteResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith(`/api/ai/report/saved/${savedId}`) && response.request().method() === 'DELETE'),
    page.getByRole('button', { name: '删除', exact: true }).click(),
  ])
  expect(deleteResponse.status()).toBe(204)

  const exportButton = page.getByRole('button', { name: '同步导出', exact: true })
  if (await exportButton.count()) {
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      exportButton.click(),
    ])
    expect(download.suggestedFilename()).toMatch(/\.xlsx$/i)
    const content = readFileSync(await download.path())
    expect(content.subarray(0, 2).toString()).toBe('PK')
  }

  // 通用筛选仍使用同一执行器；无匹配时汇总值为零而不是伪造明细。
  await page.getByRole('button', { name: '添加筛选', exact: true }).click()
  await page.getByLabel('筛选字段 1', { exact: true }).selectOption('color')
  await page.getByLabel('筛选操作 1', { exact: true }).selectOption('contains')
  await page.getByLabel('筛选值 1', { exact: true }).fill('__WEB_AI_NO_MATCH__')
  const [filteredResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith('/api/ai/report/execute') && response.request().method() === 'POST'),
    page.getByRole('button', { name: '执行查询', exact: true }).click(),
  ])
  expect(filteredResponse.status()).toBe(200)
  await expect(page.locator('.dynamic-report-table tbody tr')).toHaveCount(1)
  await expect(page.locator('.designer-error')).toHaveCount(0)

  // 有订单权限时，切换数据集后日期字段和指标应整体重置，状态由选项提供。
  const datasetSelect = page.getByLabel('数据集', { exact: true })
  if (await datasetSelect.locator('option[value="orders"]').count()) {
    await datasetSelect.selectOption('orders')
    await page.getByRole('button', { name: '添加筛选', exact: true }).click()
    await page.getByLabel('筛选字段 1', { exact: true }).selectOption('status')
    await expect(page.getByLabel('筛选值 1', { exact: true })).toHaveValue('producing')
    const [orderResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith('/api/ai/report/execute') && response.request().method() === 'POST'),
      page.getByRole('button', { name: '执行查询', exact: true }).click(),
    ])
    expect(orderResponse.status()).toBe(200)
    await expect(page.locator('.dynamic-report-table')).toBeVisible()
    await expect(page.locator('.dynamic-report-table thead')).toContainText('订单数')
    await expect(page.locator('.query-scope')).toContainText('排除已删除订单')
  }

  // 出货数据集明确区分实际出货与含草稿的单据数量。
  if (await datasetSelect.locator('option[value="shipments"]').count()) {
    await datasetSelect.selectOption('shipments')
    const [shipmentResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith('/api/ai/report/execute') && response.request().method() === 'POST'),
      page.getByRole('button', { name: '执行查询', exact: true }).click(),
    ])
    expect(shipmentResponse.status()).toBe(200)
    await expect(page.locator('.dynamic-report-table thead')).toContainText('实际出货数量')
    await expect(page.locator('.dynamic-report-table thead')).toContainText('单据数量')
    await expect(page.locator('.query-scope')).toContainText('发货日期')
  }

  // 仓库数量强制保留单位维度，不能把米、公斤等不同单位混加。
  if (await datasetSelect.locator('option[value="warehouse_lots"]').count()) {
    await datasetSelect.selectOption('warehouse_lots')
    const unitDimension = dimensionSection.getByLabel('单位', { exact: true })
    await expect(unitDimension).toBeChecked()
    await expect(unitDimension).toBeDisabled()
    const [warehouseResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith('/api/ai/report/execute') && response.request().method() === 'POST'),
      page.getByRole('button', { name: '执行查询', exact: true }).click(),
    ])
    expect(warehouseResponse.status()).toBe(200)
    await expect(page.locator('.dynamic-report-table thead')).toContainText('当前流水库存')
    await expect(page.locator('.dynamic-report-table thead')).toContainText('单位')
    await expect(page.locator('.report-note')).toContainText('不同单位不得混加')
  }
  expect(errors).toEqual([])
})

test('AI 聊天优先生成并执行受治理 QueryPlan', async ({ page }) => {
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/web/#ai')
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.topbar h1')).not.toHaveText('登录')

  const [summaryResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith('/api/ai/report/query') && response.request().method() === 'POST'),
    page.getByRole('button', { name: '今天完成了多少件？', exact: true }).first().click(),
  ])
  expect(summaryResponse.status()).toBe(200)
  expect(summaryResponse.request().postDataJSON().mode).toBe('query_plan')
  const summaryBody = await summaryResponse.json()
  expect(summaryBody.mode).toBe('query_plan')
  expect(summaryBody.planner.source).toBe('local')
  await expect(page.locator('.report-block').last()).toContainText('登记件数')
  await expect(page.locator('.report-block').last()).toContainText('排除已撤销登记')

  await page.getByLabel('输入业务问题', { exact: true }).fill('今天各工序登记多少件？')
  const [groupResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith('/api/ai/report/query') && response.request().method() === 'POST'),
    page.getByRole('button', { name: '发送', exact: true }).click(),
  ])
  expect(groupResponse.status()).toBe(200)
  const groupBody = await groupResponse.json()
  expect(groupBody.plan.dimensions).toEqual(['process_name'])
  await expect(page.locator('.report-block').last()).toContainText('工序')
  await expect(page.locator('.report-block').last()).toContainText('登记件数')

  await page.getByLabel('输入业务问题', { exact: true }).fill('各面料当前库存')
  const [warehouseResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith('/api/ai/report/query') && response.request().method() === 'POST'),
    page.getByRole('button', { name: '发送', exact: true }).click(),
  ])
  expect(warehouseResponse.status()).toBe(200)
  const warehouseBody = await warehouseResponse.json()
  expect(warehouseBody.plan.dataset).toBe('warehouse_lots')
  expect(warehouseBody.plan.dimensions).toEqual(['fabric_name', 'unit'])
  await expect(page.locator('.report-block').last()).toContainText('当前流水库存')
  expect(errors).toEqual([])
})

test('图表与公司分享复用同一计划，明细表始终保留', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/web/#ai')
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('tab', { name: '报表设计器', exact: true }).click()
  await expect(page.getByLabel('数据集', { exact: true })).toHaveValue('piecework_records')
  await page.getByRole('button', { name: '全部时间', exact: true }).click()
  await page.getByRole('checkbox', { name: '预估工资', exact: true }).uncheck()
  await page.getByRole('checkbox', { name: '登记笔数', exact: true }).uncheck()
  await page.getByRole('combobox', { name: '展示方式', exact: true }).selectOption('bar')
  await page.getByRole('button', { name: '执行查询', exact: true }).click()
  await expect(page.locator('.report-chart')).toBeVisible()
  await expect(page.locator('.dynamic-report-table')).toBeVisible()
  const dimensionSection = page.locator('.designer-section').filter({ has: page.getByRole('heading', { name: /分组维度/ }) })
  const process = dimensionSection.getByLabel('工序', { exact: true })
  if (await process.isChecked()) await process.uncheck()
  await dimensionSection.getByLabel('登记日', { exact: true }).check()
  await page.getByRole('combobox', { name: '展示方式', exact: true }).selectOption('line')
  await page.getByRole('button', { name: '执行查询', exact: true }).click()
  await expect(page.locator('.line-chart')).toBeVisible()
  await expect(page.locator('.dynamic-report-table')).toBeVisible()

  const name = `E2E共享图表-${Date.now()}`
  await page.getByLabel('保存报表名称', { exact: true }).fill(name)
  await page.getByRole('button', { name: '保存当前', exact: true }).click()
  const saved = page.getByLabel('已保存报表', { exact: true })
  const id = await saved.inputValue()
  await page.getByRole('button', { name: '分享设置', exact: true }).click()
  await page.getByRole('combobox', { name: '分享范围', exact: true }).selectOption('company')
  const [shareResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith(`/api/ai/report/saved/${id}/share`)),
    page.getByRole('button', { name: '保存分享设置', exact: true }).click(),
  ])
  expect(shareResponse.status()).toBe(200)
  await expect(saved.locator('option:checked')).toContainText('全公司')
  await page.getByRole('button', { name: '分享设置', exact: true }).click()
  await page.getByRole('combobox', { name: '分享范围', exact: true }).selectOption('private')
  await page.getByRole('button', { name: '保存分享设置', exact: true }).click()
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '删除', exact: true }).click()
})

test('异步导出由 worker 生成并可从任务列表下载', async ({ page }) => {
  test.skip(process.env.WEB_AI_WORKER !== '1', '设置 WEB_AI_WORKER=1 并启动隔离导出 worker 后运行')
  await page.goto('/web/#ai')
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('tab', { name: '报表设计器', exact: true }).click()
  await expect(page.getByLabel('数据集', { exact: true })).toHaveValue('piecework_records')
  await page.getByRole('button', { name: '执行查询', exact: true }).click()
  await expect(page.locator('.dynamic-report-table')).toBeVisible()
  const exportButton = page.getByRole('button', { name: '创建异步导出', exact: true })
  test.skip(!(await exportButton.count()), '当前账号无 ai.export 权限')
  await exportButton.click()
  await expect(page.locator('.export-task').first()).toContainText('可下载', { timeout: 20000 })
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.locator('.export-task').first().getByRole('button', { name: '下载', exact: true }).click(),
  ])
  expect(download.suggestedFilename()).toMatch(/\.xlsx$/i)
})

test('真实隔离环境定时报表创建启停、手动执行和删除', async ({ page }) => {
  await page.goto('/web/#ai')
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('tab', { name: '报表设计器', exact: true }).click()
  await expect(page.getByLabel('数据集', { exact: true })).toHaveValue('piecework_records')
  const name = `E2E定时任务-${Date.now()}`
  await page.getByLabel('定时任务名称', { exact: true }).fill(name)
  await page.getByLabel('定时执行频率', { exact: true }).selectOption('daily')
  await page.getByLabel('定时小时', { exact: true }).fill('9')
  await page.getByLabel('定时分钟', { exact: true }).fill('30')
  const [created] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith('/api/ai/report/schedules') && response.request().method() === 'POST'),
    page.getByRole('button', { name: '创建定时报表', exact: true }).click(),
  ])
  expect(created.status()).toBe(201)
  const item = page.locator('.schedule-item').filter({ hasText: name })
  await expect(item).toContainText('下次执行')
  const id = (await created.json()).id
  for (const action of ['停用', '启用']) {
    const [updated] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith(`/api/ai/report/schedules/${id}`) && response.request().method() === 'PATCH'),
      item.getByRole('button', { name: action, exact: true }).click(),
    ])
    expect(updated.status()).toBe(200)
  }
  const [manualRun] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith(`/api/ai/report/schedules/${id}/run`) && response.request().method() === 'POST'),
    item.getByRole('button', { name: '手动执行', exact: true }).click(),
  ])
  expect(manualRun.status()).toBe(201)
  const run = page.locator('.schedule-run').filter({ hasText: name }).first()
  await expect(run).toContainText('手动执行')
  await expect(run).toContainText('执行成功')
  await expect(run.getByRole('button', { name: '失败重试', exact: true })).toHaveCount(0)
  page.once('dialog', (dialog) => dialog.accept())
  const [removed] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith(`/api/ai/report/schedules/${id}`) && response.request().method() === 'DELETE'),
    item.getByRole('button', { name: `删除定时任务 ${name}`, exact: true }).click(),
  ])
  expect(removed.status()).toBe(204)
  await expect(item).toHaveCount(0)
})

// 以下 P1 用例全部使用本地会话和路由模拟，不连接业务账号、数据库或 GLM。
async function mockP1Ai(page) {
  const state = { schedules: [], executePlans: [], scheduleBodies: [] }
  const plan = {
    dataset: 'piecework_records', metrics: [{ key: 'registered_qty', aggregation: 'sum' }],
    dimensions: ['process_name'], filters: [{ field: 'registered_date', operator: 'between', value: ['2025-10-01', '2025-10-31'] }],
    order_by: [{ field: 'registered_qty', direction: 'desc' }], limit: 100,
    include_deleted: false, presentation: 'table', comparison: 'year_ago',
  }
  const saved = { id: 71, name: '自己的期间报表', is_owner: true, visibility: 'private', plan }
  const report = {
    title: '计件登记动态报表', summary: '对比结果', note: '',
    columns: [{ key: 'process_name', label: '工序', type: 'text' }, { key: 'registered_qty', label: '登记件数', type: 'integer' }],
    headers: ['工序', '登记件数'], rows: [['裁剪', '120'], ['车缝', null]], scope: ['排除已撤销登记'],
    comparison: {
      mode: 'previous', current_range: ['2026-10-01', '2026-10-31'], previous_range: ['2026-09-01', '2026-09-30'],
      available: true, message: '上期为 0 或任一期为空时变化率为空。',
      rows: [
        { dimensions: ['裁剪'], metrics: { registered_qty: { current: '120', previous: '100', delta: '20', change_rate: '0.2' } } },
        { dimensions: ['车缝'], metrics: { registered_qty: { current: null, previous: '0', delta: null, change_rate: 'invalid' } } },
      ],
    },
  }
  await page.addInitScript(() => {
    localStorage.setItem('yisao.web.session.v1', JSON.stringify({
      access: 'p1-local-only', companyId: 1, user: { id: 1, real_name: '本地测试' },
      companies: [{ id: 1, name: '本地模拟工厂', status: 1, post: { name: '测试岗位' } }],
    }))
  })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    const json = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
    if (path === '/api/notifications') return json({ count: 0, results: [] })
    if (path === '/api/ai/report/catalog') return json({
      capabilities: { export: false, full_data: false, share: false },
      datasets: [{
        key: 'piecework_records', label: '计件登记', description: '本地模拟数据集', time_field: 'registered_date',
        default_dimensions: ['process_name'], default_metrics: ['registered_qty'], supports_as_of: false,
        fields: [
          { key: 'registered_date', label: '登记日', can_dimension: true, can_filter: true, operators: ['between'] },
          { key: 'process_name', label: '工序', can_dimension: true, can_filter: true, operators: ['eq', 'contains'] },
        ],
        metrics: [{ key: 'registered_qty', label: '登记件数', default_aggregation: 'sum', aggregations: ['sum'] }],
      }],
    })
    if (path === '/api/ai/report/saved' && method === 'GET') return json({ results: [saved, { id: 72, name: '同事分享报表', is_owner: false, owner: { name: '同事' }, plan } ] })
    if (path === '/api/ai/report/execute' && method === 'POST') {
      const body = request.postDataJSON()
      state.executePlans.push(body.plan)
      return json({ plan: body.plan, report: { ...report, comparison: body.plan.comparison ? { ...report.comparison, mode: body.plan.comparison } : null } })
    }
    if (path === '/api/ai/report/saved/71/execute' && method === 'POST') return json({ plan, report: { ...report, comparison: { ...report.comparison, mode: 'year_ago' } } })
    if (path === '/api/ai/report/query' && method === 'POST') return json({ mode: 'query_plan', plan: { ...plan, comparison: 'previous' }, report, answer: '本地对比完成', planner: { source: 'local' } })
    if (path === '/api/ai/report/schedules' && method === 'GET') return json({ results: state.schedules })
    if (path === '/api/ai/report/schedules' && method === 'POST') {
      const body = request.postDataJSON()
      state.scheduleBodies.push(body)
      const item = {
        id: 800 + state.schedules.length, name: body.name, saved_report_id: body.saved_report_id || null,
        saved_report: body.saved_report_id ? { id: saved.id, name: saved.name, visibility: 'private' } : null,
        plan: body.plan || null, frequency: body.frequency, hour: body.hour, minute: body.minute,
        weekday: body.weekday ?? null, day_of_month: body.day_of_month ?? null, enabled: body.enabled,
        timezone: 'Asia/Shanghai', next_run_at: '2026-11-12T09:30:00+08:00',
      }
      state.schedules.unshift(item)
      return json(item, 201)
    }
    const scheduleMatch = path.match(/^\/api\/ai\/report\/schedules\/(\d+)$/)
    if (scheduleMatch && method === 'PATCH') {
      const item = state.schedules.find((row) => row.id === Number(scheduleMatch[1]))
      Object.assign(item, request.postDataJSON())
      return json(item)
    }
    if (scheduleMatch && method === 'DELETE') {
      state.schedules = state.schedules.filter((row) => row.id !== Number(scheduleMatch[1]))
      return route.fulfill({ status: 204, body: '' })
    }
    return json({ detail: `未模拟接口：${method} ${path}` }, 500)
  })
  return state
}

test('P1 期间对比双向计划、空值安全和双区域条件摘要', async ({ page }) => {
  const state = await mockP1Ai(page)
  await page.goto('/web/#ai')
  await page.getByRole('tab', { name: '报表设计器', exact: true }).click()
  await expect(page.getByLabel('数据集', { exact: true })).toHaveValue('piecework_records')
  await page.getByLabel('期间对比', { exact: true }).selectOption('previous')
  await page.getByRole('button', { name: '全部时间', exact: true }).click()
  await page.getByRole('button', { name: '执行查询', exact: true }).click()
  await expect(page.locator('.designer-error')).toContainText('期间对比不能用于全部时间')
  expect(state.executePlans).toHaveLength(0)

  await page.getByLabel('开始日期', { exact: true }).fill('2026-10-01')
  await page.getByLabel('结束日期', { exact: true }).fill('2026-10-31')
  await page.getByRole('button', { name: '执行查询', exact: true }).click()
  await expect(page.locator('.designer-result .comparison-table')).toBeVisible()
  expect(state.executePlans[0].comparison).toBe('previous')
  const comparison = page.locator('.designer-result .comparison-block')
  await expect(comparison).toContainText('2026-10-01 至 2026-10-31')
  await expect(comparison).toContainText('2026-09-01 至 2026-09-30')
  await expect(comparison).toContainText('20%')
  await expect(comparison.locator('tbody tr').nth(1)).toContainText('—')
  await expect(comparison).not.toContainText('NaN')
  await expect(page.locator('.designer-result .dynamic-report-table')).toBeVisible()
  const summary = page.getByLabel('设计器已执行计划条件', { exact: true })
  await expect(summary).toContainText('计件登记')
  await expect(summary).toContainText('工序')
  await expect(summary).toContainText('登记件数')
  await expect(summary).toContainText('0 条（不含期间）')

  await page.getByLabel('已保存报表', { exact: true }).selectOption('71')
  await page.getByRole('button', { name: '打开并执行', exact: true }).click()
  await expect(page.getByLabel('期间对比', { exact: true })).toHaveValue('year_ago')
  await expect(page.getByLabel('开始日期', { exact: true })).toHaveValue('2025-10-01')

  await page.getByRole('tab', { name: 'AI 问报', exact: true }).click()
  await page.getByRole('button', { name: '今天完成了多少件？', exact: true }).first().click()
  const chatReport = page.locator('.report-block').last()
  await expect(chatReport.locator('.comparison-table')).toBeVisible()
  await expect(chatReport.locator('table').last()).toBeVisible()
  await expect(chatReport.getByLabel('本轮已执行计划条件', { exact: true })).toContainText('期间')
})

test('P1 定时报表支持当前计划与自己的保存报表并管理启停删除', async ({ page }) => {
  const state = await mockP1Ai(page)
  await page.goto('/web/#ai')
  await page.getByRole('tab', { name: '报表设计器', exact: true }).click()
  await expect(page.getByLabel('数据集', { exact: true })).toHaveValue('piecework_records')
  await page.getByLabel('定时任务名称', { exact: true }).fill('当前条件周报')
  await page.getByLabel('定时执行频率', { exact: true }).selectOption('weekly')
  await page.getByLabel('每周执行日', { exact: true }).selectOption('2')
  await page.getByLabel('定时小时', { exact: true }).fill('9')
  await page.getByLabel('定时分钟', { exact: true }).fill('30')
  await page.getByRole('button', { name: '创建定时报表', exact: true }).click()
  await expect(page.locator('.schedule-item')).toHaveCount(1)
  expect(state.scheduleBodies[0].weekday).toBe(2)
  expect(state.scheduleBodies[0].plan.dataset).toBe('piecework_records')
  expect(state.scheduleBodies[0].saved_report_id).toBeUndefined()
  await expect(page.locator('.schedule-item')).toContainText('每周三 09:30')
  await expect(page.locator('.schedule-item')).toContainText('下次执行')

  await page.getByLabel('定时计划来源', { exact: true }).selectOption('saved')
  await expect(page.getByLabel('定时已保存报表', { exact: true }).locator('option')).toHaveCount(2)
  await expect(page.getByLabel('定时已保存报表', { exact: true })).not.toContainText('同事分享报表')
  await page.getByLabel('定时任务名称', { exact: true }).fill('保存报表月报')
  await page.getByLabel('定时执行频率', { exact: true }).selectOption('monthly')
  await page.getByLabel('每月执行日', { exact: true }).fill('31')
  await page.getByRole('button', { name: '创建定时报表', exact: true }).click()
  await expect(page.locator('.schedule-item')).toHaveCount(2)
  expect(state.scheduleBodies[1].saved_report_id).toBe(71)
  expect(state.scheduleBodies[1].plan).toBeUndefined()
  expect(state.scheduleBodies[1].day_of_month).toBe(31)
  await expect(page.locator('.schedule-item').first()).toContainText('每月 31 日')

  await page.locator('.schedule-item').first().getByRole('button', { name: '停用', exact: true }).click()
  await expect(page.locator('.schedule-item').first()).toContainText('已停用')
  await page.locator('.schedule-item').first().getByRole('button', { name: '启用', exact: true }).click()
  await expect(page.locator('.schedule-item').first()).toContainText('下次执行')
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '删除定时任务 保存报表月报', exact: true }).click()
  await expect(page.locator('.schedule-item')).toHaveCount(1)
  await expect(page.locator('.schedule-manager')).toContainText('不包含也不保存查询结果')
})
