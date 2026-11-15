import { test, expect } from '@playwright/test'

const phone = process.env.WEB_PHONE
const password = process.env.WEB_PASSWORD

test.beforeEach(async () => {
  test.skip(!phone || !password, '设置 WEB_PHONE/WEB_PASSWORD 后运行隔离现金流问报测试')
})

test('真实隔离后端：统一经营现金流设计器执行同一受治理计划并导出', async ({ page }) => {
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/web/#ai')
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.topbar h1')).not.toHaveText('登录')
  await page.getByRole('tab', { name: '报表设计器', exact: true }).click()
  await expect(page.locator('.designer-loading')).toHaveCount(0)
  await page.getByLabel('数据集', { exact: true }).selectOption('finance_cash_flow')
  await expect(page.getByLabel('业务月', { exact: true })).toBeChecked()
  await page.getByRole('button', { name: '本月', exact: true }).click()
  const [response] = await Promise.all([
    page.waitForResponse((item) => item.url().endsWith('/api/ai/report/execute') && item.request().method() === 'POST'),
    page.getByRole('button', { name: '执行查询', exact: true }).click(),
  ])
  expect(response.status()).toBe(200)
  expect((await response.json()).plan.dataset).toBe('finance_cash_flow')
  await expect(page.locator('.dynamic-report-table thead')).toContainText('流入合计')
  await expect(page.locator('.dynamic-report-table thead')).toContainText('流出合计')
  await expect(page.locator('.dynamic-report-table thead')).toContainText('净现金流')
  await expect(page.locator('.query-scope')).toContainText('永久排除作废或撤销流水')
  await expect(page.getByText('本报表不是利润表', { exact: false }).first()).toBeVisible()
  const exportButton = page.getByRole('button', { name: '同步导出', exact: true })
  if (await exportButton.count()) {
    const downloadEvent = page.waitForEvent('download')
    await exportButton.click()
    expect((await downloadEvent).suggestedFilename()).toMatch(/\.xlsx$/i)
  }
  expect(errors).toEqual([])
})
