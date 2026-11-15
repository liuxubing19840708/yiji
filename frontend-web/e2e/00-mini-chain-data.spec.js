import { test, expect } from '@playwright/test'

const phone = process.env.WEB_PHONE
const password = process.env.WEB_PASSWORD
const list = (data) => Array.isArray(data) ? data : (data?.results || [])
const money = (value) => `¥${Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

async function login(page) {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/web/')
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.stats-grid')).toBeVisible()
}

async function api(page, path, params = {}) {
  const result = await page.evaluate(async ({ path, params }) => {
    const session = JSON.parse(localStorage.getItem('yisao.web.session.v1'))
    const query = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== '' && value !== null && value !== undefined) query.set(key, String(value))
    })
    const response = await fetch(`/api${path}${query.size ? `?${query}` : ''}`, {
      headers: { Authorization: `Bearer ${session.access}`, 'X-Company-Id': String(session.companyId) },
    })
    const data = await response.json()
    return { status: response.status, data }
  }, { path, params })
  expect(result.status, `${path}: ${JSON.stringify(result.data)}`).toBe(200)
  return result.data
}

async function openRoute(page, route) {
  await page.evaluate((key) => { location.hash = key }, route)
  await expect(page.locator('.content .spinner')).toHaveCount(0, { timeout: 15000 })
  await expect(page.locator('.content .error-state')).toHaveCount(0)
}

async function setRangeAndQuery(page, chain, selector = '.salary-filters') {
  const form = page.locator(selector)
  await form.locator('input[type="date"]').first().fill(chain.dateFrom)
  await form.locator('input[type="date"]').nth(1).fill(chain.dateTo)
  await form.getByRole('button', { name: /查询/ }).click()
  await expect(page.locator('.content .spinner')).toHaveCount(0, { timeout: 15000 })
}

async function readChain(page) {
  const orders = list(await api(page, '/orders', { page_size: 100 }))
  const order = orders.find((item) => item.order_no.startsWith('XD86-'))
  expect(order, '未找到小程序全链路 XD86 订单').toBeTruthy()
  const prefix = order.order_no.slice(0, -3)
  const dateTo = order.created_at.slice(0, 10)
  const dateFrom = `${dateTo.slice(0, 8)}01`
  const range = { date_from: dateFrom, date_to: dateTo }
  const [styles, productions, processes, quotes, boms, materials] = await Promise.all([
    api(page, '/styles', { page_size: 100 }),
    api(page, '/productions', { page_size: 100 }),
    api(page, '/processes', { page_size: 100 }),
    api(page, '/quotation/quotations', { page_size: 100 }),
    api(page, '/tech/boms', { page_size: 100 }),
    api(page, '/tech/materials', { page_size: 100 }),
  ])
  const style = list(styles).find((item) => item.id === order.style)
  const production = list(productions).find((item) => item.order === order.id)
  expect(style).toBeTruthy()
  expect(production).toBeTruthy()
  const [bundles, records, completion, employees, settlements, costs, pnl, shipments, receivables, lots, purchases, stockIns, issues, valuation, trace, payslip] = await Promise.all([
    api(page, `/productions/${production.id}/bundles`, { expand: 1 }),
    api(page, '/records', { ...range, page_size: 100 }),
    api(page, '/salary/summary/completion', range),
    api(page, '/salary/company/employees', range),
    api(page, '/salary/settlements', { ...range, page_size: 100 }),
    api(page, '/cost/orders', { ...range, keyword: order.order_no, page_size: 100 }),
    api(page, '/cost/orders/pnl', { ...range, keyword: order.order_no, page_size: 100 }),
    api(page, '/shipments', { page_size: 100 }),
    api(page, '/finance/receivables', { keyword: order.order_no, page_size: 100 }),
    api(page, '/lots', { keyword: prefix, page_size: 100 }),
    api(page, '/purchase-orders', { page_size: 100 }),
    api(page, '/stock-ins', { page_size: 100 }),
    api(page, '/material-issues', { page_size: 100 }),
    api(page, '/finance/stock-valuation', { date_to: dateTo }),
    api(page, '/trace/lot', { keyword: prefix }),
    api(page, '/salary/settlements/1/payslip'),
  ])
  return {
    prefix, order, style, production, dateFrom, dateTo,
    processes: list(processes).filter((item) => item.name.startsWith(prefix)),
    quote: list(quotes).find((item) => item.style === style.id),
    bom: list(boms).find((item) => item.style === style.id),
    materials: list(materials).filter((item) => item.code.startsWith(prefix)),
    bundles: list(bundles), records: list(records), recordCount: records.count,
    completion, employees, settlements: list(settlements), payslip,
    cost: list(costs)[0], pnl: list(pnl)[0], shipment: list(shipments)[0],
    receivable: list(receivables)[0], lots: list(lots), purchases: list(purchases),
    stockIns: list(stockIns), issues: list(issues), valuation: valuation.rows,
    trace: list(trace),
  }
}

test.beforeEach(async ({ page }) => {
  test.skip(!phone || !password, '设置 WEB_PHONE/WEB_PASSWORD 后运行隔离环境浏览器测试')
  await login(page)
})

test('01 导入数据 API 全链路数量和金额闭合', async ({ page }) => {
  const chain = await readChain(page)
  expect(chain.prefix).toMatch(/^XD86-/)
  expect(chain.style.ticket_count).toBe(8)
  expect(chain.style.cut_total).toBe(200)
  expect(chain.style.process_count).toBe(3)
  expect(chain.processes).toHaveLength(3)
  expect(chain.order.total_qty).toBe(200)
  expect(chain.order.registered_qty).toBe(540)
  expect(chain.order.progress).toBe(100)
  expect(chain.production.ticket_count).toBe(8)
  expect(chain.production.done_ticket_count).toBe(6)
  expect(chain.bundles).toHaveLength(8)
  expect(chain.bundles.reduce((sum, item) => sum + Number(item.qty), 0)).toBe(200)
  expect(chain.recordCount).toBe(20)
  expect(chain.records.reduce((sum, item) => sum + Number(item.qty), 0)).toBe(540)
  const settledRecords = chain.records.filter((item) => item.settlement)
  expect(settledRecords.length).toBeGreaterThan(0)
  expect(settledRecords.every((item) => item.user_name === 'E2E员工一')).toBe(true)
  expect(settledRecords.reduce((sum, item) => sum + Number(item.amount), 0)).toBe(585)
  expect(chain.records.reduce((sum, item) => sum + Number(item.amount), 0)).toBe(1205)
  expect(chain.completion.styles[0].qty).toBe(540)
  expect(chain.completion.styles[0].amount).toBe('1205.00')
  expect(chain.completion.styles[0].ticket_count).toBe(7)
  expect(chain.completion.styles[0].process_count).toBe(3)
  const employeeOne = chain.employees.find((item) => item.real_name === 'E2E员工一')
  const employeeTwo = chain.employees.find((item) => item.real_name === 'E2E员工二')
  expect(employeeOne).toMatchObject({ done_qty: 270, settled: '585.00', unsettled: '0.00' })
  expect(employeeTwo).toMatchObject({ done_qty: 270, settled: '0.00', unsettled: '620.00' })
  expect(chain.settlements).toHaveLength(1)
  expect(chain.payslip.total).toBe('585.00')
  expect(chain.cost).toMatchObject({ revenue: '5920.00', material_cost: '5689.60', labor_cost: '1205.00', overhead_cost: '100.00', total_cost: '6994.60' })
  expect(chain.pnl).toMatchObject({ ar_amount: '3520', ar_received: '3520', ar_gap: '0', real_profit: '-3474.60' })
  expect(chain.shipment.total_qty).toBe(120)
  expect(chain.receivable).toMatchObject({ amount: '3520.00', received_amount: '3520.00', status: 'settled' })
  expect(chain.lots).toHaveLength(3)
  expect(chain.stockIns).toHaveLength(2)
  expect(chain.issues).toHaveLength(2)
  expect(chain.issues.every((item) => item.status === 'confirmed')).toBe(true)
  expect(chain.trace.find((item) => item.lot_no.endsWith('-L1'))).toMatchObject({ production_count: 1, ticket_count: 8, qty_total: 200 })
  const fabric = chain.valuation.find((item) => item.material_code.endsWith('-M1'))
  const accessory = chain.valuation.find((item) => item.material_code.endsWith('-M2'))
  expect(fabric).toMatchObject({ balance_qty: '1235.60', issued_qty: '264.40' })
  expect(accessory).toMatchObject({ balance_qty: '99.20', issued_qty: '200.80' })
})

test('02 桌面款式工序物料 BOM 与报价展示一致', async ({ page }) => {
  const chain = await readChain(page)
  await openRoute(page, 'master/styles')
  const styleRow = page.locator('.desktop-table tbody tr').filter({ hasText: chain.style.style_no })
  await expect(styleRow).toContainText('¥6.50')
  await expect(styleRow.locator('td').nth(3)).toHaveText('3')
  await expect(styleRow.locator('td').nth(4)).toHaveText('8')

  await openRoute(page, 'master/processes')
  const processRows = page.locator('.desktop-table tbody tr').filter({ hasText: chain.prefix })
  await expect(processRows).toHaveCount(3)
  for (const [name, price] of [['裁剪', '¥2.50'], ['车缝', '¥3.00'], ['锁边', '¥1.00']]) {
    await expect(processRows.filter({ hasText: name })).toContainText(price)
  }

  await openRoute(page, 'tech/materials')
  await expect(page.locator('.desktop-table tbody tr').filter({ hasText: `${chain.prefix}M1` })).toContainText('参数齐全')
  await expect(page.locator('.desktop-table tbody tr').filter({ hasText: `${chain.prefix}M2` })).toContainText('辅料')
  await openRoute(page, 'tech/boms')
  const bomRow = page.locator('.desktop-table tbody tr').filter({ hasText: chain.style.style_no })
  await expect(bomRow).toContainText('v1')
  await expect(bomRow).toContainText('生效')
  await bomRow.click()
  const bomDialog = page.getByRole('dialog', { name: `${chain.style.style_no} BOM 详情` })
  await expect(bomDialog.locator('tbody tr')).toHaveCount(2)
  await expect(bomDialog).toContainText('1.2000 米')
  await expect(bomDialog).toContainText('6.00%')
  await expect(bomDialog).toContainText('1.0000 个')
  await bomDialog.getByRole('button', { name: '关闭', exact: true }).click()

  await openRoute(page, 'quotations')
  const quoteDetail = await api(page, `/quotation/quotations/${chain.quote.id}`)
  const quoteRow = page.locator('.desktop-table tbody tr').filter({ hasText: chain.quote.quote_no })
  await expect(quoteRow).toContainText('200')
  await expect(quoteRow).toContainText('4800.00')
  await expect(quoteRow).toContainText('4819.44')
  await quoteRow.click()
  const quoteDialog = page.locator('.modal-backdrop .modal')
  await expect(quoteDialog).toContainText(chain.style.style_no)
  await expect(quoteDialog).toContainText('材料合计（元）')
  await expect(quoteDialog.locator('.quotation-material-table tbody tr')).toHaveCount(quoteDetail.materials.length)
  for (const material of quoteDetail.materials) {
    await expect(quoteDialog.locator('.quotation-material-table')).toContainText(material.material_code || material.material_name)
  }
})

test('03 桌面订单床次八扎菲票与二十条计件一致', async ({ page }) => {
  const chain = await readChain(page)
  await openRoute(page, 'orders')
  const orderRow = page.locator('.desktop-table tbody tr').filter({ hasText: chain.order.order_no })
  await expect(orderRow.locator('td').nth(2)).toHaveText('200')
  await expect(orderRow).toContainText('100.0%')
  await orderRow.click()
  const orderDialog = page.getByRole('dialog', { name: `订单 ${chain.order.order_no}` })
  await expect(orderDialog.locator('.order-progress-stats')).toContainText('200')
  await expect(orderDialog.locator('.order-progress-stats')).toContainText('540')
  await expect(orderDialog.locator('.order-progress-stats')).toContainText('100.0%')
  await orderDialog.getByRole('button', { name: '关闭', exact: true }).click()

  await openRoute(page, 'production')
  const productionRow = page.locator('.desktop-table tbody tr').filter({ hasText: chain.production.bed_no })
  await expect(productionRow.locator('td').nth(2)).toHaveText('200')
  await expect(productionRow).toContainText('6/8')
  await expect(productionRow.locator('td').nth(4)).toHaveText('540')
  await productionRow.click()
  const productionDialog = page.getByRole('dialog', { name: `床次 ${chain.production.bed_no}` })
  await expect(productionDialog.locator('.desktop-detail-bundles tbody tr')).toHaveCount(8)
  await expect(productionDialog.locator('.detail-grid')).toContainText('8 扎')
  await expect(productionDialog.locator('.detail-grid')).toContainText('6 扎')
  await expect(productionDialog.locator('.detail-grid')).toContainText('540')
  await productionDialog.getByRole('button', { name: '关闭', exact: true }).click()

  await openRoute(page, 'records')
  await setRangeAndQuery(page, chain, '.records-desktop-filters')
  const recordRows = page.locator('.records-table-card .desktop-table tbody tr')
  await expect(recordRows).toHaveCount(20)
  await expect(page.locator('.records-table-card .desktop-table .badge.settled')).toHaveCount(chain.records.filter((item) => item.settlement).length)
  await expect(page.locator('.records-table-card .desktop-table .badge.producing')).toHaveCount(chain.records.filter((item) => !item.settlement).length)
  await expect(recordRows.filter({ hasText: 'E2E员工一' })).toHaveCount(chain.records.filter((item) => item.user_name === 'E2E员工一').length)
  await expect(recordRows.filter({ hasText: 'E2E员工二' })).toHaveCount(chain.records.filter((item) => item.user_name === 'E2E员工二').length)
})

test('04 桌面工资完成汇总结算单和薪资条一致', async ({ page }) => {
  test.setTimeout(120000)
  const chain = await readChain(page)
  await openRoute(page, 'salary')
  await setRangeAndQuery(page, chain)
  const employeeRows = page.locator('.salary-desktop.table-card tbody tr')
  const first = employeeRows.filter({ hasText: 'E2E员工一' })
  const second = employeeRows.filter({ hasText: 'E2E员工二' })
  await expect(first.locator('td').nth(1)).toHaveText('270')
  await expect(first.locator('td').nth(2)).toHaveText('¥0.00')
  await expect(first.locator('td').nth(3)).toContainText('¥0.00')
  await expect(first.locator('td').nth(4)).toHaveText('¥0.00')
  await expect(second.locator('td').nth(1)).toHaveText('270')
  await expect(second.locator('td').nth(2)).toHaveText('¥620.00')
  await expect(second.locator('td').nth(3)).toContainText('¥620.00')
  await expect(second.locator('td').nth(4)).toHaveText('¥620.00')
  await first.click()
  const detail = page.locator('.salary-modal')
  await expect(detail.locator('.detail-grid')).toContainText('270')
  await expect(detail.locator('.detail-grid')).toContainText('¥0.00')
  await detail.locator('.icon-button').click()

  await openRoute(page, 'salary/completion')
  await setRangeAndQuery(page, chain)
  const completionStyle = page.locator('.salary-desktop.table-card tbody tr').filter({ hasText: chain.style.style_no })
  await expect(completionStyle.locator('td').nth(1)).toHaveText('540')
  await expect(completionStyle).toContainText('¥1,205.00')
  await expect(completionStyle.locator('td').nth(4)).toHaveText('7 / 3')
  await completionStyle.click()
  const processSummary = await api(page, '/salary/summary/completion', {
    date_from: chain.dateFrom, date_to: chain.dateTo, style_id: chain.completion.styles[0].style_id,
  })
  const sewing = processSummary.processes.find((item) => item.name.endsWith('车缝'))
  expect(sewing).toBeTruthy()
  const completionProcesses = page.locator('.salary-desktop.table-card tbody tr')
  await expect(completionProcesses).toHaveCount(3)
  const sewingRow = completionProcesses.filter({ hasText: '车缝' })
  await expect(sewingRow.locator('td').nth(1)).toHaveText(String(sewing.qty))
  await expect(sewingRow.locator('td').nth(2)).toContainText(money(sewing.amount))
  await sewingRow.click()
  const employeeSummary = await api(page, '/salary/summary/completion', {
    date_from: chain.dateFrom, date_to: chain.dateTo,
    style_id: chain.completion.styles[0].style_id, process_id: sewing.process_id,
  })
  expect(employeeSummary.employees).toHaveLength(2)
  for (const employee of employeeSummary.employees) {
    await expect(page.locator('.salary-desktop.table-card')).toContainText(`${employee.user_name}：${employee.qty} 件 · ${money(employee.amount)}`)
  }

  await openRoute(page, 'salary/settlements')
  const settlementRow = page.locator('.salary-desktop.table-card tbody tr').filter({ hasText: 'E2E员工一' })
  await expect(settlementRow).toContainText('¥585.00')
  await expect(settlementRow).toContainText('已结算')
  await settlementRow.getByRole('button', { name: '薪资条', exact: true }).click()
  const payslip = page.locator('.salary-modal')
  await expect(payslip).toContainText('合计 ¥585.00')
  await expect(payslip.locator('.salary-payslip-desktop tbody tr')).toHaveCount(1)
  await expect(payslip).toContainText('E2E员工一')
  await expect(payslip).toContainText('90 件 / ¥270.00')
})

test('05 桌面采购入库领料批次追溯与库存结存一致', async ({ page }) => {
  test.setTimeout(120000)
  const chain = await readChain(page)
  await openRoute(page, 'warehouse/lots')
  await expect(page.locator('.warehouse-desktop .desktop-table tbody tr')).toHaveCount(3)
  for (const [suffix, qty] of [['L1', '500'], ['L2', '300'], ['L3', '1,000']]) {
    await expect(page.locator('.warehouse-desktop .desktop-table tbody tr').filter({ hasText: `${chain.prefix}${suffix}` })).toContainText(qty)
  }
  await openRoute(page, 'warehouse/purchases')
  await expect(page.locator('.warehouse-desktop .desktop-table tbody tr')).toHaveCount(1)
  await expect(page.locator('.warehouse-desktop .desktop-table')).toContainText(chain.purchases[0].order_no)
  await openRoute(page, 'warehouse/stockIns')
  await expect(page.locator('.warehouse-desktop .desktop-table tbody tr')).toHaveCount(2)
  await expect(page.locator('.warehouse-desktop .desktop-table .badge.done')).toHaveCount(2)
  await openRoute(page, 'warehouse/issues')
  await expect(page.locator('.warehouse-desktop .desktop-table tbody tr')).toHaveCount(2)
  await expect(page.locator('.warehouse-desktop .desktop-table .badge.done')).toHaveCount(2)
  for (const issue of chain.issues) {
    const issueRow = page.locator('.warehouse-desktop .desktop-table tbody tr').filter({ hasText: issue.issue_no })
    await expect(issueRow.locator('td').nth(3)).toHaveText(String(issue.items.filter((line) => line.over || line.over_state === 'over').length))
    await issueRow.getByRole('button', { name: issue.issue_no, exact: true }).click()
    const issueDialog = page.getByRole('dialog', { name: '领退料单' })
    await expect(issueDialog.locator('.table-wrap tbody tr')).toHaveCount(issue.items.length)
    for (const line of issue.items) {
      await expect(issueDialog.locator('.table-wrap')).toContainText(String(line.qty))
    }
    await issueDialog.locator('.form-actions').getByRole('button', { name: '关闭', exact: true }).click()
  }

  await openRoute(page, 'warehouse/trace')
  await page.locator('.warehouse-desktop input[placeholder="输入批次号、面料或颜色"]').fill(chain.prefix)
  await page.locator('.warehouse-desktop').getByRole('button', { name: '查询批次', exact: true }).click()
  const traceRow = page.locator('.warehouse-desktop .desktop-table tbody tr').filter({ hasText: `${chain.prefix}L1` })
  await expect(traceRow).toContainText('1')
  await expect(traceRow).toContainText('8')
  await expect(traceRow).toContainText('200')
  await traceRow.click()
  const traceDetail = page.locator('.warehouse-trace-detail')
  await expect(traceDetail).toContainText('8 扎')
  await expect(traceDetail).toContainText('200')
  await expect(traceDetail).toContainText(chain.production.bed_no)

  await openRoute(page, 'finance/valuation')
  await page.locator('.filter-bar input[type="date"]').fill(chain.dateTo)
  await page.getByRole('button', { name: '查询估值', exact: true }).click()
  const fabricCard = page.locator('.valuation-card').filter({ hasText: `${chain.prefix}M1` })
  const accessoryCard = page.locator('.valuation-card').filter({ hasText: `${chain.prefix}M2` })
  await expect(fabricCard).toContainText('1235.60')
  await expect(fabricCard).toContainText('31112.00')
  await expect(accessoryCard).toContainText('99.20')
  await expect(accessoryCard).toContainText('198.40')
  await fabricCard.locator('.valuation-head').click()
  await expect(fabricCard.locator('.valuation-lot-table tbody tr')).toHaveCount(2)
  await expect(fabricCard).toContainText('235.60')
})

test('06 桌面出货应收双收款成本与损益一致', async ({ page }) => {
  test.setTimeout(120000)
  const chain = await readChain(page)
  await openRoute(page, 'shipments')
  const shipmentRow = page.locator('.desktop-table tbody tr').filter({ hasText: chain.shipment.shipment_no })
  await expect(shipmentRow.locator('td').nth(2)).toHaveText('120')
  await expect(shipmentRow).toContainText(chain.order.order_no)
  await shipmentRow.click()
  const shipmentDialog = page.locator('.modal-backdrop .modal')
  await expect(shipmentDialog.locator('.detail-grid')).toContainText('120')
  await expect(shipmentDialog.locator('.mobile-undelivered-total')).toContainText('已出 120 · 未交 80')
  await expect(shipmentDialog.locator('.desktop-shipment-table').first().locator('tbody tr')).toHaveCount(3)
  await expect(shipmentDialog.locator('.desktop-shipment-table').first()).toContainText('50')
  await expect(shipmentDialog.locator('.desktop-shipment-table').first()).toContainText('30')
  await expect(shipmentDialog.locator('.desktop-shipment-table').first()).toContainText('40')
  await shipmentDialog.getByRole('button', { name: '关闭', exact: true }).click()

  await openRoute(page, 'finance/receivables')
  const receivableRow = page.locator('.desktop-table tbody tr').filter({ hasText: chain.receivable.receivable_no })
  await expect(receivableRow.locator('td').nth(2)).toHaveText('3520.00')
  await expect(receivableRow.locator('td').nth(3)).toHaveText('3520.00')
  await expect(receivableRow.locator('td').nth(4)).toHaveText('0.00')
  await expect(receivableRow).toContainText('已结清')
  await receivableRow.click()
  const receivableDialog = page.locator('.modal-backdrop .modal')
  await expect(receivableDialog.locator('.finance-receipt-table tbody tr')).toHaveCount(2)
  await expect(receivableDialog.getByRole('button', { name: '收款核销', exact: true })).toBeDisabled()
  await receivableDialog.getByRole('button', { name: '关闭', exact: true }).click()

  await openRoute(page, 'costs/costs')
  await setRangeAndQuery(page, chain, '.cost-filter')
  const costRow = page.locator('.desktop-table tbody tr').filter({ hasText: chain.order.order_no })
  for (const value of ['5920.00', '5689.60', '1205.00', '100.00', '6994.60', '-1074.60']) await expect(costRow).toContainText(value)
  await openRoute(page, 'costs/pnl')
  await setRangeAndQuery(page, chain, '.pnl-filter')
  const pnlRow = page.locator('.desktop-table tbody tr').filter({ hasText: chain.order.order_no })
  await expect(pnlRow.locator('td').nth(3)).toHaveText('6994.60')
  await expect(pnlRow.locator('td').nth(5)).toHaveText('3520')
  await expect(pnlRow.locator('td').nth(6)).toHaveText('3520')
  await expect(pnlRow.locator('td').nth(7)).toHaveText('0')
  await expect(pnlRow.locator('td').nth(8)).toHaveText('-3474.60')
})

test('07 手机订单床次计件和完成汇总三级钻取一致', async ({ page }) => {
  test.setTimeout(120000)
  const chain = await readChain(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await openRoute(page, 'orders')
  const orderCard = page.locator('.order-mobile-card').filter({ hasText: chain.order.order_no })
  await expect(orderCard).toContainText('订单 200 件 · 已登记 540 件')
  await orderCard.click()
  const orderDialog = page.getByRole('dialog', { name: `订单 ${chain.order.order_no}` })
  await expect(orderDialog.locator('.order-progress-stats')).toContainText('540')
  await expect(orderDialog.locator('.mobile-order-cells')).toBeVisible()
  await orderDialog.getByRole('button', { name: '关闭', exact: true }).click()

  await openRoute(page, 'production')
  const productionCard = page.locator('.production-mobile-card').filter({ hasText: chain.production.bed_no })
  await expect(productionCard).toContainText('菲票 8 扎')
  await expect(productionCard).toContainText('已登记 540 件')
  await productionCard.click()
  const productionDialog = page.getByRole('dialog', { name: `床次 ${chain.production.bed_no}` })
  await expect(productionDialog.locator('.mobile-bundle-card')).toHaveCount(8)
  await expect(productionDialog.locator('.mobile-production-bundles')).toBeVisible()
  await productionDialog.getByRole('button', { name: '关闭', exact: true }).click()

  await openRoute(page, 'records')
  const recordDates = page.locator('.records-mobile-filters input[type="date"]')
  await recordDates.first().fill(chain.dateFrom)
  await recordDates.nth(1).fill(chain.dateTo)
  await expect(page.locator('.record-mobile-card')).toHaveCount(20)
  await expect(page.locator('.records-mobile-summary')).toContainText('共 20 条登记')
  await expect(page.locator('.record-mobile-card .badge.settled')).toHaveCount(chain.records.filter((item) => item.settlement).length)
  await expect(page.locator('.record-mobile-card .badge.producing')).toHaveCount(chain.records.filter((item) => !item.settlement).length)

  await openRoute(page, 'salary/completion')
  await setRangeAndQuery(page, chain)
  await expect(page.locator('.salary-completion-head')).toContainText('540')
  const styleCard = page.locator('.salary-completion-card').filter({ hasText: chain.style.style_no })
  await expect(styleCard).toContainText('540 件')
  await expect(styleCard).toContainText('¥1,205.00')
  await expect(styleCard).toContainText('3 道工序 · 7 扎')
  await styleCard.click()
  await expect(page.locator('.salary-completion-card')).toHaveCount(3)
  const processSummary = await api(page, '/salary/summary/completion', {
    date_from: chain.dateFrom, date_to: chain.dateTo, style_id: chain.completion.styles[0].style_id,
  })
  const sewing = processSummary.processes.find((item) => item.name.endsWith('车缝'))
  expect(sewing).toBeTruthy()
  const sewingCard = page.locator('.salary-completion-card').filter({ hasText: '车缝' })
  await expect(sewingCard).toContainText(`数量：${sewing.qty}`)
  await expect(sewingCard).toContainText(money(sewing.amount))
  await sewingCard.click()
  const employeeSummary = await api(page, '/salary/summary/completion', {
    date_from: chain.dateFrom, date_to: chain.dateTo,
    style_id: chain.completion.styles[0].style_id, process_id: sewing.process_id,
  })
  await expect(page.locator('.salary-completion-employee')).toHaveCount(employeeSummary.employees.length)
  for (const employee of employeeSummary.employees) {
    const employeeCard = page.locator('.salary-completion-employee').filter({ hasText: employee.user_name })
    await expect(employeeCard).toContainText(`数量${employee.qty}`)
    await expect(employeeCard).toContainText(money(employee.amount))
  }
  await expect(page.locator('.salary-completion-tickets')).toBeVisible()
})

test('08 手机基础资料出货应收成本页面数据一致', async ({ page }) => {
  test.setTimeout(120000)
  const chain = await readChain(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await openRoute(page, 'master/styles')
  const styleCard = page.locator('.mobile-style-row').filter({ hasText: chain.style.style_no })
  await expect(styleCard).toContainText('¥6.50')
  await expect(styleCard).toContainText('3 / 8')
  await openRoute(page, 'quotations')
  const quoteCard = page.locator('.quotation-business-card').filter({ hasText: chain.quote.quote_no })
  await expect(quoteCard).toContainText('4819.44')
  await expect(quoteCard).toContainText('×200')
  await openRoute(page, 'tech/boms')
  const bomCard = page.locator('.tech-mobile-card').filter({ hasText: chain.style.style_no })
  await expect(bomCard).toContainText('v1')
  await expect(bomCard).toContainText('2 项明细')
  await expect(bomCard).toContainText('生效')

  await openRoute(page, 'shipments')
  const shipmentCard = page.locator('.shipment-mobile-card').filter({ hasText: chain.shipment.shipment_no })
  await expect(shipmentCard).toContainText('120 件')
  await expect(shipmentCard).toContainText(chain.order.order_no)
  await shipmentCard.click()
  const shipmentDialog = page.locator('.modal-backdrop .modal')
  await expect(shipmentDialog.locator('.mobile-undelivered-total')).toContainText('已出 120 · 未交 80')
  await expect(shipmentDialog.locator('.mobile-shipment-items article')).toHaveCount(3)
  await expect(shipmentDialog.locator('.mobile-shipment-items')).toContainText('50 件')
  await expect(shipmentDialog.locator('.mobile-shipment-items')).toContainText('30 件')
  await expect(shipmentDialog.locator('.mobile-shipment-items')).toContainText('40 件')
  await shipmentDialog.getByRole('button', { name: '关闭', exact: true }).click()

  await openRoute(page, 'finance/receivables')
  const receivableCard = page.locator('.finance-receivable-card').filter({ hasText: chain.receivable.receivable_no })
  await expect(receivableCard).toContainText('3520.00')
  await expect(receivableCard).toContainText('已结清')
  await receivableCard.click()
  const receivableDialog = page.locator('.modal-backdrop .modal')
  await expect(receivableDialog.locator('.finance-receipt-mobile article')).toHaveCount(2)
  await expect(receivableDialog.locator('.finance-receipt-mobile')).toContainText('2520.00 元')
  await expect(receivableDialog.locator('.finance-receipt-mobile')).toContainText('1000.00 元')
  await expect(receivableDialog).toContainText('3520.00')
  await receivableDialog.getByRole('button', { name: '关闭', exact: true }).click()

  await openRoute(page, 'costs/costs')
  await setRangeAndQuery(page, chain, '.cost-filter')
  const costCard = page.locator('.cost-order-card').filter({ hasText: chain.order.order_no })
  for (const value of ['5920.00', '5689.60', '1205.00', '100.00', '6994.60']) await expect(costCard).toContainText(value)
  await openRoute(page, 'warehouse/lots')
  await expect(page.locator('.warehouse-mobile .warehouse-mobile-card')).toHaveCount(3)
  await expect(page.locator('.warehouse-mobile')).toContainText(`${chain.prefix}L1`)
  await expect(page.locator('.warehouse-mobile')).toContainText(`${chain.prefix}L3`)
})
