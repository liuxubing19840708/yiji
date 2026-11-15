import { test, expect } from '@playwright/test'

const phone = process.env.WEB_PHONE
const password = process.env.WEB_PASSWORD

test.beforeEach(async () => {
  test.skip(!phone || !password, '设置 WEB_PHONE/WEB_PASSWORD 后运行隔离资金操作测试')
})

async function login(page, hash) {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`/web/#${hash}`)
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.topbar h1')).not.toHaveText('登录')
}

test('真实隔离后端：应付、关联付款、余额回退、收支作废与现金流', async ({ page }) => {
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  const stamp = Date.now()
  const supplier = `隔离界面供应商-${stamp}`
  const paymentRef = `WEB-AP-${stamp}`

  await login(page, 'finance/payables')
  await page.getByRole('button', { name: '新建应付', exact: true }).click()
  let dialog = page.getByRole('dialog', { name: '新建采购应付' })
  await dialog.getByLabel('供应商名称', { exact: true }).fill(supplier)
  await dialog.getByLabel('应付金额（元）').fill('100')
  await dialog.getByLabel('备注', { exact: true }).fill('真实隔离界面验收')
  const [createdResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith('/api/finance/payables') && response.request().method() === 'POST'),
    dialog.getByRole('button', { name: '建立应付', exact: true }).click(),
  ])
  expect(createdResponse.status()).toBe(201)
  const payable = await createdResponse.json()
  const detail = page.getByRole('dialog', { name: `应付详情 ${payable.payable_no}` })
  await expect(detail).toContainText('¥100.00')

  await detail.getByRole('button', { name: '登记付款', exact: true }).click()
  dialog = page.getByRole('dialog', { name: '登记供应商付款' })
  await expect(dialog.getByLabel('关联应付单（可选）')).toHaveValue(String(payable.id))
  await dialog.getByLabel('付款金额（元）').fill('40')
  await dialog.getByLabel('外部流水号（可选）').fill(paymentRef)
  const [paymentResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith('/api/finance/supplier-payments') && response.request().method() === 'POST'),
    dialog.getByRole('button', { name: '确认付款', exact: true }).click(),
  ])
  expect(paymentResponse.status()).toBe(201)
  await page.getByPlaceholder('搜索应付单号').fill(payable.payable_no)
  await page.getByRole('button', { name: '查询', exact: true }).click()
  let row = page.getByRole('table', { name: '采购应付列表' }).locator('tbody tr').filter({ hasText: payable.payable_no })
  await expect(row).toContainText('¥40.00')
  await expect(row).toContainText('¥60.00')
  await expect(row).toContainText('部分付款')

  await page.goto('/web/#finance/payments')
  const paymentRow = page.getByRole('table', { name: '供应商付款列表' }).locator('tbody tr').filter({ hasText: paymentRef })
  await expect(paymentRow).toContainText(payable.payable_no)
  await paymentRow.getByRole('button', { name: `作废 ${supplier} 付款 40.00 元`, exact: true }).click()
  dialog = page.getByRole('dialog', { name: '作废供应商付款' })
  await dialog.getByLabel('作废原因（必填）').fill('隔离界面验收作废，保留付款事实')
  await dialog.getByRole('button', { name: '确认作废', exact: true }).click()
  await expect(paymentRow).toContainText('已作废')

  await page.goto('/web/#finance/payables')
  await page.getByPlaceholder('搜索应付单号').fill(payable.payable_no)
  await page.getByRole('button', { name: '查询', exact: true }).click()
  row = page.getByRole('table', { name: '采购应付列表' }).locator('tbody tr').filter({ hasText: payable.payable_no })
  await expect(row).toContainText('¥100.00')
  await expect(row).toContainText('未付')
  await row.getByRole('button', { name: '详情', exact: true }).click()
  page.once('dialog', (browserDialog) => browserDialog.accept())
  await page.getByRole('dialog', { name: `应付详情 ${payable.payable_no}` }).getByRole('button', { name: '作废应付', exact: true }).click()
  await page.getByRole('button', { name: '查询', exact: true }).click()
  await expect(page.getByText('暂无应付记录，可点击「新建应付」登记', { exact: true })).toBeVisible()

  await page.goto('/web/#finance/entries')
  const expenseRef = `WEB-EXP-${stamp}`
  await page.getByRole('button', { name: '登记费用支出', exact: true }).click()
  dialog = page.getByRole('dialog', { name: '登记费用支出' })
  await dialog.getByLabel('类别', { exact: true }).fill('隔离验收水电费')
  await dialog.getByLabel('金额（元）').fill('20')
  await dialog.getByLabel('外部凭证号（可选）').fill(expenseRef)
  await dialog.getByRole('button', { name: '确认登记', exact: true }).click()
  let entryRow = page.getByRole('table', { name: '费用支出与其他收入列表' }).locator('tbody tr').filter({ hasText: expenseRef })
  await expect(entryRow).toContainText('¥20.00')
  await entryRow.getByRole('button', { name: /^作废收支 / }).click()
  dialog = page.getByRole('dialog', { name: '作废收支记录' })
  await dialog.getByLabel('作废原因（必填）').fill('隔离界面验收作废费用')
  await dialog.getByRole('button', { name: '确认作废', exact: true }).click()
  await expect(entryRow).toContainText('已作废')

  const incomeRef = `WEB-INC-${stamp}`
  await page.getByRole('button', { name: '登记其他收入', exact: true }).click()
  dialog = page.getByRole('dialog', { name: '登记其他收入' })
  await dialog.getByLabel('类别', { exact: true }).fill('隔离验收利息收入')
  await dialog.getByLabel('金额（元）').fill('5')
  await dialog.getByLabel('外部凭证号（可选）').fill(incomeRef)
  await dialog.getByRole('button', { name: '确认登记', exact: true }).click()
  entryRow = page.getByRole('table', { name: '费用支出与其他收入列表' }).locator('tbody tr').filter({ hasText: incomeRef })
  await expect(entryRow).toContainText('¥5.00')
  await entryRow.getByRole('button', { name: /^作废收支 / }).click()
  dialog = page.getByRole('dialog', { name: '作废收支记录' })
  await dialog.getByLabel('作废原因（必填）').fill('隔离界面验收作废收入')
  await dialog.getByRole('button', { name: '确认作废', exact: true }).click()
  await expect(entryRow).toContainText('已作废')

  await page.goto('/web/#finance/cashflow')
  await page.getByLabel('开始日期', { exact: true }).fill('2026-11-01')
  await page.getByLabel('结束日期', { exact: true }).fill('2026-11-11')
  await page.getByLabel('现金流汇总周期').selectOption('day')
  await page.getByRole('button', { name: '查询现金流', exact: true }).click()
  await expect(page.getByRole('table', { name: '经营现金流按日明细' })).toBeVisible()
  await expect(page.getByRole('note')).toContainText('经营现金流不是利润表')
  expect(errors).toEqual([])
})

