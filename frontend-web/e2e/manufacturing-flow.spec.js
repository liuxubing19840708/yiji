import { test, expect } from '@playwright/test'

const ticket = { id: 31, production: 7, style_no: 'MOCK-STYLE', style_name: 'Mock 款', bed_no: 'MOCK-BED', bundle_no: 3, color: 'Mock 色', size: 'M', qty: 10, qr_code: 'MOCK-TICKET' }
const nodes = [
  { id: 101, stage: 1, stage_code: 'CUT', stage_name: 'Mock 裁剪', stage_type: 'cutting', sequence: 1, required: true, allow_parallel: false, allow_skip: false },
  { id: 102, stage: 2, stage_code: 'CHECK', stage_name: 'Mock 半检', stage_type: 'semi_inspection', sequence: 2, required: true, allow_parallel: false, allow_skip: false },
  { id: 103, stage: 3, stage_code: 'PACK', stage_name: 'Mock 包装', stage_type: 'packing', sequence: 3, required: true, allow_parallel: false, allow_skip: false },
  { id: 104, stage: 4, stage_code: 'IN', stage_name: 'Mock 入库', stage_type: 'finished_inbound', sequence: 4, required: true, allow_parallel: false, allow_skip: false },
]
const routeInstance = { id: 11, code: 'MOCK-ROUTE', name: 'Mock 路线', is_template: false, order: null, production: 7, source_template: null, nodes }
const balance = { received: 10, qualified: 10, rework: 0, scrapped: 0, transferred: 0, inputs: 10, available: 10, qualification_remaining: 10, transferable: 10, reversals: 0 }
const inspection = { id: 51, ticket: ticket.id, ticket_bed_no: ticket.bed_no, ticket_bundle_no: ticket.bundle_no, route: routeInstance.id, node: 102, node_name: 'Mock 半检', node_sequence: 2, inspection_type: 'semi_inspection', inspection_type_display: '半成品检验', status: 'pending', status_display: '待确认', inspected: 1, qualified: 1, rework: 0, scrapped: 0, rework_node: null, rework_node_name: null, reinspection_of: null, reason: '', inspector_name: 'Mock 检验员', inspected_at: '2026-11-01T01:00:00Z' }
const packingLine = { id: 81, ticket: ticket.id, bed_no: ticket.bed_no, bundle_no: ticket.bundle_no, color: ticket.color, size: ticket.size, quantity: 5 }
const packingTask = { id: 61, task_no: 'MOCK-PACK', route: routeInstance.id, route_code: routeInstance.code, packing_node: 103, inbound_node: 104, status: 'draft', status_display: '待审核', total_quantity: 5, boxes: [{ id: 71, box_no: 'MOCK-BOX', total_quantity: 5, lines: [packingLine] }], remark: '', created_at: '2026-11-01T01:00:00Z' }
const inboundDocument = { id: 62, document_no: 'MOCK-IN', route: routeInstance.id, route_code: routeInstance.code, warehouse_name: 'Mock 仓', location_name: 'A-1', status: 'draft', status_display: '待审核', total_quantity: 5, lines: [{ ...packingLine, id: 82, box: 71, box_no: 'MOCK-BOX' }], remark: '', created_at: '2026-11-01T01:00:00Z' }
const pageOf = (items) => ({ count: items.length, next: null, previous: null, results: items })
async function json(route, data, status = 200) { await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) }) }
function session(admin = true) {
  return { access: 'manufacturing-mock', companyId: 1, user: { id: 9, real_name: 'Mock 操作员' }, companies: [{ id: 1, name: 'Mock 工厂', post: admin ? { name: 'Mock 管理', is_admin: true } : { name: 'Mock 只读', effective_perms: ['manufacturing.view', 'quality.view', 'cutting.view', 'finished_goods.view', 'preproduction.view'] } }] }
}
let inspectionRows = []
let packingRows = []
let inboundRows = []
let orderRows = []
let employeeRows = []
let techPackRows = []
async function install(page, handler, admin = true, routeRows = [routeInstance]) {
  inspectionRows = []
  packingRows = [structuredClone(packingTask)]
  inboundRows = [structuredClone(inboundDocument)]
  orderRows = []
  employeeRows = []
  techPackRows = []
  await page.addInitScript((value) => localStorage.setItem('yisao.web.session.v1', JSON.stringify(value)), session(admin))
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace(/^\/api/, '')
    if (path === '/notifications') return json(route, pageOf([]))
    if (path === '/manufacturing/routes') return json(route, routeRows)
    if (path === '/manufacturing/stages') return json(route, nodes.map((node) => ({ id: node.stage, code: node.stage_code, name: node.stage_name, stage_type: node.stage_type, in_use: true, is_active: true })))
    if (path === '/productions') return json(route, pageOf([{ id: 7, bed_no: ticket.bed_no, style_no: ticket.style_no, order: null }]))
    if (path === '/orders') return json(route, pageOf(orderRows))
    if (path === '/employees') return json(route, pageOf(employeeRows))
    if (path === '/tickets') return json(route, pageOf([ticket]))
    if (path === `/manufacturing/routes/${routeInstance.id}/balances`) return json(route, nodes.map((node) => ({ ticket_id: ticket.id, bundle_no: ticket.bundle_no, color: ticket.color, size: ticket.size, node_id: node.id, sequence: node.sequence, stage_type: node.stage_type, balance })))
    if (path === `/manufacturing/routes/${routeInstance.id}/timeline`) return json(route, { route: routeInstance.id, ticket: ticket.id, events: [], total: 0, truncated: false })
    if (path === '/quality/inspections' && request.method() === 'GET') return json(route, pageOf(inspectionRows))
    if (path === '/cutting/handovers/') return json(route, pageOf([]))
    if (path === '/finished-goods/packing-tasks' && request.method() === 'GET') return json(route, packingRows)
    if (path === '/finished-goods/inbounds' && request.method() === 'GET') return json(route, inboundRows)
    if (path === '/finished-goods/inventory') return json(route, { results: [], total_available: 0 })
    if (path === '/preproduction/tech-packs') return json(route, techPackRows)
    if (path.startsWith('/preproduction/orders/')) return json(route, { approved: false, approval: null, problems: [] })
    if (handler) return handler(route, request, path)
    return json(route, { detail: `未配置 mock：${request.method()} ${path}` }, 500)
  })
}

