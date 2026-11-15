import { test, expect } from '@playwright/test'

const pageOf = (items) => ({ count: items.length, next: null, previous: null, results: items })
const clone = (value) => structuredClone(value)
async function json(route, data, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
}
function session(permissions = null) {
  return {
    access: 'mock-session-no-login-request', companyId: 1,
    user: { id: 9, real_name: 'Mock 操作员' },
    companies: [{ id: 1, name: 'Mock 工厂', post: permissions
      ? { name: 'Mock 受限岗位', effective_perms: permissions }
      : { name: 'Mock 管理岗位', is_admin: true } }],
  }
}
async function installSession(page, permissions) {
  await page.addInitScript((value) => localStorage.setItem('yisao.web.session.v1', JSON.stringify(value)), session(permissions))
}
function pathOf(request) { return new URL(request.url()).pathname.replace(/^\/api/, '') }

const order = { id: 7, order_no: 'MOCK-ORDER-7', style_no: 'MOCK-STYLE', customer_name: 'Mock 客户', delivery_date: '2026-12-01' }
const originalPack = {
  id: 71, order: 7, version_no: 1, style_no: 'MOCK-STYLE', pattern_no: 'MOCK-PAT-1', is_current: true,
  requirements: '原始工艺要求', challenges: '原始难点', common_mistakes: '原始易错点', quality_standards: '原始品质标准',
  measurements: [{ size: 'M', point: '衣长', unit: 'cm', value: '68', tolerance_plus: '1', tolerance_minus: '1' }],
  pattern_files: ['/media/mock-pattern.pdf'], attachments: [{ name: 'Mock 工艺单', path: '/media/mock-tech.pdf', note: '' }], tolerances: { mock: true },
  sample_tasks: [{ id: 711, title: '大货产前样', records: [{ id: 7111, result: 'failed', note: '首次样检', occurred_at: '2026-11-01T01:00:00Z', operator_name: 'Mock 检验员', attachments: [] }] }],
  review_issues: [{ id: 712, title: '领口问题', detail: '领口尺寸需复核', status: 'open', rectification: '', review_note: '', raised_by_name: 'Mock 工艺员', created_at: '2026-11-01T02:00:00Z' }],
  briefing_confirmations: [], approvals: [],
}
const originalTemplates = [
  { id: 81, kind: 'requirements', name: 'Mock 标准工艺', content: '模板工艺要求', is_system: true },
  { id: 82, kind: 'measurements', name: 'Mock 尺寸模板', content: [{ size: 'L', point: '胸围', unit: 'cm', value: '100', tolerance_plus: '1.5', tolerance_minus: '1' }], is_system: false },
]

