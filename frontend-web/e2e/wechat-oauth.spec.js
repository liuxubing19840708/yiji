import { test, expect } from '@playwright/test'

const config = {
  enabled: true,
  authorization_url: 'https://open.weixin.qq.com/connect/qrconnect?appid=web-test&redirect_uri=http%3A%2F%2F127.0.0.1%3A8000%2Fweb%2F&response_type=code&scope=snsapi_login&state=signed-state#wechat_redirect',
}

test('后端已配置时启用微信按钮并真实跳转官方授权 URL', async ({ page }) => {
  let configRequests = 0
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/auth/wechat-web') {
      configRequests += 1
      return route.fulfill({ json: { ...config, authorization_url: config.authorization_url.replace('signed-state', `signed-state-${configRequests}`) } })
    }
    return route.fulfill({ json: {} })
  })
  await page.route('https://open.weixin.qq.com/**', (route) => route.fulfill({
    contentType: 'text/html', body: '<title>Mock WeChat OAuth</title>',
  }))
  await page.goto('/web/')
  const button = page.getByRole('button', { name: '微信一键登录', exact: true })
  await expect(button).toBeEnabled()
  await button.click()
  await expect(page.getByRole('alert')).toContainText('请先阅读并同意')
  expect(page.url()).toContain('/web/')
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await button.click()
  await page.waitForURL('https://open.weixin.qq.com/**')
  const url = new URL(page.url())
  expect(url.hostname).toBe('open.weixin.qq.com')
  expect(url.pathname).toBe('/connect/qrconnect')
  expect(url.searchParams.get('appid')).toBe('web-test')
  expect(url.searchParams.get('redirect_uri')).toBe('http://127.0.0.1:8000/web/')
  expect(url.searchParams.get('state')).toBe('signed-state-2')
  expect(configRequests).toBe(2)
})

test('OAuth 回跳调用登录接口、清除敏感参数并进入现有公司选择', async ({ page }) => {
  let loginBody
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/auth/wechat-web/login') {
      loginBody = request.postDataJSON()
      return route.fulfill({ json: { access: 'web-access', refresh: 'web-refresh', user: { id: 8, real_name: '微信用户' } } })
    }
    if (path === '/api/companies/mine') return route.fulfill({ json: [] })
    return route.fulfill({ json: {} })
  })
  await page.goto('/web/?code=mock-code&state=mock-state&companyNo=100001')
  await expect(page.getByRole('heading', { name: '加入公司', exact: true })).toBeVisible()
  expect(loginBody).toEqual({ code: 'mock-code', state: 'mock-state' })
  expect(page.url()).toBe('http://127.0.0.1:8000/web/?companyNo=100001')
  expect(page.url()).not.toContain('mock-code')
  expect(page.url()).not.toContain('mock-state')
  await expect(page.getByPlaceholder('请输入公司编号')).toHaveValue('100001')
})