test('全流程执行展示菲票五维身份并用安全 UUID 报工', async ({ page }) => {
  const errors = []
  let actionBody
  page.on('pageerror', (error) => errors.push(error.message))
  await install(page, async (route, request, path) => {
    if (path === '/manufacturing/actions/complete' && request.method() === 'POST') {
      actionBody = request.postDataJSON()
      return json(route, { id: 1, ...actionBody }, 201)
    }
    return json(route, { detail: `unexpected ${path}` }, 500)
  })
  await page.goto('/web/#manufacturing-flow')
  await expect(page.getByRole('heading', { name: '全流程生产', exact: true }).last()).toBeVisible()
  const identity = page.getByTestId('ticket-identity').first()
  await expect(identity).toContainText('MOCK-BED')
  await expect(identity).toContainText('MOCK-STYLE')
  await expect(identity).toContainText('Mock 色')
  await expect(identity).toContainText('M')
  await expect(identity).toContainText('#31')
  await page.getByRole('button', { name: '提交完工判合格', exact: true }).click()
  await expect(page.locator('.action-card .form-error')).toHaveCount(0)
  expect(actionBody.ticket_id).toBe(ticket.id)
  expect(actionBody.node_id).toBe(101)
  expect(actionBody.request_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  expect(errors).toEqual([])
})

test('节点检验可登记并用 UUID 确认', async ({ page }) => {
  let confirmBody
  let createBody
  await install(page, async (route, request, path) => {
    if (path === '/quality/inspections' && request.method() === 'POST') {
      createBody = request.postDataJSON()
      inspectionRows.push({ ...inspection, ...createBody })
      return json(route, inspectionRows[0], 201)
    }
    if (path === `/quality/inspections/${inspection.id}/confirm`) {
      confirmBody = request.postDataJSON()
      inspectionRows[0].status = 'confirmed'
      inspectionRows[0].status_display = '已确认'
      return json(route, inspectionRows[0])
    }
    return json(route, { detail: `unexpected ${path}` }, 500)
  })
  page.on('dialog', (dialog) => dialog.accept())
  await page.goto('/web/#manufacturing-flow')
  await page.getByRole('button', { name: /节点质检/ }).click()
  await page.getByRole('button', { name: '登记检验', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '登记节点检验' })
  await dialog.getByLabel('检验节点 *').selectOption({ label: '2. Mock 半检' })
  await dialog.getByRole('button', { name: '保存待确认检验单' }).click()
  await page.getByRole('button', { name: '确认并记账', exact: true }).click()
  await expect(page.locator('.inspection-card tbody')).toContainText('已确认')
  expect(confirmBody.request_id).toMatch(/^[0-9a-f-]{36}$/)
  expect(createBody.create_request_id).toMatch(/^[0-9a-f-]{36}$/)
  expect(createBody.create_request_id).not.toBe(confirmBody.request_id)
})

test('待审核包装和入库单显示审核入口并提交幂等号', async ({ page }) => {
  const approvalBodies = []
  await install(page, async (route, request, path) => {
    if (path === `/finished-goods/packing-tasks/${packingTask.id}/approve`) {
      approvalBodies.push(request.postDataJSON())
      packingRows[0].status = 'approved'
      packingRows[0].status_display = '已审核'
      return json(route, packingRows[0])
    }
    if (path === `/finished-goods/inbounds/${inboundDocument.id}/approve`) {
      approvalBodies.push(request.postDataJSON())
      inboundRows[0].status = 'approved'
      inboundRows[0].status_display = '已审核'
      return json(route, inboundRows[0])
    }
    return json(route, { detail: `unexpected ${path}` }, 500)
  })
  page.on('dialog', (dialog) => dialog.accept())
  await page.goto('/web/#manufacturing-flow')
  await page.getByRole('button', { name: /包装入库/ }).click()
  await page.getByRole('button', { name: '审核包装', exact: true }).click()
  await expect(page.locator('.document-card').first()).toContainText('已审核')
  await page.getByRole('button', { name: '审核入库', exact: true }).click()
  await expect(page.locator('.document-card').nth(1)).toContainText('已审核')
  expect(approvalBodies).toHaveLength(2)
  approvalBodies.forEach((body) => expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/))
})