async function installPreproduction(page, permissions) {
  const state = { pack: clone(originalPack), templates: clone(originalTemplates), writes: [], queries: [] }
  await installSession(page, permissions)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = pathOf(request)
    const method = request.method()
    const body = method === 'GET' ? null : request.postDataJSON()
    if (path === '/notifications') return json(route, pageOf([]))
    if (path === '/orders' && method === 'GET') {
      state.queries.push(new URL(request.url()).searchParams)
      return json(route, pageOf([order]))
    }
    if (path === '/orders/7') return json(route, order)
    if (path === '/preproduction/orders/7/status') return json(route, {
      status: 'pending_approval', status_display: '待产前审批', approved: false, missing_items: [],
      unresolved_issue_count: state.pack.review_issues.filter((item) => item.status !== 'closed').length,
      current_sample: state.pack.sample_tasks.flatMap((task) => task.records).at(-1) || null,
      briefing_confirmation: state.pack.briefing_confirmations.at(-1) || null,
      approval: state.pack.approvals.find((item) => item.status === 'approved') || null,
    })
    if (path === '/preproduction/templates' && method === 'GET') return json(route, pageOf(state.templates))
    if (path === '/preproduction/tech-packs' && method === 'GET') return json(route, pageOf([state.pack]))
    if (path === '/preproduction/templates' && method === 'POST') {
      state.writes.push({ method, path, body })
      const created = { id: 83, ...body, is_system: false }
      state.templates.push(created)
      return json(route, created, 201)
    }
    if (path === '/preproduction/tech-packs/71' && method === 'PUT') {
      state.writes.push({ method, path, body })
      state.pack = { ...state.pack, ...body }
      return json(route, state.pack)
    }
    if (path === '/preproduction/tech-packs' && method === 'POST') {
      state.writes.push({ method, path, body })
      state.pack = { ...state.pack, ...body, id: 72, version_no: Number(body.version_no || 2), is_current: true }
      return json(route, state.pack, 201)
    }
    if (path === '/preproduction/samples/711/records' && method === 'POST') {
      state.writes.push({ method, path, body })
      const record = { id: 7112, ...body, occurred_at: '2026-11-15T01:00:00Z', operator_name: 'Mock 检验员' }
      state.pack.sample_tasks[0].records.push(record)
      return json(route, record, 201)
    }
    if (path === '/preproduction/tech-packs/71/issues' && method === 'POST') {
      state.writes.push({ method, path, body })
      const issue = { id: 713, ...body, status: 'open', rectification: '', raised_by_name: 'Mock 工艺员', created_at: '2026-11-15T02:00:00Z' }
      state.pack.review_issues.push(issue)
      return json(route, issue, 201)
    }
    if (path === '/preproduction/issues/712' && method === 'PUT') {
      state.writes.push({ method, path, body })
      Object.assign(state.pack.review_issues[0], { status: body.action === 'resolve' ? 'closed' : 'open', rectification: body.resolution || '', review_note: body.reason || '', handled_by_name: 'Mock 工艺员', reviewed_at: '2026-11-15T03:00:00Z' })
      return json(route, state.pack.review_issues[0])
    }
    if (path === '/preproduction/tech-packs/71/briefing-confirmation' && method === 'POST') {
      state.writes.push({ method, path, body })
      const confirmation = { id: 714, ...body, confirmed_by_name: 'Mock 生产负责人', confirmed_at: '2026-11-15T04:00:00Z' }
      state.pack.briefing_confirmations.push(confirmation)
      return json(route, confirmation, 201)
    }
    if (path === '/preproduction/tech-packs/71/approval' && method === 'POST') {
      state.writes.push({ method, path, body })
      const approval = { id: 715, status: body.action === 'approve' ? 'approved' : 'rejected', reason: body.reason, decided_by_name: 'Mock 审批人', decided_at: '2026-11-15T05:00:00Z' }
      state.pack.approvals.push(approval)
      return json(route, approval, 201)
    }
    return json(route, { detail: `未配置 mock：${method} ${path}` }, 500)
  })
  return state
}

