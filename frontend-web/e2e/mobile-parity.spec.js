import { test, expect } from '@playwright/test'

const phone = process.env.WEB_PHONE
const password = process.env.WEB_PASSWORD
const routes = [
  ['dashboard', '工作台'], ['scan', '扫菲'], ['records', '计件记录'],
  ['batch', '批量登记'], ['notifications', '通知'], ['ai', 'AI 问报'],
  ['orders', '订单管理'], ['production', '生产床次'], ['manufacturing-flow', '全流程生产'],
  ['preproduction', '产前准备'], ['handovers', '交接中心'], ['shipments', '出货装箱'],
  ['quality', '质量管理'], ['outsource', '外发管理'], ['salary', '薪酬结算'],
  ['finance', '应收财务'], ['costs', '成本损益'], ['quotations', '报价管理'],
  ['warehouse', '采购仓库'], ['tech', '物料与 BOM'], ['master', '基础档案'],
  ['organization', '员工与岗位'], ['settings', '系统设置'],
  ['working', '工作中'], ['kanban', '生产看板'], ['assigned', '指定工序'],
  ['printing', '打印管理'], ['company-qr', '公司二维码'],
  ['profile', '个人资料'], ['help', '使用帮助'], ['finance/payables', '资金管理'],
]

test.beforeEach(async ({ page }) => {
  test.skip(!phone || !password, '设置 WEB_PHONE/WEB_PASSWORD 后运行隔离环境浏览器测试')
})

test('全部业务模块在常见手机宽度逐页渲染且无页面级横向溢出', async ({ page }) => {
  test.setTimeout(120000)
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/web/')
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.today-strip')).toBeVisible()

  for (const width of [390, 360, 320]) {
    await page.setViewportSize({ width, height: 844 })
    for (const [route, heading] of routes) {
      await page.evaluate((key) => { location.hash = key }, route)
      await expect(page.locator('.topbar h1')).toHaveText(heading)
      await expect(page.locator('.content .spinner')).toHaveCount(0, { timeout: 15000 })
      await expect(page.locator('.content .error-state')).toHaveCount(0)
      expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), `${route} @ ${width}px 页面横向溢出`).toBe(false)
      await expect(page.locator('.bottom-nav')).toBeVisible()
      await expect(page.locator('.bottom-nav .ai-fab')).toBeVisible()
      if (width === 390) {
        await page.screenshot({ path: `test-results/mobile/${route}.png`, fullPage: true })
      }
    }
  }
  expect(errors).toEqual([])
})