test('包装和入库冲销提交原因与独立幂等号并保留单证', async ({ page }) => {
  const bodies = []
  await install(page, async (route, request, path) => {
    if (path === `/finished-goods/packing-tasks/${packingTask.id}/reverse`) {
      bodies.push(request.postDataJSON())
      packingRows[0] = { ...packingRows[0], status: 'reversed', status_display: '已冲销', reversal_reason: bodies[0].reason, reverser_name: 'Mock 操作员', reversed_at: '2026-11-02T01:00:00Z' }
      return json(route, packingRows[0])
    }
    if (path === `/finished-goods/inbounds/${inboundDocument.id}/reverse`) {
      bodies.push(request.postDataJSON())
      inboundRows[0] = { ...inboundRows[0], status: 'reversed', status_display: '已冲销', reversal_reason: bodies[1].reason, reverser_name: 'Mock 操作员', reversed_at: '2026-11-02T01:00:00Z' }
      return json(route, inboundRows[0])
    }
    if (path === `/finished-goods/packing-tasks/${packingTask.id}` || path === `/finished-goods/inbounds/${inboundDocument.id}`) {
      const source = path.includes('packing-tasks') ? packingRows[0] : inboundRows[0]
      return json(route, { ...source, ledger_entries: [
        { id: 901, node_name: 'Mock 包装', event_type_display: '完工判合格', quantity: 5, operator_name: 'Mock 操作员', reversal_of: null },
        { id: 902, node_name: 'Mock 包装', event_type_display: '冲销', quantity: 5, operator_name: 'Mock 操作员', reversal_of: 901 },
      ] })
    }
    return json(route, { detail: `unexpected ${path}` }, 500)
  })
  page.on('dialog', (dialog) => dialog.accept('隔离测试冲销原因'))
  await page.goto('/web/#manufacturing-flow')
  await page.getByRole('button', { name: /包装入库/ }).click()
  await page.getByRole('button', { name: '冲销包装', exact: true }).click()
  await expect(page.locator('.document-card').first()).toContainText('已冲销')
  await page.getByRole('button', { name: '冲销入库', exact: true }).click()
  await expect(page.locator('.document-card').nth(1)).toContainText('已冲销')
  expect(bodies).toHaveLength(2)
  bodies.forEach((body) => {
    expect(body.reason).toBe('隔离测试冲销原因')
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/)
  })
  expect(bodies[0].request_id).not.toBe(bodies[1].request_id)
  await page.getByText('查看箱内菲票、床款色码及冲销关联流水', { exact: true }).click()
  await expect(page.locator('.document-card').first()).toContainText('冲销原流水 #901')
  await expect(page.locator('.document-card').first()).toContainText('Mock 操作员')
  await page.getByText('查看入库菲票、床款色码及冲销关联流水', { exact: true }).click()
  await expect(page.locator('.document-card').nth(1)).toContainText('冲销原流水 #901')
})