const nodes = [
  { id: 101, stage_name: 'Mock 裁剪', stage_type: 'cutting', sequence: 1 },
  { id: 102, stage_name: 'Mock 车缝', stage_type: 'sewing', sequence: 2 },
]
const routeInstance = { id: 11, code: 'MOCK-ROUTE', name: 'Mock 路线', production: 7, order: null, is_template: false, is_active: true, nodes }
const tickets = [
  { id: 31, production: 7, bed_no: 'MOCK-BED', bundle_no: 1, color: 'Mock 蓝', size: 'M', style_no: 'MOCK-STYLE', qty: 10 },
  { id: 32, production: 7, bed_no: 'MOCK-BED', bundle_no: 2, color: 'Mock 蓝', size: 'L', style_no: 'MOCK-STYLE', qty: 8 },
]
const employees = [
  { id: 1, user_id: 21, name: 'Mock 接收人甲', group_no: 1, post_name: '车缝', is_suspended: false },
  { id: 2, user_id: 22, name: 'Mock 接收人乙', group_no: 2, post_name: '车缝', is_suspended: false },
]
function handover(id = 501) {
  return {
    id, handover_no: `MOCK-HO-${id}`, batch_no: 'MOCK-BATCH-1', kind: 'cutting', status: 'pending',
    source_node_name: 'Mock 裁剪', target_node_name: 'Mock 车缝', receiver_name: 'Mock 接收人甲', target_group_no: 1,
    created_by_name: 'Mock 交出人', created_at: '2026-11-01T01:00:00Z', due_at: '2026-11-16T01:00:00Z',
    total_quantity: 18, received_quantity: 0, policy_mode: 'warn', timeout_minutes: 120, is_overdue: false, overdue_minutes: 0,
    has_discrepancy: false, reject_reason: '', remark: 'Mock 交接备注',
    items: tickets.map((ticket, index) => ({ id: 5011 + index, ticket: ticket.id, ticket_label: `MOCK-BED / ${ticket.bundle_no} 扎`, quantity: ticket.qty, received_quantity: ticket.qty, difference_type: null, reason: '' })),
    events: [],
  }
}
async function installHandover(page, permissions) {
  const state = { handover: handover(), writes: [], listQueries: [] }
  await installSession(page, permissions)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = pathOf(request)
    const method = request.method()
    const body = method === 'GET' ? null : request.postDataJSON()
    if (path === '/notifications') return json(route, pageOf([]))
    if (path === '/handovers/' && method === 'GET') {
      state.listQueries.push(new URL(request.url()).searchParams)
      return json(route, pageOf([state.handover]))
    }
    if (path === '/handovers/501/' && method === 'GET') return json(route, state.handover)
    if (path === '/handovers/batches/MOCK-BATCH-1/') return json(route, {
      batch_no: 'MOCK-BATCH-1', items: [state.handover],
      summary: { handover_count: 1, received_count: state.handover.status === 'received' ? 1 : 0, pending_count: state.handover.status === 'pending' ? 1 : 0, rejected_count: state.handover.status === 'rejected' ? 1 : 0, total_quantity: 18, received_quantity: state.handover.received_quantity },
    })
    if (path === '/handovers/501/receive' && method === 'POST') {
      state.writes.push({ method, path, body })
      const differences = body.items || []
      state.handover = { ...state.handover, status: 'received', received_quantity: differences.length ? differences.reduce((sum, item) => sum + item.received_quantity, state.handover.total_quantity - differences.reduce((sum, item) => sum + state.handover.items.find((row) => row.id === item.id).quantity, 0)) : state.handover.total_quantity, has_discrepancy: Boolean(differences.length), received_by_name: 'Mock 接收人甲', received_at: '2026-11-15T06:00:00Z', items: state.handover.items.map((item) => ({ ...item, ...(differences.find((row) => row.id === item.id) || {}) })) }
      return json(route, state.handover)
    }
    if (path === '/handovers/501/reject' && method === 'POST') {
      state.writes.push({ method, path, body })
      state.handover = { ...state.handover, status: 'rejected', reject_reason: body.reason }
      return json(route, state.handover)
    }
    if (path === '/handovers/501/reassign' && method === 'POST') {
      state.writes.push({ method, path, body })
      state.handover = { ...state.handover, receiver_name: 'Mock 接收人乙', receiver: body.receiver }
      return json(route, state.handover)
    }
    if (path === '/handovers/policies' && method === 'GET') return json(route, pageOf([{ id: 601, kind: 'cutting', mode: 'off', source_node: null, target_node: null, target_group_no: null, receiver: null, timeout_minutes: 120, updated_by_name: 'Mock 管理员' }]))
    if (path === '/handovers/policies/601' && method === 'PUT') {
      state.writes.push({ method, path, body })
      return json(route, { id: 601, ...body, updated_by_name: 'Mock 管理员' })
    }
    if (path === '/manufacturing/routes' && method === 'GET') return json(route, pageOf([routeInstance]))
    if (path === '/employees' && method === 'GET') return json(route, pageOf(employees))
    if (path === '/productions' && method === 'GET') return json(route, pageOf([{ id: 7, bed_no: 'MOCK-BED', order: null }]))
    if (path === '/tickets' && method === 'GET') return json(route, pageOf(tickets))
    if (path === '/manufacturing/routes/11/balances' && method === 'GET') return json(route, tickets.flatMap((ticket) => nodes.map((node) => ({ ticket_id: ticket.id, node_id: node.id, balance: { transferable: ticket.qty } }))))
    if (path === '/handovers/' && method === 'POST') {
      state.writes.push({ method, path, body })
      return json(route, { batch_no: 'MOCK-BATCH-NEW', items: body.targets.map((target, index) => ({ ...handover(502 + index), ...target, handover_no: `MOCK-HO-${502 + index}`, batch_no: 'MOCK-BATCH-NEW' })) }, 201)
    }
    if (path === '/handovers/batches/MOCK-BATCH-NEW/') return json(route, { batch_no: 'MOCK-BATCH-NEW', items: [], summary: { handover_count: 2, received_count: 0, pending_count: 2, rejected_count: 0, total_quantity: 18, received_quantity: 0 } })
    return json(route, { detail: `未配置 mock：${method} ${path}` }, 500)
  })
  return state
}

