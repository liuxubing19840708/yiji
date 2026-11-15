import { test, expect } from '@playwright/test'

const phone = process.env.WEB_PHONE
const password = process.env.WEB_PASSWORD

test.beforeEach(async () => {
  test.skip(!phone || !password, '由 Django 隔离 LiveServer 提供 WEB_PHONE/WEB_PASSWORD 后运行')
})

test('真实隔离后端：生产运营筛选与包装入库冲销关联流水', async ({ page }) => {
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/web/#manufacturing-flow')
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.topbar h1')).not.toHaveText('登录')

  await page.getByRole('button', { name: /运营看板/ }).click()
  await page.getByLabel('筛选款号', { exact: true }).fill('LIVE-S1')
  const [operationsResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/manufacturing/operations?') && new URL(response.url()).searchParams.get('style_no') === 'LIVE-S1' && response.request().method() === 'GET'),
    page.getByRole('button', { name: '查询', exact: true }).click(),
  ])
  expect(operationsResponse.status()).toBe(200)
  expect(new URL(operationsResponse.url()).searchParams.get('style_no')).toBe('LIVE-S1')
  await expect(page.locator('.operations-dashboard')).toContainText('LIVE-ROUTE')
  await expect(page.locator('.operations-dashboard')).toContainText('床次 #')

  await page.getByRole('button', { name: /包装入库/ }).click()
  await expect(page.locator('.document-card').first()).toContainText('真实HTTP包装冲销')
  await expect(page.locator('.document-card').nth(1)).toContainText('真实HTTP入库冲销')
  await page.getByText('查看箱内菲票、床款色码及冲销关联流水', { exact: true }).click()
  await expect(page.locator('.document-card').first().locator('.ledger-detail')).toContainText('冲销原流水 #')
  await page.getByText('查看入库菲票、床款色码及冲销关联流水', { exact: true }).click()
  await expect(page.locator('.document-card').nth(1).locator('.ledger-detail')).toContainText('冲销原流水 #')
  expect(errors).toEqual([])
})
