import { test, expect } from '@playwright/test'

const session = {
  access: 'salary-mobile-fixture',
  companyId: 1,
  user: { id: 7, real_name: '测试员工' },
  companies: [{ id: 1, name: '测试工厂', post: { name: '管理员' } }],
}

const employee = {
  user_id: 7,
  real_name: '测试员工',
  post_name: '车缝',
  status: 1,
  status_display: '在职',
  salary_type: 'process',
  salary_type_display: '计件工资',
  settled: '120.00',
  unsettled: '36.00',
  piece_amount: '36.00',
  fixed_amount: '0.00',
  total_amount: '36.00',
  process_count: 1,
  no_price_count: 0,
  done_qty: 24,
  period_amount: '36.00',
}

const employeeDetail = {
  settled: '120.00',
  unsettled: '36.00',
  salary_type: 'process',
  salary_type_display: '计件工资',
  summary: { qty: 24, ticket_count: 2, processes: 1, no_price_count: 0, amount: '36.00' },
  fixed: { fixed_salary: '3000.00', fixed_type_display: '月薪', worked_days: 2, months: 1, amount: '3000.00' },
  by_date: [{ date: '2026-11-08', qty: 24, tickets: 2, amount: '36.00' }],
  by_bed: [{ production_id: 11, bed_no: 'B-01', style_no: 'S-01', style_name: '移动测试款', bed_total: 40, qty: 24, amount: '36.00', settled: '12.00' }],
  by_process: [{ process_id: 21, name: '车缝', qty: 24, amount: '36.00' }],
  by_style: [{ style_id: 31, style_no: 'S-01', style_name: '移动测试款', qty: 24, avg_price: '1.50', amount: '36.00' }],
}

const styles = [{ style_id: 31, style_no: 'S-01', style_name: '移动测试款', qty: 24, avg_price: '1.50', amount: '36.00', ticket_count: 2, process_count: 1 }]
const processes = [{ process_id: 21, name: '车缝', qty: 24, avg_price: '1.50', amount: '36.00', ticket_count: 2, users: 1 }]
const settlement = {
  id: 91,
  user_name: '测试员工',
  created_by_name: '管理员',
  created_at: '2026-11-08T10:00:00+08:00',
  period_start: '2026-11-01',
  period_end: '2026-11-08',
  employee_count: 1,
  amount: '36.00',
  status: 'settled',
  status_display: '已结算',
}
const apply = {
  id: 81,
  user_name: '测试员工',
  created_at: '2026-11-08T09:00:00+08:00',
  period_start: '2026-11-01',
  period_end: '2026-11-08',
  amount: '36.00',
  status: 0,
  status_display: '待审核',
  reviewer_hint: '待管理员审核',
  note: '移动卡片选择器验证',
  reject_reason: '',
  void_reason: '',
}
const payslip = {
  settlement,
  total: '36.00',
  rows: [{ user_id: 7, name: '测试员工', amount: '36.00', snapshot_amount: '36.00', items: [{ process: '车缝', qty: 24, amount: '36.00' }] }],
}