test('产前订单、状态、模板和工艺包真实接线，保存模板、工艺与新版本提交正确载荷', async ({ page }) => {
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  const state = await installPreproduction(page)
  await page.goto('/web/#preproduction')
  await expect(page.getByLabel('选择产前准备订单')).toHaveValue('7')
  await expect(page.locator('.preproduction-summary')).toContainText('MOCK-ORDER-7')
  await expect(page.locator('.preproduction-summary')).toContainText('工艺 V1 · MOCK-STYLE')
  await expect(page.locator('.preproduction-summary')).toContainText('待产前审批')
  await expect(page.locator('.preproduction-order-meta')).toContainText('Mock 客户')
  await expect(page.getByLabel('选择尺寸模板')).toContainText('Mock 尺寸模板')
  expect(state.queries[0].get('page_size')).toBe('200')

  const requirementsSection = page.locator('.preproduction-form-section').filter({ has: page.getByRole('heading', { name: '工艺要求 *', exact: true }) })
  await requirementsSection.getByLabel('选择工艺要求模板').selectOption('81')
  await expect(requirementsSection.locator('textarea')).toHaveValue('模板工艺要求')
  await requirementsSection.locator('textarea').fill('已接线的工艺要求')
  await requirementsSection.getByRole('button', { name: '存为模板', exact: true }).click()
  const templateDialog = page.getByRole('dialog', { name: '保存工艺文本模板' })
  await templateDialog.getByLabel('模板名称 *').fill('Mock 公司工艺模板')
  await templateDialog.getByRole('button', { name: '保存模板', exact: true }).click()
  await expect(templateDialog).toHaveCount(0)
  expect(state.writes[0]).toMatchObject({ method: 'POST', path: '/preproduction/templates', body: { kind: 'requirements', name: 'Mock 公司工艺模板', content: '已接线的工艺要求' } })

  await page.getByRole('button', { name: '保存工艺资料', exact: true }).click()
  await expect(page.locator('.preproduction-summary')).toContainText('工艺 V1')
  const update = state.writes.find((item) => item.method === 'PUT' && item.path === '/preproduction/tech-packs/71')
  expect(update.body).toMatchObject({ requirements: '已接线的工艺要求', pattern_no: 'MOCK-PAT-1', pattern_files: ['/media/mock-pattern.pdf'], tolerances: { mock: true } })
  expect(update.body.measurements[0]).toMatchObject({ size: 'M', point: '衣长', value: '68', tolerance_plus: '1', tolerance_minus: '1' })
  expect(update.body.attachments[0]).toMatchObject({ name: 'Mock 工艺单', path: '/media/mock-tech.pdf' })

  await page.getByRole('button', { name: '新建版本', exact: true }).click()
  await page.getByLabel('版本号（留空自动递增）').fill('2')
  await requirementsSection.locator('textarea').fill('第二版工艺要求')
  await page.getByRole('button', { name: '创建新工艺版本', exact: true }).click()
  await expect(page.locator('.preproduction-summary')).toContainText('工艺 V2')
  const create = state.writes.find((item) => item.method === 'POST' && item.path === '/preproduction/tech-packs')
  expect(create.body).toMatchObject({ order: 7, version_no: 2, requirements: '第二版工艺要求', tolerances: {} })
  expect(errors).toEqual([])
})

