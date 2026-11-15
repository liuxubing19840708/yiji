import { test, expect } from '@playwright/test'

const phone = process.env.WEB_PHONE
const password = process.env.WEB_PASSWORD

test.beforeEach(async ({ page }) => {
  test.skip(!phone || !password, '设置 WEB_PHONE/WEB_PASSWORD 后运行隔离环境浏览器测试')
})

test('桌面登录与七个业务模块可用', async ({ page }) => {
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/web/')
  await expect(page.getByRole('heading', { name: '登录工作台' })).toBeVisible()
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.today-strip').getByText('今日预估工资', { exact: true })).toBeVisible()
  await expect(page.locator('.spinner')).toHaveCount(0)
  await page.screenshot({ path: 'test-results/web-desktop-dashboard.png', fullPage: true })
  for (const [nav, text] of [['订单管理', '订单管理'], ['生产床次', '生产床次'], ['扫菲', '扫菲登记'], ['薪酬结算', '薪酬核算'], ['通知', '消息通知'], ['AI 问报', 'AI 问报']]) {
    await page.locator('.side-nav').getByRole('button', { name: nav === '通知' ? /^通知/ : nav, exact: nav !== '通知' }).click()
    await expect(page.locator('.welcome h2')).toHaveText(text)
    await expect(page.locator('.content .spinner')).toHaveCount(0)
    await expect(page.locator('.content .error-state')).toHaveCount(0)
  }
  await page.locator('.side-nav').getByRole('button', { name: '生产床次', exact: true }).click()
  const firstBed = page.locator('.desktop-table tbody tr').first()
  if (await firstBed.count()) {
    await firstBed.click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByRole('dialog').locator('.spinner')).toHaveCount(0)
    await page.getByRole('button', { name: '关闭', exact: true }).click()
  }
  await page.locator('.side-nav').getByRole('button', { name: '扫菲', exact: true }).click()
  await page.getByRole('button', { name: '床次＋扎号', exact: true }).click()
  await expect(page.getByPlaceholder('例如：C001')).toBeVisible()
  const ticket = await page.evaluate(async () => {
    const session = JSON.parse(localStorage.getItem('yisao.web.session.v1'))
    const headers = { Authorization: `Bearer ${session.access}`, 'X-Company-Id': String(session.companyId) }
    const productions = await (await fetch('/api/productions?page_size=1', { headers })).json()
    const production = productions.results?.[0]
    if (!production) return null
    const bundles = await (await fetch(`/api/productions/${production.id}/bundles`, { headers })).json()
    const bundle = bundles.results?.[0]
    return bundle ? { bed_no: production.bed_no, bundle_no: bundle.bundle_no } : null
  })
  if (ticket) {
    await page.getByPlaceholder('例如：C001').fill(ticket.bed_no)
    await page.getByPlaceholder('例如：1').fill(ticket.bundle_no)
    await page.getByRole('button', { name: '查询菲票', exact: true }).click()
    await expect(page.locator('#register-card select[required]')).toBeVisible()
    await expect(page.locator('#register-card select[required] option').nth(1)).toBeAttached()
    await expect(page.locator('#register-card .form-error')).toHaveCount(0)
  }
  expect(errors).toEqual([])
})

test('全部 ERP 模块可进入且无脚本错误', async ({ page }) => {
  test.setTimeout(120000)
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/web/')
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.today-strip').getByText('今日预估工资', { exact: true })).toBeVisible()
  const routes = [
    ['records', '计件记录', '计件记录'], ['batch', '批量登记', '批量协助登记'],
    ['notifications', '通知', '消息通知'], ['ai', 'AI 问报', 'AI 问报'],
    ['orders', '订单管理', '订单管理'], ['production', '生产床次', '生产床次'],
    ['shipments', '出货装箱', '出货管理'], ['quality', '质量管理', '质检管理'],
    ['outsource', '外发管理', '委外加工'], ['salary', '薪酬结算', '薪酬核算'],
    ['finance', '应收财务', '财务管理'], ['costs', '成本损益', '成本核算'],
    ['quotations', '报价管理', '报价管理'], ['warehouse', '采购仓库', '仓库与追溯'],
    ['tech', '物料与 BOM', '工艺与 BOM'], ['master', '基础档案', '基础档案'],
    ['organization', '员工与岗位', '组织系统'], ['settings', '系统设置', '公司与设置'],
  ]
  for (const [route, navHeading, pageHeading] of routes) {
    await page.evaluate((key) => { location.hash = key }, route)
    await expect(page.locator('.topbar h1')).toHaveText(navHeading)
    await expect(page.locator('.welcome h2')).toHaveText(pageHeading)
    await expect(page.locator('.content .spinner')).toHaveCount(0, { timeout: 15000 })
    await expect(page.locator('.content .error-state')).toHaveCount(0)
  }
  expect(errors).toEqual([])
})