test('运营看板展示跨路线汇总、滞留节点并可进入单路线', async ({ page }, testInfo) => {
  let operationQuery = ''
  await install(page, async (route, request, path) => {
    if (path === '/manufacturing/operations') {
      const params = new URL(request.url()).searchParams
      operationQuery = `?${params}`
      const pageNumber = Number(params.get('page') || 1)
      return json(route, {
      total: 2, page: pageNumber, page_size: 20, pages: 2,
      has_next: pageNumber === 1, has_previous: pageNumber === 2,
      truncated: pageNumber === 1, stalled_hours: 24,
      summary: { routes: 2, wip: 12, scrapped: 2, finished_available: 6, stalled_routes: 2 },
      results: [{ route: 11, route_code: 'MOCK-ROUTE', route_name: 'Mock 路线', order: null, production: 7,
        summary: { tickets: 1, ticket_qty: 10, wip: 6, scrapped: 1, finished_available: 3 },
        pending: { quality: 1, cutting: 0, packing: 0, inbound: 0 },
        delivery: { ordered: 10, shipped: 3, remaining: 7 },
        stalled_nodes: [{ node_id: 101, stage_name: 'Mock 裁剪', available: 6, last_activity_at: '2026-11-01T01:00:00Z' }] }],
      })
    }
    if (path === `/manufacturing/routes/${routeInstance.id}/dashboard`) return json(route, {
      route: 11, route_code: 'MOCK-ROUTE', rows: [],
      summary: { tickets: 1, ticket_qty: 10, wip: 6, scrapped: 1, finished_available: 3 },
      pending: { quality: 1, cutting: 0, packing: 0, inbound: 0 }, delivery: { ordered: 10, shipped: 3, remaining: 7 },
    })
    return json(route, { detail: `unexpected ${path}` }, 500)
  })
  orderRows = [{ id: 91, order_no: 'MOCK-SO', customer: 5, customer_name: 'Mock 客户' }]
  await page.goto('/web/#manufacturing-flow')
  await page.getByRole('button', { name: /运营看板/ }).click()
  await page.getByLabel('筛选订单').selectOption('91')
  await page.getByLabel('筛选床次').selectOption('7')
  await page.getByLabel('筛选客户').selectOption('5')
  await page.getByLabel('筛选款号').fill('MOCK-STYLE')
  await page.getByLabel('筛选路线或单号').fill('MOCK-BED')
  await page.getByRole('button', { name: '查询', exact: true }).click()
  expect(operationQuery).toContain('order_id=91')
  expect(operationQuery).toContain('production_id=7')
  expect(operationQuery).toContain('customer_id=5')
  expect(operationQuery).toContain('style_no=MOCK-STYLE')
  expect(operationQuery).toContain('q=MOCK-BED')
  expect(operationQuery).toContain('page=1')
  expect(operationQuery).toContain('page_size=20')
  await expect(page.locator('.operations-summary')).toContainText('滞留路线')
  await expect(page.locator('.operations-card')).toContainText('未交 7 / 订单 10')
  await expect(page.locator('.stalled-node')).toContainText('在手 6')
  await page.screenshot({ path: testInfo.outputPath('operations-dashboard.png'), fullPage: true })
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.locator('.operations-pagination')).toContainText('第 2 / 2 页')
  expect(operationQuery).toContain('page=2')
  await expect(page.locator('.operations-summary')).toContainText('12')
  await page.getByRole('button', { name: '查看路线', exact: true }).click()
  await expect(page.locator('.summary-strip')).toContainText('生产在手')
})