test('产前样追加、问题解决、班组交底和审批请求均由真实控件触发', async ({ page }) => {
  const state = await installPreproduction(page)
  page.on('dialog', (dialog) => dialog.accept())
  await page.goto('/web/#preproduction')
  await page.getByRole('button', { name: /2\s*产前样/ }).click()
  await expect(page.locator('.preproduction-history')).toContainText('首次样检')
  await page.getByLabel('追加检验结果').selectOption('passed')
  await page.getByLabel('检验说明 *').fill('复验合格')
  await page.getByRole('button', { name: '追加产前样结果', exact: true }).click()
  await expect(page.locator('.preproduction-current')).toContainText('合格')
  expect(state.writes.find((item) => item.path === '/preproduction/samples/711/records')).toMatchObject({ method: 'POST', body: { result: 'passed', note: '复验合格', attachments: [] } })

  await page.getByRole('button', { name: /3\s*问题处理/ }).click()
  await page.getByLabel('登记问题', { exact: true }).fill('袖窿需要复核')
  await page.getByRole('button', { name: '登记问题', exact: true }).click()
  await expect(page.locator('.preproduction-issues')).toContainText('袖窿需要复核')
  expect(state.writes.find((item) => item.path === '/preproduction/tech-packs/71/issues')).toMatchObject({ method: 'POST', body: { title: '袖窿需要复核', detail: '袖窿需要复核' } })
  const issue = page.locator('.preproduction-issues article').filter({ hasText: '领口尺寸需复核' })
  await issue.getByRole('button', { name: '填写处理结果并解决', exact: true }).click()
  const issueDialog = page.getByRole('dialog', { name: '填写处理结果' })
  await issueDialog.getByLabel('处理结果 *').fill('已复尺并修正纸样')
  await issueDialog.getByRole('button', { name: '确认', exact: true }).click()
  await expect(issue).toContainText('已解决')
  expect(state.writes.find((item) => item.path === '/preproduction/issues/712')).toMatchObject({ method: 'PUT', body: { action: 'resolve', resolution: '已复尺并修正纸样' } })

  await page.getByRole('button', { name: /4\s*交底审批/ }).click()
  await page.getByLabel('交底说明（可选）').fill('已向 Mock 班组交底')
  await page.getByRole('button', { name: '确认已完成班组交底', exact: true }).click()
  await expect(page.locator('.preproduction-confirmation').first()).toContainText('生产负责人已确认班组交底')
  expect(state.writes.find((item) => item.path === '/preproduction/tech-packs/71/briefing-confirmation')).toMatchObject({ method: 'POST', body: { note: '已向 Mock 班组交底' } })
  await page.getByRole('button', { name: '审批通过', exact: true }).click()
  const approvalDialog = page.getByRole('dialog', { name: '产前审批通过' })
  await approvalDialog.getByLabel('审批意见（可选）').fill('同意大货生产')
  await approvalDialog.getByRole('button', { name: '确认通过', exact: true }).click()
  await expect(page.locator('.preproduction-approval-panel')).toContainText('当前审批有效')
  expect(state.writes.find((item) => item.path === '/preproduction/tech-packs/71/approval')).toMatchObject({ method: 'POST', body: { action: 'approve', reason: '同意大货生产' } })
})