async function installApi(page, { personal = false } = {}) {
  const applyState = { ...apply }
  await page.addInitScript((value) => {
    localStorage.setItem('yisao.web.session.v1', JSON.stringify(value))
  }, session)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace(/^\/api/, '')
    const respond = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
    if (path === '/notifications') return respond({ count: 0, results: [] })
    if (path === '/salary/company/employees') return personal ? respond({ detail: '无权限' }, 403) : respond([employee])
    if (path === '/salary/company') return personal ? respond({ detail: '无权限' }, 403) : respond({
      settled: '120.00',
      unsettled: { qty: 24, amount: '36.00', users: 1, tickets: 2, processes: 1, no_price_processes: 0 },
      fixed_total: '0.00',
      whole_total: '0.00',
      commission_total: '0.00',
      total: '36.00',
    })
    if (path === '/salary/employee/7') {
      if (url.searchParams.get('tab') === 'count') {
        const count = {
          ...employeeDetail,
          summary: { qty: 24, ticket_count: 2, processes: 1, no_price_count: 0 },
          by_date: employeeDetail.by_date.map(({ date, qty, tickets }) => ({ date, qty, tickets })),
          by_bed: employeeDetail.by_bed.map(({ production_id, bed_no, style_no, style_name, bed_total, qty, settled }) => ({ production_id, bed_no, style_no, style_name, bed_total, qty, settled })),
          by_process: employeeDetail.by_process.map(({ process_id, name, qty }) => ({ process_id, name, qty })),
          by_style: employeeDetail.by_style.map((row) => ({ ...row, avg_price: '0.00', amount: '0.00' })),
        }
        return respond(count)
      }
      return respond(employeeDetail)
    }
    if (path === '/salary/summary/completion') {
      const level = url.searchParams.get('process_id') ? 3 : url.searchParams.get('style_id') ? 2 : 1
      return respond({
        styles,
        processes: level >= 2 ? processes : [],
        matrix: level === 3 ? [{ color: '测试蓝', sizes: [{ size: 'M', qty: 10 }, { size: 'L', qty: 14 }] }] : [],
        employees: level === 3 ? [{ user_id: 7, user_name: '测试员工', qty: 24, avg_price: '1.50', amount: '36.00' }] : [],
        tickets: level === 3 ? [{ ticket_id: 41, bed_no: 'B-01', bundle_no: 1, color: '测试蓝', size: 'M', qty: 24, price: '1.50', amount: '36.00' }] : [],
      })
    }
    if (path === '/salary/settlements/91/payslip') return respond(payslip)
    if (path === '/salary/settlements') return respond({ results: [settlement] })
    if (path === '/salary/settle-applies/81/review' && request.method() === 'PUT') {
      const approved = request.postDataJSON().action === 'approve'
      Object.assign(applyState, { status: approved ? 1 : 3, status_display: approved ? '待发放' : '已拒绝', reviewer_hint: approved ? '已审核，待管理员发放' : '' })
      return respond(applyState)
    }
    if (path === '/salary/settle-applies/81/pay' && request.method() === 'PUT') {
      Object.assign(applyState, { status: 2, status_display: '已发放', reviewer_hint: '', paid_at: '2026-11-08T10:30:00+08:00' })
      return respond(applyState)
    }
    if (path === '/salary/settle-applies') return respond({ results: [applyState] })
    if (path === '/salary/adjust') return respond({ results: [{ id: 71, created_at: '2026-11-08T08:00:00+08:00', user_name: '测试员工', amount: '-2.00', reason: '移动调整记录卡', operator_name: '管理员' }] })
    return respond({ detail: `未配置的夹具请求：${request.method()} ${path}` }, 500)
  })
}

async function expectNoOverflow(page, label) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), label).toBe(true)
}