test('桌面侧栏可进入新增独立工作区', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/web/')
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.today-strip')).toBeVisible()
  for (const [nav, title] of [['生产看板', '生产看板'], ['工作中', '工作中'], ['指定工序', '指定工序'], ['打印管理', '打印管理'], ['公司二维码', '公司二维码']]) {
    await page.locator('.side-nav').getByRole('button', { name: nav, exact: true }).click()
    await expect(page.locator('.topbar h1')).toHaveText(title)
    await expect(page.locator('.content .spinner')).toHaveCount(0, { timeout: 15000 })
    await expect(page.locator('.content .error-state')).toHaveCount(0)
  }
  await expect(page.locator('.company-qr-card img')).toBeVisible()
})

test('手机布局与小程序导航一致，宫格和我的入口可用且无横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/web/')
  await page.screenshot({ path: 'test-results/web-mobile-login.png' })
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.today-strip').getByText('今日预估工资', { exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/web-mobile-dashboard-viewport.png' })
  const bottomNav = page.locator('.bottom-nav')
  await expect(bottomNav).toBeVisible()
  await expect(bottomNav.locator('button')).toHaveCount(5)
  await expect(bottomNav.getByRole('button', { name: 'AI问报', exact: true }).locator('.ai-fab')).toBeVisible()
  await expect(page.locator('.module-group')).toHaveCount(7)
  await page.screenshot({ path: 'test-results/web-mobile-dashboard.png', fullPage: true })
  for (const [nav, text] of [['扫菲', '扫菲登记'], ['AI问报', 'AI 问报'], ['通知', '消息通知']]) {
    await bottomNav.getByRole('button', { name: nav === '通知' ? /^通知/ : nav, exact: nav !== '通知' }).click()
    await expect(page.locator('.welcome h2')).toHaveText(text)
    await expect(page.locator('.content .spinner')).toHaveCount(0)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)
    expect(overflow).toBe(false)
  }
  await bottomNav.getByRole('button', { name: '首页', exact: true }).click()
  await page.locator('.mini-dashboard').getByRole('button', { name: '订单管理', exact: true }).click()
  await expect(page.locator('.welcome h2')).toHaveText('订单管理')
  await bottomNav.getByRole('button', { name: '我的', exact: true }).click()
  const profile = page.getByLabel('我的与全部功能')
  await expect(profile).toBeVisible()
  await expect(profile.getByLabel('当前工厂')).toBeVisible()
  await page.screenshot({ path: 'test-results/web-mobile-profile.png', fullPage: true })
  await expect(profile.getByRole('button', { name: '退出登录', exact: true })).toBeInViewport()
  await profile.getByRole('button', { name: /全部功能/ }).click()
  const lastFunction = profile.getByRole('button', { name: '客户端日志', exact: true })
  await expect(lastFunction).toBeVisible()
  await lastFunction.scrollIntoViewIfNeeded()
  await expect(lastFunction).toBeInViewport()
  await page.screenshot({ path: 'test-results/web-mobile-functions.png' })
  await profile.getByRole('button', { name: '薪酬管理', exact: true }).click()
  await expect(page.locator('.welcome h2')).toHaveText('薪酬核算')
  await expect(profile).toHaveCount(0)
  await expect(page.locator('.content .spinner')).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
  const tabs = page.locator('.filter-bar > .segmented').first()
  await expect(tabs.getByRole('button', { name: '结算申请', exact: true })).toBeInViewport()
  await page.screenshot({ path: 'test-results/web-mobile-salary.png', fullPage: true })
})
