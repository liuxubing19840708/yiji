import { test, expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync, unlinkSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const phone = process.env.WEB_PHONE
const password = process.env.WEB_PASSWORD
const python = process.env.WEB_PYTHON

test.beforeEach(async () => {
  test.skip(!phone || !password || !python, '设置 WEB_PHONE/WEB_PASSWORD/WEB_PYTHON 后运行隔离环境扩展测试')
})

async function login(page, route) {
  await page.goto(`/web/#${route}`)
  await page.getByPlaceholder('请输入手机号').fill(phone)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('checkbox', { name: '我已阅读并同意隐私政策与用户协议' }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.locator('.topbar h1')).not.toHaveText('登录')
}

async function call(page, method, path, body) {
  return page.evaluate(async ({ method, path, body }) => {
    const session = JSON.parse(localStorage.getItem('yisao.web.session.v1'))
    const response = await fetch(`/api${path}`, {
      method,
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${session.access}`,
        'X-Company-Id': String(session.companyId),
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const text = await response.text()
    let data = null
    try { data = text ? JSON.parse(text) : null } catch { data = text }
    return { status: response.status, data }
  }, { method, path, body })
}

function workbook(path, rows) {
  const source = [
    'import json, sys',
    'from openpyxl import Workbook',
    'book = Workbook()',
    'sheet = book.active',
    'for row in json.loads(sys.argv[2]): sheet.append(row)',
    'book.save(sys.argv[1])',
  ].join('\n')
  execFileSync(python, ['-I', '-c', source, path, JSON.stringify(rows)])
}

async function importFile(page, title, path, endpoint) {
  await page.getByRole('button', { name: title === '批量导入物料' ? '导入 物料' : title === '批量导入 BOM' ? '导入 BOM' : '导入期初库存', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: title, exact: true })
  await dialog.locator('input[type=file]').setInputFiles(path)
  const [response] = await Promise.all([
    page.waitForResponse((item) => item.url().endsWith(`/api${endpoint}`) && item.request().method() === 'POST'),
    dialog.getByRole('button', { name: '开始导入', exact: true }).click(),
  ])
  expect(response.status()).toBe(200)
  return { response, body: await response.json(), dialog }
}

async function expectXlsxDownload(page, nav, hash) {
  await page.evaluate((route) => { location.hash = route }, hash)
  await expect(page.locator('.topbar h1')).toHaveText(nav)
  await expect(page.locator('.content .spinner')).toHaveCount(0)
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: '导出 Excel', exact: true }).click(),
  ])
  expect(download.suggestedFilename()).toMatch(/\.xlsx$/i)
  const content = readFileSync(await download.path())
  expect(content.subarray(0, 2).toString()).toBe('PK')
  expect(content.length).toBeGreaterThan(1000)
}

test('真实 XLSX 导入→床次挂批→质检/委外导出→出货打印记录', async ({ page }, testInfo) => {
  test.setTimeout(180000)
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('dialog', (dialog) => dialog.accept())
  await page.setViewportSize({ width: 1440, height: 1000 })
  await login(page, 'tech')
  await expect(page.locator('.welcome h2')).toHaveText('工艺与 BOM')

  const suffix = Date.now().toString().slice(-9)
  const materialCode = `WEB-M${suffix}`
  const styleNo = `WEB-S${suffix}`
  const bedNo = `WEB-B${suffix}`
  const lotNo = `WEB-L${suffix}`
  const materialPath = testInfo.outputPath('materials.xlsx')
  workbook(materialPath, [
    ['编码', '品名', '分类', '主单位', '副单位', '克重', '幅宽', '缩率', '旧编码'],
    [materialCode, 'Web 隔离导入面料', '面料', '米', '', '', '', '2.5', ''],
  ])
  const materialImport = await importFile(page, '批量导入物料', materialPath, '/tech/materials/import')
  expect(materialImport.body.created).toBe(1)
  expect(materialImport.body.errors).toEqual([])
  await expect(materialImport.dialog.locator('.import-summary')).toContainText('created')
  await materialImport.dialog.locator('.form-actions').getByRole('button', { name: '关闭', exact: true }).click()

  const customers = await call(page, 'GET', '/customers?page_size=1')
  const processes = await call(page, 'GET', '/processes?page_size=1')
  expect(customers.status).toBe(200)
  expect(processes.status).toBe(200)
  const styleResult = await call(page, 'POST', '/styles', {
    style_no: styleNo, name: 'Web 隔离扩展测试款', customer: customers.data.results[0].id,
    fabric: '测试面料', remark: '', craft_note: '', images: [], craft_images: [], video: '',
    process_items: [{ process_id: processes.data.results[0].id, price: '0.85', sort: 1, allow_skip: true }],
  })
  expect(styleResult.status).toBe(201)
  const style = styleResult.data

  await page.getByRole('button', { name: '版式 BOM', exact: true }).click()
  const bomPath = testInfo.outputPath('bom.xlsx')
  workbook(bomPath, [
    ['款号', '版本', '物料编码', '类别', '颜色', '尺码', '单耗', '损耗率'],
    [styleNo, 1, materialCode, '面料', '', '', '1.25', '3'],
  ])
  const bomImport = await importFile(page, '批量导入 BOM', bomPath, '/tech/boms/import')
  expect(bomImport.body.created).toBe(1)
  expect(bomImport.body.errors).toEqual([])
  await bomImport.dialog.locator('.form-actions').getByRole('button', { name: '关闭', exact: true }).click()

  await page.evaluate(() => { location.hash = 'warehouse' })
  await expect(page.locator('.welcome h2')).toHaveText('仓库与追溯')
  const openingPath = testInfo.outputPath('opening-stock.xlsx')
  workbook(openingPath, [
    ['物料编码', '缸号', '数量', '单位', '批次单价', '单价单位', '库位', '备注'],
    [materialCode, lotNo, '80', '米', '12.5', '米', 'Web 测试区', '隔离副本扩展测试'],
  ])
  const openingImport = await importFile(page, '导入期初库存', openingPath, '/opening-stock/import')
  expect(openingImport.body.created).toBe(1)
  expect(openingImport.body.stock_in_id).toBeTruthy()
  await openingImport.dialog.locator('.form-actions').getByRole('button', { name: '关闭', exact: true }).click()
  const lots = await call(page, 'GET', `/lots?keyword=${lotNo}`)
  expect(lots.status).toBe(200)
  expect(lots.data.results).toHaveLength(1)

  const productionResult = await call(page, 'POST', '/productions', {
    bed_no: bedNo, style: style.id, customer: customers.data.results[0].id,
    order_type: '自产', fabric: '测试面料', source_order_no: '', plan_qty: 10,
    bom_bypass: true, bypass_reason: 'Web 隔离副本挂批测试（BOM 为草稿）',
  })
  expect(productionResult.status).toBe(201)
  const production = productionResult.data
  await page.evaluate(() => { location.hash = 'production' })
  await expect(page.locator('.welcome h2')).toHaveText('生产床次')
  await page.getByPlaceholder('搜索床次号、款号或客户').fill(bedNo)
  const productionRow = page.locator('.desktop-table tbody tr').filter({ hasText: bedNo })
  await expect(productionRow).toHaveCount(1)
  await productionRow.click()
  const productionDetail = page.getByRole('dialog', { name: `床次 ${bedNo}` })
  await productionDetail.getByRole('button', { name: '面料批次', exact: true }).click()
  const lotEditor = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: '关联面料批次', exact: true }) })
  await expect(lotEditor.locator('.spinner')).toHaveCount(0)
  await lotEditor.getByRole('button', { name: '添加批次', exact: true }).click()
  await lotEditor.locator('select').selectOption(String(lots.data.results[0].id))
  await lotEditor.getByLabel('批次用量').fill('12.5')
  const [lotResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith(`/api/productions/${production.id}/lot-links`) && response.request().method() === 'PUT'),
    lotEditor.getByRole('button', { name: '保存批次关系', exact: true }).click(),
  ])
  expect(lotResponse.status()).toBe(200)
  const savedLinks = await lotResponse.json()
  expect(savedLinks).toHaveLength(1)
  expect(Number(savedLinks[0].qty)).toBe(12.5)
  await productionDetail.getByRole('button', { name: '关闭', exact: true }).click()

  await expectXlsxDownload(page, '质量管理', 'quality')
  await expectXlsxDownload(page, '外发管理', 'outsource')

  const orderResult = await call(page, 'POST', '/orders', {
    style: style.id, customer: customers.data.results[0].id, delivery_date: null,
    order_type: '自产', remark: 'Web 隔离副本出货打印测试',
    items: [{ color: '测试蓝', size: 'M', qty: 4 }],
    process_items: [{ process_id: processes.data.results[0].id, price: '0.85' }],
  })
  expect(orderResult.status).toBe(201)
  const order = orderResult.data
  await page.evaluate(() => { location.hash = 'shipments' })
  await expect(page.locator('.welcome h2')).toHaveText('出货管理')
  await page.getByRole('button', { name: '新建出货单', exact: true }).click()
  const shipmentEditor = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: '新建出货单', exact: true }) })
  await shipmentEditor.getByLabel('订单').selectOption(String(order.id))
  await expect(shipmentEditor.getByRole('combobox', { name: '颜色', exact: true })).toHaveValue('测试蓝')
  await expect(shipmentEditor.getByRole('combobox', { name: '尺码', exact: true })).toHaveValue('M')
  const [shipmentResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith('/api/shipments') && response.request().method() === 'POST'),
    shipmentEditor.getByRole('button', { name: '保存出货单', exact: true }).click(),
  ])
  expect(shipmentResponse.status()).toBe(201)
  const shipment = await shipmentResponse.json()
  const shipmentDetail = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: shipment.shipment_no, exact: true }) })
  await expect(shipmentDetail).toBeVisible()
  await shipmentDetail.getByRole('button', { name: '新增装箱行', exact: true }).click()
  const boxEditor = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: '新增装箱行', exact: true }) })
  await boxEditor.getByLabel('箱号').fill('WEB-BOX-1')
  await boxEditor.getByLabel('数量', { exact: true }).fill('4')
  await boxEditor.getByLabel('颜色').selectOption('测试蓝')
  await boxEditor.getByLabel('尺码').selectOption('M')
  await boxEditor.getByRole('button', { name: '保存装箱行', exact: true }).click()
  await expect(boxEditor).toHaveCount(0)
  await shipmentDetail.getByRole('button', { name: '打印 / 打印历史', exact: true }).click()
  const printDialog = page.getByRole('dialog', { name: '出货打印预览', exact: true })
  await expect(printDialog).toBeVisible()
  await expect(printDialog.locator('.shipment-paper')).toContainText(shipment.shipment_no)
  await printDialog.getByRole('button', { name: '装箱单', exact: true }).click()
  await expect(printDialog.locator('.shipment-paper')).toContainText('WEB-BOX-1')
  await page.evaluate(() => { window.print = () => { window.__webPrintCalled = true } })
  await printDialog.getByRole('button', { name: '浏览器打印', exact: true }).click()
  expect(await page.evaluate(() => window.__webPrintCalled)).toBe(true)
  const [printResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith(`/api/shipments/${shipment.id}/print-logs`) && response.request().method() === 'POST'),
    printDialog.getByRole('button', { name: '确认已打印并上报', exact: true }).click(),
  ])
  expect(printResponse.status()).toBe(201)
  expect((await printResponse.json()).reported).toBe(1)
  await expect(printDialog.locator('.print-history tbody tr')).toHaveCount(1)
  await expect(printDialog.locator('.print-history')).toContainText('首次')
  expect(errors).toEqual([])
})

test('款式图片、工艺图片及视频上传并保存档案', async ({ page }, testInfo) => {
  test.setTimeout(60000)
  const errors = []
  const uploadedUrls = []
  const cleanupErrors = []
  let styleId
  page.on('pageerror', (error) => errors.push(error.message))
  await login(page, 'master')
  await expect(page.locator('.welcome h2')).toHaveText('基础档案')
  const suffix = Date.now().toString().slice(-9)
  const imagePath = testInfo.outputPath('pixel.png')
  const videoPath = testInfo.outputPath('upload-header.mp4')
  writeFileSync(imagePath, Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64'))
  writeFileSync(videoPath, Buffer.from([0, 0, 0, 24, 102, 116, 121, 112, 105, 115, 111, 109, 0, 0, 0, 0]))

  async function uploadMedia(label, endpoint, path) {
    const [response] = await Promise.all([
      page.waitForResponse((item) => item.url().endsWith(`/api${endpoint}`) && item.request().method() === 'POST'),
      page.getByLabel(label, { exact: true }).setInputFiles(path),
    ])
    expect(response.status()).toBe(200)
    const result = await response.json()
    expect(result.url).toMatch(/^\/media\/uploads\/[0-9a-f]{32}\.(png|mp4)$/)
    uploadedUrls.push(result.url)
    return result.url
  }

  try {
    await page.getByRole('button', { name: '新建款式', exact: true }).click()
    const editor = page.getByRole('dialog', { name: '新建款式', exact: true })
    await editor.getByLabel('款号 *').fill(`WEB-UP${suffix}`)
    await editor.getByLabel('款式名称 *').fill('Web 隔离媒体上传测试')
    const styleImage = await uploadMedia('上传款式图片', '/uploads', imagePath)
    const craftImage = await uploadMedia('上传工艺图片', '/uploads', imagePath)
    const video = await uploadMedia('上传工艺视频', '/uploads/video', videoPath)
    await expect(editor.getByRole('img', { name: '款式图片 1' })).toHaveAttribute('src', styleImage)
    await expect(editor.getByRole('img', { name: '工艺图片 1' })).toHaveAttribute('src', craftImage)
    await expect(editor.getByLabel('工艺视频预览')).toHaveAttribute('src', video)
    const [saveResponse] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith('/api/styles') && response.request().method() === 'POST'),
      editor.getByRole('button', { name: '保存款式与工序', exact: true }).click(),
    ])
    expect(saveResponse.status()).toBe(201)
    const saved = await saveResponse.json()
    styleId = saved.id
    expect(saved.images).toEqual([styleImage])
    expect(saved.craft_images).toEqual([craftImage])
    expect(saved.video).toBe(video)
    await expect(editor).toHaveCount(0)
    expect(errors).toEqual([])
  } finally {
    if (styleId) {
      const result = await call(page, 'DELETE', `/styles/${styleId}`)
      if (result.status >= 400) cleanupErrors.push(`款式 ${styleId}: ${result.status}`)
    }
    const mediaRoot = process.env.WEB_MEDIA_ROOT ? resolve(process.env.WEB_MEDIA_ROOT) : fileURLToPath(new URL('../../backend/media', import.meta.url))
    for (const url of uploadedUrls) {
      try { unlinkSync(resolve(mediaRoot, url.slice('/media/'.length))) } catch (error) { cleanupErrors.push(`${url}: ${error.message}`) }
    }
  }
  expect(cleanupErrors).toEqual([])
})