test('产前只读岗位隐藏维护、交底和审批写入口', async ({ page }) => {
  await installPreproduction(page, ['preproduction.view'])
  await page.goto('/web/#preproduction')
  await expect(page.locator('.preproduction-summary')).toContainText('MOCK-ORDER-7')
  await expect(page.getByRole('button', { name: '保存工艺资料', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '新建版本', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '存为模板', exact: true })).toHaveCount(0)
  await expect(page.locator('.preproduction-form-section textarea').first()).toBeDisabled()
  await page.getByRole('button', { name: /2\s*产前样/ }).click()
  await expect(page.getByRole('button', { name: '追加产前样结果', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: /3\s*问题处理/ }).click()
  await expect(page.getByRole('button', { name: '登记问题', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '填写处理结果并解决', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: /4\s*交底审批/ }).click()
  await expect(page.getByRole('button', { name: '确认已完成班组交底', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '审批通过', exact: true })).toHaveCount(0)
})

test('交接列表查询、批次汇总和全部接收核对筛选参数与幂等载荷', async ({ page }) => {
  const state = await installHandover(page)
  page.on('dialog', (dialog) => dialog.accept())
  await page.goto('/web/#handovers')
  const table = page.locator('.desktop-handover-table')
  await expect(table).toContainText('MOCK-HO-501')
  await expect(table).toContainText('Mock 裁剪')
  await expect(table).toContainText('18 / 0')
  expect(state.listQueries[0].get('kind')).toBe('cutting')
  expect(state.listQueries[0].get('box')).toBe('mine')
  expect(state.listQueries[0].get('status')).toBe('pending')
  await page.getByRole('button', { name: '我发起', exact: true }).click()
  await page.getByLabel('交接状态').selectOption('received')
  await expect.poll(() => state.listQueries.at(-1)?.get('status')).toBe('received')
  expect(state.listQueries.at(-1).get('box')).toBe('created')

  await table.getByRole('button', { name: '批次 MOCK-BATCH-1', exact: true }).click()
  const batchDialog = page.getByRole('dialog', { name: '批次汇总 · MOCK-BATCH-1' })
  await expect(batchDialog.locator('.batch-stats')).toContainText('交接单')
  await expect(batchDialog.locator('tbody')).toContainText('MOCK-HO-501')
  await batchDialog.getByRole('button', { name: '关闭', exact: true }).click()
  await table.getByRole('button', { name: '全部接收', exact: true }).click()
  await expect(table).toContainText('已接收')
  const receive = state.writes.find((item) => item.path === '/handovers/501/receive')
  expect(receive.method).toBe('POST')
  expect(receive.body.request_id).toMatch(/^[0-9a-f-]{36}$/)
  expect(receive.body.items).toBeUndefined()
})

test('交接详情差异接收提交展开项、实收数、差异类型和原因', async ({ page }) => {
  const state = await installHandover(page)
  await page.goto('/web/#handovers')
  await page.locator('.desktop-handover-table').getByRole('button', { name: '详情', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '交接详情 · MOCK-HO-501' })
  await expect(dialog.locator('.detail-ticket')).toHaveCount(2)
  await dialog.getByRole('button', { name: '有差异，逐项登记', exact: true }).click()
  const ticket = dialog.locator('.detail-ticket').first()
  await ticket.getByRole('button', { name: '登记差异', exact: true }).click()
  await ticket.getByLabel('实收数量').fill('8')
  await ticket.getByLabel('差异类型').selectOption('shortage')
  await ticket.getByLabel('差异原因（必填）').fill('清点短少两件')
  await dialog.getByLabel('整单差异说明（选填）').fill('按实物签收')
  await dialog.getByRole('button', { name: '确认差异并接收', exact: true }).click()
  await expect(dialog).toContainText('有差异')
  const write = state.writes.find((item) => item.path === '/handovers/501/receive')
  expect(write.method).toBe('POST')
  expect(write.body.reason).toBe('按实物签收')
  expect(write.body.items).toEqual([{ id: 5011, received_quantity: 8, difference_type: 'shortage', reason: '清点短少两件' }])
  expect(write.body.request_id).toMatch(/^[0-9a-f-]{36}$/)
})

test('交接拒收与改派分别提交原因、新接收人和独立幂等号', async ({ page }) => {
  const state = await installHandover(page)
  page.on('dialog', (dialog) => dialog.accept())
  await page.goto('/web/#handovers')
  await page.locator('.desktop-handover-table').getByRole('button', { name: '详情', exact: true }).click()
  let dialog = page.getByRole('dialog', { name: '交接详情 · MOCK-HO-501' })
  await dialog.getByRole('button', { name: '拒收', exact: true }).click()
  await dialog.getByLabel('拒收原因（必填）').fill('货项与交接单不符')
  await dialog.getByRole('button', { name: '确认拒收', exact: true }).click()
  await expect(dialog).toContainText('已拒收')
  const reject = state.writes.find((item) => item.path === '/handovers/501/reject')
  expect(reject).toMatchObject({ method: 'POST', body: { reason: '货项与交接单不符' } })
  expect(reject.body.request_id).toMatch(/^[0-9a-f-]{36}$/)
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()

  // 恢复待接收 mock 数据，独立验证同页的改派接线。
  state.handover = handover()
  await page.locator('.desktop-handover-table').getByRole('button', { name: '详情', exact: true }).click()
  dialog = page.getByRole('dialog', { name: '交接详情 · MOCK-HO-501' })
  await dialog.getByRole('button', { name: '改派', exact: true }).click()
  await expect(dialog.getByLabel('新接收人（在职）').locator('option')).toHaveCount(3)
  await dialog.getByLabel('新接收人（在职）').selectOption('22')
  await dialog.getByLabel('改派原因（必填）').fill('原接收人调岗')
  await dialog.getByRole('button', { name: '确认改派', exact: true }).click()
  await expect(dialog.locator('.detail-summary')).toContainText('Mock 接收人乙')
  const reassign = state.writes.find((item) => item.path === '/handovers/501/reassign')
  expect(reassign).toMatchObject({ method: 'POST', body: { receiver: 22, reason: '原接收人调岗' } })
  expect(reassign.body.request_id).toMatch(/^[0-9a-f-]{36}$/)
  expect(reassign.body.request_id).not.toBe(reject.body.request_id)
})

test('交接策略保存提交模式、节点、目标组、接收人和超时时间', async ({ page }) => {
  const state = await installHandover(page)
  await page.goto('/web/#handovers')
  await page.getByRole('button', { name: '交接策略', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '交接策略与超时' })
  await expect(dialog.getByLabel('策略模式')).toHaveValue('off')
  await dialog.getByLabel('策略模式').selectOption('strict')
  await dialog.getByLabel('交出节点（留空不限）').selectOption('101')
  await dialog.getByLabel('接收节点（留空不限）').selectOption('102')
  await dialog.getByLabel('目标组（选填）').selectOption('2')
  await dialog.getByLabel('指定接收人（选填）').selectOption('22')
  await dialog.getByLabel('超时时间（分钟）').fill('90')
  await dialog.getByRole('button', { name: '保存策略', exact: true }).click()
  await expect(dialog.getByText('最近保存：Mock 管理员', { exact: true })).toBeVisible()
  expect(state.writes.find((item) => item.path === '/handovers/policies/601')).toMatchObject({ method: 'PUT', body: { kind: 'cutting', mode: 'strict', source_node: 101, target_node: 102, target_group_no: 2, receiver: 22, timeout_minutes: 90 } })
})

test('一对多交接创建按目标分组菲票并提交数量、接收人和幂等号', async ({ page }) => {
  const state = await installHandover(page)
  await page.goto('/web/#handovers')
  await page.getByRole('button', { name: '批量发起', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '批量发起责任交接' })
  await dialog.getByLabel('实例路线 *').selectOption('11')
  await dialog.getByLabel('交出节点（裁剪）*').selectOption('101')
  await dialog.getByLabel('接收节点（后续）*').selectOption('102')
  await dialog.getByRole('button', { name: '添加目标', exact: true }).click()
  const targets = dialog.locator('.target-editor')
  await expect(targets).toHaveCount(2)
  await targets.nth(0).getByLabel('指定接收人').selectOption('21')
  await targets.nth(0).getByLabel('目标组').selectOption('1')
  await targets.nth(1).getByLabel('指定接收人').selectOption('22')
  await targets.nth(1).getByLabel('目标组').selectOption('2')
  await dialog.getByLabel('选择MOCK-BED / 1 扎 / Mock 蓝 / M').check()
  await dialog.getByLabel('MOCK-BED / 1 扎 / Mock 蓝 / M交接数量').fill('6')
  await dialog.getByLabel('MOCK-BED / 1 扎 / Mock 蓝 / M接收目标').selectOption('0')
  await dialog.getByLabel('选择MOCK-BED / 2 扎 / Mock 蓝 / L').check()
  await dialog.getByLabel('MOCK-BED / 2 扎 / Mock 蓝 / L交接数量').fill('8')
  await dialog.getByLabel('MOCK-BED / 2 扎 / Mock 蓝 / L接收目标').selectOption('1')
  await dialog.getByLabel('交接备注（选填）').fill('Mock 一对多交接')
  await dialog.getByRole('button', { name: '确认批量发起（2 个目标）', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '批次汇总 · MOCK-BATCH-NEW' })).toBeVisible()
  const create = state.writes.find((item) => item.method === 'POST' && item.path === '/handovers/')
  expect(create.body).toMatchObject({ kind: 'cutting', source_node: 101, target_node: 102, remark: 'Mock 一对多交接' })
  expect(create.body.request_id).toMatch(/^[0-9a-f-]{36}$/)
  expect(create.body.targets).toEqual([
    { receiver: 21, target_group_no: 1, items: [{ ticket: 31, quantity: 6 }] },
    { receiver: 22, target_group_no: 2, items: [{ ticket: 32, quantity: 8 }] },
  ])
})

test('交接受限岗位隐藏发起、策略、接收和改派入口', async ({ page }) => {
  await installHandover(page, ['handover.view'])
  await page.goto('/web/#handovers')
  await expect(page.locator('.desktop-handover-table')).toContainText('MOCK-HO-501')
  await expect(page.getByRole('button', { name: '批量发起', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '交接策略', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '全部接收', exact: true })).toHaveCount(0)
  await page.locator('.desktop-handover-table').getByRole('button', { name: '详情', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '交接详情 · MOCK-HO-501' })
  await expect(dialog.getByRole('button', { name: '拒收', exact: true })).toHaveCount(0)
  await expect(dialog.getByRole('button', { name: '改派', exact: true })).toHaveCount(0)
  await expect(dialog.getByRole('button', { name: '有差异，逐项登记', exact: true })).toHaveCount(0)
})
