import { test, expect } from '@playwright/test'

const company = { id: 1, name: '隔离测试公司', company_no: '100001', status: 1, status_display: '在职', post: { name: '管理员', is_admin: true, permissions: ['*'] } }

test('协议不预勾，短信发送与登录调用真实接口并执行 60 秒倒计时', async ({ page }) => {
  const calls = []
  await page.clock.install()
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    calls.push({ path, method: request.method(), body: request.postDataJSON() })
    if (path === '/api/auth/sms-code') return route.fulfill({ json: { message: '验证码已发送' } })
    if (path === '/api/auth/login-sms') return route.fulfill({ json: { access: 'sms-access', refresh: 'sms-refresh', user: { id: 1, real_name: '短信用户' } } })
    if (path === '/api/companies/mine') return route.fulfill({ json: [company] })
    if (path === '/api/companies/1/switch') return route.fulfill({ json: { message: '已切换', company } })
    return route.fulfill({ json: { count: 0, results: [] } })
  })
  await page.goto('/web/')
  const agreement = page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' })
  await expect(page.getByRole('button', { name: '微信一键登录', exact: true })).toBeDisabled()
  await expect(page.getByText('Web 未配置微信 OAuth', { exact: false })).toBeVisible()
  await expect(agreement).not.toBeChecked()
  await page.getByPlaceholder('请输入手机号').fill('13800138000')
  await page.getByPlaceholder('请输入密码').fill('not-a-demo-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('请先阅读并同意')
  expect(calls.filter((item) => item.path === '/api/auth/login')).toHaveLength(0)

  await page.getByRole('button', { name: '《隐私政策》', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '隐私政策' })).toContainText('信息使用与保护')
  await page.getByRole('button', { name: '我已阅读', exact: true }).click()
  await page.getByRole('button', { name: '《用户协议》', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '用户协议' })).toContainText('账号与公司')
  await page.getByRole('button', { name: '我已阅读', exact: true }).click()

  await page.getByRole('button', { name: '验证码登录', exact: true }).click()
  await agreement.check()
  await page.getByRole('button', { name: '获取验证码', exact: true }).click()
  await expect(page.getByRole('button', { name: '60 秒后重发', exact: true })).toBeDisabled()
  await page.clock.runFor(61000)
  await expect(page.getByRole('button', { name: '获取验证码', exact: true })).toBeEnabled()
  await page.getByPlaceholder('请输入验证码').fill('123456')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.app-shell')).toBeVisible()
  expect(calls.find((item) => item.path === '/api/auth/sms-code')).toMatchObject({ method: 'POST', body: { phone: '13800138000' } })
  expect(calls.find((item) => item.path === '/api/auth/login-sms')).toMatchObject({ method: 'POST', body: { phone: '13800138000', code: '123456' } })
})

test('个人资料真实请求浏览器通知权限，注销需双重确认并保留后端拒绝', async ({ page }) => {
  const deactivateBodies = []
  await page.addInitScript(() => {
    localStorage.setItem('yisao.web.session.v1', JSON.stringify({
      access: 'profile-test', refresh: 'profile-refresh', companyId: 1,
      user: { id: 1, real_name: '资料用户', phone: '13800138000' },
      companies: [{ id: 1, name: '隔离测试公司', company_no: '100001', status: 1, post: { name: '管理员', is_admin: true, permissions: ['*'] } }],
    }))
    window.__notificationRequests = 0
    Object.defineProperty(window, 'Notification', {
      configurable: true,
      value: {
        permission: 'default',
        requestPermission() {
          window.__notificationRequests += 1
          this.permission = 'granted'
          return Promise.resolve('granted')
        },
      },
    })
  })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/auth/me') return route.fulfill({ json: { id: 1, real_name: '资料用户', phone: '13800138000', gender: 0, birthday: '', avatar: '' } })
    if (path === '/api/auth/deactivate') {
      deactivateBodies.push(request.postDataJSON())
      if (deactivateBodies.length === 1) return route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ detail: '密码校验失败，无法注销' }) })
      return route.fulfill({ json: { message: '账号已注销' } })
    }
    return route.fulfill({ json: { count: 0, results: [] } })
  })
  await page.goto('/web/#profile')
  await expect(page.locator('.welcome h2')).toHaveText('个人资料')
  await page.getByRole('button', { name: '请求浏览器通知权限', exact: true }).click()
  await expect(page.getByText('浏览器已允许通知', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => window.__notificationRequests)).toBe(1)
  await expect(page.getByText('不会开通微信订阅消息', { exact: false })).toBeVisible()

  await page.getByRole('button', { name: '注销账号', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '注销账号确认' })
  await expect(dialog).toContainText('第 1 步，共 2 步')
  await dialog.getByRole('button', { name: '继续注销', exact: true }).click()
  await expect(dialog).toContainText('第 2 步，共 2 步')
  await dialog.getByRole('button', { name: '确认永久注销', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('密码校验失败')
  await expect(page.locator('.app-shell')).toBeVisible()
  await dialog.getByPlaceholder('设密码账号必填；无密码账号可留空').fill('current-secret')
  await dialog.getByRole('button', { name: '确认永久注销', exact: true }).click()
  await expect(page.getByRole('heading', { name: '登录工作台', exact: true })).toBeVisible()
  expect(deactivateBodies).toEqual([{}, { password: 'current-secret' }])
  expect(await page.evaluate(() => localStorage.getItem('yisao.web.session.v1'))).toBeNull()
  await expect(page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' })).not.toBeChecked()
})