test('流程看板展示统一流水汇总和节点数量', async ({ page }) => {
  await install(page, async (route, request, path) => {
    if (path === `/manufacturing/routes/${routeInstance.id}/dashboard`) return json(route, {
      route: 11, route_code: 'MOCK-ROUTE',
      summary: { tickets: 1, ticket_qty: 10, wip: 6, scrapped: 1, finished_available: 3 },
      pending: { quality: 1, cutting: 2, packing: 3, inbound: 4 },
      delivery: { ordered: 10, shipped: 3, remaining: 7 },
      rows: [{ ticket_id: 31, bed_no: 'MOCK-BED', style_no: 'MOCK-STYLE', bundle_no: 3, color: 'Mock 色', size: 'M', ticket_qty: 10, wip: 6, scrapped: 1, finished_available: 3, nodes: [{ node_id: 101, sequence: 1, stage_name: 'Mock 裁剪', stage_type: 'cutting', ...balance, available: 6 }] }],
    })
    return json(route, { detail: `unexpected ${path}` }, 500)
  })
  await page.goto('/web/#manufacturing-flow')
  await page.getByRole('button', { name: /流程看板/ }).click()
  await expect(page.locator('.summary-strip')).toContainText('生产在手')
  await expect(page.locator('.summary-strip')).toContainText('6')
  await expect(page.locator('.pending-list')).toContainText('待审核入库 4')
  await expect(page.locator('.delivery-line')).toContainText('未交 7 件')
  await page.getByText('查看 1 个节点').click()
  await expect(page.locator('.node-detail')).toContainText('可转 10')
})

test('产前资料可维护尺寸表、样衣、评审和交底', async ({ page }) => {
  const bodies = []
  await install(page, async (route, request, path) => {
    if (path === '/preproduction/tech-packs/91' && request.method() === 'PUT') {
      bodies.push(request.postDataJSON())
      techPackRows[0] = { ...techPackRows[0], ...request.postDataJSON() }
      return json(route, techPackRows[0])
    }
    if (path === '/preproduction/tech-packs/91/samples') {
      bodies.push(request.postDataJSON())
      techPackRows[0].sample_tasks.push({ id: 1, title: 'Mock 产前样', status: 'pending', assignee_name: 'Mock 员工', records: [] })
      return json(route, techPackRows[0].sample_tasks[0], 201)
    }
    if (path === '/preproduction/tech-packs/91/briefing-targets') {
      bodies.push(request.postDataJSON())
      techPackRows[0].briefing_targets.push({ id: 1, employee: 9, employee_name: 'Mock 员工', note: 'Mock 交底', receipt: null })
      return json(route, techPackRows[0].briefing_targets[0], 201)
    }
    return json(route, { detail: `unexpected ${path}` }, 500)
  })
  orderRows = [{ id: 7, order_no: 'MOCK-ORDER', style_no: 'MOCK-STYLE' }]
  employeeRows = [{ id: 1, user_id: 9, name: 'Mock 员工', status: 'active' }]
  techPackRows = [{ id: 91, order: 7, order_no: 'MOCK-ORDER', style: 1, style_no: 'MOCK-STYLE', version_no: 1, pattern_no: 'P-1', is_current: true, content_hash: 'abc', measurements: [{ point: '衣长', size: 'M', value: '68', unit: 'cm', tolerance_plus: '1', tolerance_minus: '1' }], pattern_files: [], attachments: [], tolerances: {}, requirements: 'Mock 工艺', sample_tasks: [], review_issues: [], briefing_targets: [], approvals: [] }]
  await page.goto('/web/#manufacturing-flow')
  await page.getByRole('button', { name: /产前审批/ }).click()
  await expect(page.getByText('衣长 · M：68cm（+1/-1）')).toBeVisible()
  await page.getByRole('button', { name: '编辑当前工艺与尺寸表' }).click()
  await page.getByLabel('纸样编号').fill('P-2')
  await page.getByRole('button', { name: '保存工艺版本' }).click()
  await page.getByPlaceholder('样衣任务名称 *').fill('Mock 产前样')
  await page.locator('.maintenance-grid section').first().locator('select').selectOption('9')
  await page.getByRole('button', { name: '添加', exact: true }).click()
  await page.locator('.briefing-section select').selectOption('9')
  await page.locator('.briefing-section input').fill('Mock 交底')
  await page.getByRole('button', { name: '添加交底' }).click()
  await expect(page.locator('.briefing-section')).toContainText('待签收')
  expect(bodies[0].pattern_no).toBe('P-2')
  expect(bodies[0].measurements[0]).toMatchObject({ point: '衣长', size: 'M', value: '68' })
  expect(bodies[1]).toMatchObject({ title: 'Mock 产前样', assignee_id: 9 })
  expect(bodies[2]).toMatchObject({ employee: 9, note: 'Mock 交底' })
})

