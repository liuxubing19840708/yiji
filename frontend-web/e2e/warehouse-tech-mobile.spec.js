import { test, expect } from '@playwright/test'

const phone = process.env.WEB_PHONE
const password = process.env.WEB_PASSWORD

test.beforeEach(async () => {
  test.skip(!phone || !password, '设置 WEB_PHONE/WEB_PASSWORD 后运行隔离环境移动信息架构回归')
})

async function login(page, route = 'warehouse/lots') {
  await page.goto(`/web/#${route}`)
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.topbar h1')).not.toHaveText('登录')
}

test('仓库真实子入口使用小程序移动 IA，桌面欢迎和大表不混入手机页', async ({ page }) => {
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.setViewportSize({ width: 390, height: 844 })
  await login(page)

  const sections = [
    ['lots', '新增批次'],
    ['issues', '＋ 新建领料'],
    ['receipts', '新建暂收'],
    ['suppliers', '新增供应商'],
  ]
  for (const [section, action] of sections) {
    await page.evaluate((key) => { location.hash = `warehouse/${key}` }, section)
    await expect(page.locator('.warehouse-mobile')).toBeVisible()
    await expect(page.locator('.warehouse-desktop')).toBeHidden()
    await expect(page.locator('.warehouse-mobile .warehouse-tabs button.active')).toBeVisible()
    await expect(page.locator('.warehouse-mobile').getByRole('button', { name: action, exact: true })).toBeVisible()
    if (['receipts', 'issues'].includes(section)) await expect(page.locator('.warehouse-mobile-chips')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), `warehouse/${section} 横向溢出`).toBe(false)
  }

  await page.evaluate(() => { location.hash = 'warehouse/trace' })
  await expect(page.locator('.warehouse-trace-modes button.active')).toHaveText('款色反查')
  await expect(page.locator('.warehouse-trace-filter')).toBeVisible()
  await expect(page.locator('.warehouse-desktop')).toBeHidden()
  expect(errors).toEqual([])
})

test('物料和 BOM 手机卡片、chips、底部编辑表单可用', async ({ page }) => {
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.setViewportSize({ width: 390, height: 844 })
  await login(page, 'tech/materials')
  await expect(page.locator('.tech-mobile')).toBeVisible()
  await expect(page.locator('.tech-desktop')).toBeHidden()
  await expect(page.locator('.tech-mobile-chips')).toBeVisible()
  await page.locator('.tech-mobile').getByRole('button', { name: '新增物料', exact: true }).click()
  const materialDialog = page.getByRole('dialog', { name: '新增物料', exact: true })
  await expect(materialDialog).toBeVisible()
  await expect(materialDialog.getByRole('button', { name: '保存物料', exact: true })).toBeAttached()
  await materialDialog.getByRole('button', { name: '关闭', exact: true }).click()

  await page.evaluate(() => { location.hash = 'tech/boms' })
  await expect(page.locator('.tech-mobile-note')).toBeVisible()
  await expect(page.locator('.tech-mobile-chips')).toBeVisible()
  await page.locator('.tech-mobile').getByRole('button', { name: '新建 BOM', exact: true }).click()
  const bomDialog = page.getByRole('dialog', { name: '新建 BOM', exact: true })
  await expect(bomDialog).toBeVisible()
  await expect(bomDialog.getByRole('button', { name: '添加明细', exact: true })).toBeVisible()
  await expect(bomDialog.getByRole('button', { name: '保存 BOM', exact: true })).toBeAttached()
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
  expect(errors).toEqual([])
})

test('桌面采购创建和明细选择器保持原契约', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await login(page, 'warehouse/purchases')
  await expect(page.locator('.warehouse-desktop')).toBeVisible()
  await expect(page.locator('.warehouse-mobile')).toBeHidden()
  await page.locator('.warehouse-desktop').getByRole('button', { name: '新增采购单', exact: true }).click()
  const editor = page.getByRole('dialog', { name: '采购单', exact: true })
  await expect(editor.getByLabel('供应商名称（合同为文本字段）')).toBeVisible()
  await expect(editor.getByLabel('面料 / 物料名称 *')).toBeVisible()
  await expect(editor.getByLabel('数量 *')).toBeVisible()
  await expect(editor.getByRole('button', { name: '添加明细', exact: true })).toBeVisible()
  await expect(editor.getByRole('button', { name: '创建采购单', exact: true })).toBeVisible()
  await editor.getByRole('button', { name: '关闭', exact: true }).first().click()
})