test('手机端使用员工卡、业务卡、完成矩阵和薪资条，桌面表格不作为移动主布局', async ({ page }) => {
  test.setTimeout(45000)
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('dialog', (dialog) => dialog.accept())
  await page.setViewportSize({ width: 390, height: 844 })
  await installApi(page)
  await page.goto('/web/#salary')

  await expect(page.locator('.welcome h2')).toHaveText('薪酬核算')
  await expect(page.locator('.salary-overview-mobile .salary-summary-card')).toBeVisible()
  await expect(page.locator('.salary-employee-card')).toHaveCount(1)
  await expect(page.locator('.salary-view .salary-desktop').first()).toBeHidden()
  await expect(page.locator('.salary-employee-card')).toContainText('已结')
  await expect(page.locator('.salary-employee-card')).toContainText('固定工资')
  await expectNoOverflow(page, '390px 工资汇总不应横向溢出')

  await page.locator('.salary-employee-card').click()
  await expect(page.locator('.salary-detail-mobile')).toBeVisible()
  await expect(page.locator('.salary-detail-process')).toContainText('车缝')
  await expect(page.locator('.salary-detail-fixed')).toContainText('3,000.00')
  await page.locator('.modal-header .icon-button').click()

  await page.getByRole('button', { name: '完成情况', exact: true }).click()
  await expect(page.locator('.salary-completion-card')).toHaveCount(1)
  await page.locator('.salary-completion-card').click()
  await expect(page.locator('.salary-process-qty')).toContainText('24')
  await page.locator('.salary-completion-card').click()
  await expect(page.locator('.salary-matrix-card')).toContainText('测试蓝')
  await expect(page.locator('.salary-matrix-card')).toContainText('M')
  await expect(page.locator('.salary-completion-employee')).toContainText('测试员工')
  await expect(page.locator('.salary-completion-employee')).toContainText('1.50')
  await expect(page.locator('.salary-ticket-detail-card')).toContainText('B-01')
  await expectNoOverflow(page, '390px 完成矩阵不应横向溢出')
  await page.getByRole('button', { name: '‹ 工序列表', exact: true }).click()
  await expect(page.locator('.salary-process-qty')).toBeVisible()
  await page.getByRole('button', { name: '‹ 款式列表', exact: true }).click()
  await expect(page.locator('.salary-qty-badge')).toBeVisible()

  await page.getByRole('button', { name: '结算单', exact: true }).click()
  await expect(page.locator('.salary-settlement-card')).toContainText('#91')
  await page.locator('.salary-settlement-card').getByRole('button', { name: '薪资条', exact: true }).click()
  await expect(page.locator('.salary-slip-card')).toHaveCount(1)
  await expect(page.locator('.salary-slip-card')).toContainText('金额为结算快照')
  await expect(page.locator('.salary-slip-item')).toContainText('车缝')
  await expect(page.locator('.salary-payslip-desktop')).toBeHidden()
  await expectNoOverflow(page, '390px 薪资条不应横向溢出')
  await page.locator('.modal-header .icon-button').click()

  await page.getByRole('button', { name: '结算申请', exact: true }).click()
  await expect(page.locator('.salary-apply-card')).toContainText('移动卡片选择器验证')
  await expect(page.locator('.salary-apply-card').getByRole('button', { name: '通过', exact: true })).toBeVisible()
  await expect(page.locator('.salary-apply-card').getByRole('button', { name: '作废', exact: true })).toBeVisible()
  await page.locator('.salary-apply-card').getByRole('button', { name: '通过', exact: true }).click()
  await expect(page.locator('.salary-apply-card')).toContainText('待发放')
  await expect(page.locator('.salary-apply-card').getByRole('button', { name: '通过', exact: true })).toHaveCount(0)
  await page.locator('.salary-apply-card').getByRole('button', { name: '发放', exact: true }).click()
  await expect(page.locator('.salary-apply-card')).toContainText('已发放')
  await expect(page.locator('.salary-apply-card').getByRole('button', { name: '发放', exact: true })).toHaveCount(0)
  await expect(page.locator('.salary-apply-card').getByRole('button', { name: '作废', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '调整记录', exact: true }).click()
  await expect(page.locator('.salary-change-card')).toContainText('移动调整记录卡')

  await page.setViewportSize({ width: 320, height: 740 })
  await expectNoOverflow(page, '320px 调整记录不应横向溢出')
  await page.getByRole('button', { name: '工资汇总', exact: true }).click()
  await expect(page.locator('.salary-employee-card')).toBeVisible()
  await expectNoOverflow(page, '320px 员工卡不应横向溢出')
  expect(errors).toEqual([])
})

test('个人手机端工资、计数、固定工资与三种汇总视图使用真实员工接口字段', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 })
  await installApi(page, { personal: true })
  await page.goto('/web/#salary')

  await expect(page.locator('.welcome h2')).toHaveText('我的薪酬')
  await expect(page.locator('.salary-date-row')).toContainText('36.00')
  await page.getByRole('button', { name: '按裁床查看', exact: true }).click()
  await expect(page.locator('.salary-bed-card')).toContainText('B-01')
  await expect(page.locator('.salary-bed-card')).toContainText('已结算')
  await page.getByRole('button', { name: '汇总查看', exact: true }).click()
  await expect(page.locator('.salary-style-card')).toContainText('S-01')

  await page.getByRole('button', { name: '计数', exact: true }).click()
  await expect(page.locator('.salary-style-card .salary-amount')).toHaveCount(0)
  await page.getByRole('button', { name: '按日期查看', exact: true }).click()
  await expect(page.locator('.salary-date-row .salary-amount')).toHaveCount(0)
  await expect(page.locator('.salary-date-row')).toContainText('24 件 / 2 扎')

  await page.getByRole('button', { name: '固定工资', exact: true }).click()
  await expect(page.locator('.salary-fixed-card')).toContainText('3,000.00')
  await expect(page.locator('.salary-fixed-card')).toContainText('2 天')
  await expectNoOverflow(page, '360px 个人固定工资不应横向溢出')
})

test('桌面仍保留复杂写链路使用的表格行、操作按钮和薪资条 tbody 选择器', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await installApi(page)
  await page.goto('/web/#salary')

  await page.getByRole('button', { name: '结算申请', exact: true }).click()
  const applyRow = page.locator('.desktop-table tbody tr, .table-card tbody tr').filter({ hasText: apply.note })
  await expect(applyRow).toHaveCount(1)
  await expect(applyRow.getByRole('button', { name: '通过', exact: true })).toBeVisible()
  await expect(page.locator('.salary-apply-card')).toBeHidden()

  await page.getByRole('button', { name: '结算单', exact: true }).click()
  const settlementRow = page.locator('.table-card tbody tr').filter({ hasText: '#91' })
  await expect(settlementRow).toHaveCount(1)
  await settlementRow.getByRole('button', { name: '薪资条', exact: true }).click()
  const payslipDialog = page.locator('.modal').filter({ has: page.getByRole('heading', { name: '薪资条', exact: true }) })
  await expect(payslipDialog.locator('.spinner')).toHaveCount(0)
  const payslipRow = payslipDialog.locator('tbody tr').filter({ hasText: '车缝' })
  await expect(payslipRow).toHaveCount(1)
  await expect(payslipRow).toContainText('车缝 24 件 / ¥36.00')
  await expect(page.locator('.salary-payslip-mobile')).toBeHidden()
})