test('真实隔离后端：无结算工资实付、详情与按原因作废', async ({ page }) => {
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  const paymentRef = `WEB-SALARY-${Date.now()}`
  await login(page, 'salary/payments')
  await page.getByRole('button', { name: '登记实付', exact: true }).click()
  const create = page.getByRole('dialog', { name: '登记实付工资' })
  await create.getByRole('combobox', { name: '员工', exact: true }).selectOption({ index: 1 })
  await create.getByLabel('实付金额（元）').fill('35.50')
  await create.getByLabel('外部流水号', { exact: true }).fill(paymentRef)
  await create.getByRole('combobox', { name: '支付方式', exact: true }).selectOption('cash')
  await create.getByLabel('无结算支付（不抵扣任何结算单）').check()
  const [response] = await Promise.all([
    page.waitForResponse((item) => item.url().endsWith('/api/salary/payments') && item.request().method() === 'POST'),
    create.getByRole('button', { name: '确认登记实付', exact: true }).click(),
  ])
  expect(response.status()).toBe(201)

  const row = page.locator('.payment-desktop tbody tr').filter({ hasText: paymentRef })
  await expect(row).toContainText('35.50')
  await row.getByRole('button', { name: '详情', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '支付详情' })).toContainText(paymentRef)
  await page.getByRole('dialog', { name: '支付详情' }).getByRole('button', { name: '关闭', exact: true }).last().click()
  await row.getByRole('button', { name: '作废', exact: true }).click()
  const voidDialog = page.getByRole('dialog', { name: '作废支付' })
  await expect(voidDialog.getByLabel('作废原因', { exact: true })).toHaveAttribute('required', '')
  await voidDialog.getByLabel('作废原因', { exact: true }).fill('隔离界面验收作废，保留工资实付事实')
  await voidDialog.getByRole('button', { name: '确认作废', exact: true }).click()
  await expect(row).toContainText('已作废')
  await row.getByRole('button', { name: '详情', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '支付详情' })).toContainText('隔离界面验收作废，保留工资实付事实')
  expect(errors).toEqual([])
})