test('普通员工首页只显示工序、我的薪酬和工作中单组', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(() => {
    localStorage.setItem('yisao.web.session.v1', JSON.stringify({
      access: 'layout-test', companyId: 1, user: { id: 9, real_name: '测试员工' },
      companies: [{ id: 1, name: '隔离工厂', status: 1, company_no: '100001', post: { name: '车工', is_admin: false, permissions: [] } }],
    }))
  })
  await page.route('**/api/records/workbench**', (route) => route.fulfill({ json: { beds: [], my_today: [], my_today_amount: '0' } }))
  await page.route('**/api/orders**', (route) => route.fulfill({ json: { count: 0, results: [] } }))
  await page.route('**/api/notifications**', (route) => route.fulfill({ json: { count: 0, results: [] } }))
  await page.goto('/web/#dashboard')
  await expect(page.locator('.module-group')).toHaveCount(1)
  await expect(page.locator('.module-group h3')).toHaveText('我的工作')
  await expect(page.locator('.module-grid button')).toHaveCount(3)
  await expect(page.locator('.side-nav')).not.toContainText('订单管理')
  await expect(page.locator('.side-nav')).not.toContainText('应收财务')
  for (const label of ['工序', '我的薪酬', '工作中']) await expect(page.locator('.module-grid').getByRole('button', { name: label, exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/mobile/staff-dashboard.png', fullPage: true })
})

test('首页独立入口直接到达对应页面与业务页签', async ({ page }) => {
  test.setTimeout(120000)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/web/')
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.module-group')).toHaveCount(7)
  const entries = [
    ['orders', '订单管理'], ['quotations', '报价管理'], ['production', '生产床次', '生产管理'],
    ['kanban', '生产看板'], ['assigned', '指定工序'], ['working', '工作中'],
    ['master/styles', '款式管理'], ['tech/boms', 'BOM管理'], ['tech/materials', '物料管理'],
    ['batch', '批量登记', '批量代登'], ['scan', '扫菲', '协助扫菲'], ['quality', '质量管理', '质检管理'], ['outsource', '外发管理'],
    ['master/processes', '工序'], ['handovers', '交接中心'], ['salary/overview', '薪酬结算', '薪酬管理'], ['salary/mine', '我的薪酬'],
    ['salary/orders', '订单薪酬'], ['salary/completion', '完成汇总'], ['finance/receivables', '应收管理'],
    ['warehouse/lots', '轻量仓库'], ['warehouse/issues', '按床领料'], ['warehouse/receipts', '暂收IQC'],
    ['warehouse/suppliers', '供应商'], ['shipments', '出货装箱', '出货管理'], ['costs/costs', '成本管理'],
    ['finance/payables', '资金管理'],
    ['warehouse/trace', '质量追溯'], ['printing', '打印管理'], ['settings/audit', '日志管理'],
    ['settings/client', '客户端日志'], ['master/customers', '客户管理'], ['organization/employees', '员工与岗位', '员工管理'],
    ['company-qr', '公司二维码'],
  ]
  for (const [route, title, buttonLabel = title] of entries) {
    await expect(page.locator('.topbar h1')).toHaveText('工作台')
    await expect(page.locator('.content .spinner')).toHaveCount(0, { timeout: 15000 })
    await page.locator('.mini-dashboard').getByRole('button', { name: buttonLabel, exact: true }).click()
    await expect(page.locator('.topbar h1')).toHaveText(title)
    await expect(page.locator('.content .spinner')).toHaveCount(0, { timeout: 15000 })
    await expect(page.locator('.content .error-state')).toHaveCount(0)
    if (route.includes('/')) {
      const [key, section] = route.split('/')
      if (key === 'warehouse') await expect(page.locator('.warehouse-mobile .warehouse-tabs button.active')).toBeVisible()
      if (key === 'tech') await expect(page.locator('.tech-mobile-tabs button.active')).toBeVisible()
      if (key === 'master') {
        if (['customers', 'groups'].includes(section)) await expect(page.locator('.admin-master-tabs button.active')).toBeVisible()
        else await expect(page.locator('.admin-master-tabs')).toHaveCount(0)
      }
      if (key === 'salary' && section !== 'overview') {
        await expect(page.locator('.salary-main-tabs')).toHaveCount(0)
        await expect(page.locator('.welcome h2')).toHaveText(section === 'mine' ? '我的薪酬' : section === 'orders' ? '订单薪酬' : '完成汇总')
      }
      if (route === 'salary/completion') {
        await expect(page.getByRole('button', { name: '申请结算', exact: true })).toHaveCount(0)
        await expect(page.getByRole('button', { name: '发起结算', exact: true })).toHaveCount(0)
      }
      if (key === 'settings') {
        await expect(page.locator('.admin-settings-tabs')).toHaveCount(0)
        await expect(page.locator('.welcome h2')).toHaveText(section === 'audit' ? '操作日志' : section === 'client' ? '客户端日志' : section === 'settings' ? '系统设置' : '公司信息')
      }
    }
    await page.screenshot({ path: `test-results/mobile/entry-${route.replace('/', '-')}.png`, fullPage: true })
    await page.evaluate(() => { location.hash = 'dashboard' })
  }
})

test('手机业务新建与编辑弹窗完整可用且无页面级横向溢出', async ({ page }) => {
  test.setTimeout(120000)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/web/')
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.today-strip')).toBeVisible()
  const dialogs = [
    ['orders', '新建订单'], ['production', '新建床次'],
    ['shipments', '新建出货单'], ['quality', '登记返修'],
    ['outsource', '新建外发单'], ['salary', '申请结算'],
    ['finance', '新建应收'], ['quotations', '新建报价'],
    ['warehouse', '新增批次'], ['tech', '新增物料'],
    ['master', '新建款式'], ['organization', '邀请员工'],
  ]
  for (const [route, action] of dialogs) {
    await page.evaluate((key) => { location.hash = key }, route)
    const button = page.locator('.content').getByRole('button', { name: action, exact: true })
    await expect(button).toBeVisible()
    await button.click()
    const modal = page.locator('.modal-backdrop .modal').last()
    await expect(modal).toBeVisible()
    await expect(modal.locator('.spinner')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), `${route} 弹窗页面横向溢出`).toBe(false)
    await page.screenshot({ path: `test-results/mobile/dialog-${route}.png` })
    const primaryAction = modal.locator('.form-actions .button.primary, form .button.primary').last()
    if (await primaryAction.count()) {
      await modal.evaluate((element) => { element.scrollTop = element.scrollHeight })
      await primaryAction.scrollIntoViewIfNeeded()
      await expect(primaryAction).toBeInViewport()
      await page.screenshot({ path: `test-results/mobile/dialog-${route}-bottom.png` })
    }
    const close = modal.locator('.modal-header .icon-button, [aria-label="关闭"]').first()
    await expect(close).toBeVisible()
    await close.click()
    await expect(modal).toHaveCount(0)
  }
})