test('专用节点不展示可绕过质检、包装和入库审核的通用动作', async ({ page }) => {
  await install(page)
  await page.goto('/web/#manufacturing-flow')
  const source = page.getByLabel('来源节点 *')
  const action = page.getByLabel('动作 *')
  await source.selectOption('102')
  await expect(page.locator('.dedicated-hint')).toContainText('节点质检')
  await expect(action.locator('option')).toHaveText(['首节点接收', '合格转出'])
  await source.selectOption('103')
  await expect(page.locator('.dedicated-hint')).toContainText('包装入库')
  await expect(action.locator('option')).toHaveText(['首节点接收', '返修退回', '报废'])
  await source.selectOption('104')
  await expect(action.locator('option')).toHaveCount(0)
  await expect(page.locator('.dedicated-hint')).toContainText('不开放通用动作')
})

test('只读岗位隐藏所有生产写入口', async ({ page }) => {
  await install(page, null, false)
  await page.goto('/web/#manufacturing-flow')
  await expect(page.locator('.action-card')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '标准阶段', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: /节点质检/ }).click()
  await expect(page.getByRole('button', { name: '登记检验', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: /裁片交接/ }).click()
  await expect(page.getByRole('button', { name: '新建交接单', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: /包装入库/ }).click()
  await expect(page.getByRole('button', { name: '新建包装任务', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '新建入库单', exact: true })).toHaveCount(0)
})

test('模板展示配置警告并支持复制、新版本和历史保护提示', async ({ page }) => {
  const template = {
    id: 12, code: 'MOCK-TEMPLATE', name: 'Mock 模板', is_template: true,
    is_active: true, version: 1, previous_version: null, instance_count: 2,
    configuration_warnings: ['缺少尾查/成品检验节点，不能形成完整成品闭环'], nodes,
  }
  const routeRows = [template]
  const bodies = []
  await install(page, async (route, request, path) => {
    if (path === '/manufacturing/routes/12/copy' || path === '/manufacturing/routes/13/new-version') {
      const body = request.postDataJSON()
      bodies.push({ path, body })
      const result = {
        ...template, id: path.includes('/copy') ? 13 : 14,
        code: body.code, name: body.name, nodes: body.nodes.map((node, index) => ({
          ...nodes.find((item) => item.stage === node.stage_id), ...node, id: 200 + index,
        })),
        version: path.includes('/copy') ? 1 : 2, previous_version: path.includes('/copy') ? null : 13,
        configuration_warnings: [],
      }
      routeRows.push(result)
      return json(route, result, 201)
    }
    return json(route, { detail: `unexpected ${path}` }, 500)
  }, true, routeRows)
  await page.goto('/web/#manufacturing-flow')
  await expect(page.locator('.configuration-warnings')).toContainText('缺少尾查/成品检验节点')
  await expect(page.locator('.template-meta')).toContainText('已有 2 个实例')
  await page.getByRole('button', { name: '复制', exact: true }).click()
  await expect(page.getByRole('heading', { name: '复制独立模板' })).toBeVisible()
  await page.getByLabel('路线节点').first().selectOption('2')
  await page.getByRole('button', { name: '创建独立副本', exact: true }).click()
  await expect(page.getByRole('heading', { name: '复制独立模板' })).toHaveCount(0)
  expect(bodies[0].body.nodes[0].stage_id).toBe(2)
  expect(bodies[0].body.nodes).toHaveLength(4)

  await page.getByRole('button', { name: '新版本', exact: true }).click()
  await expect(page.getByRole('heading', { name: '创建路线新版本' })).toBeVisible()
  await expect(page.getByText('将从 V1 创建 V2', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '创建新版本', exact: true }).click()
  await expect(page.getByRole('heading', { name: '创建路线新版本' })).toHaveCount(0)
  expect(bodies[1].path).toBe('/manufacturing/routes/13/new-version')
  expect(bodies[1].body.nodes).toHaveLength(4)

  await page.getByRole('button', { name: '标准阶段', exact: true }).click()
  await page.locator('.stage-list tbody tr').first().getByRole('button', { name: '编辑' }).click()
  await expect(page.getByText('此阶段已被路线引用，编码和类型不可修改', { exact: false })).toBeVisible()
  await expect(page.getByLabel('阶段编码 *')).toBeDisabled()
  await expect(page.getByLabel('阶段类型 *')).toBeDisabled()
})
