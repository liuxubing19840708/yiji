import { test, expect } from '@playwright/test'

const pageOf = (items) => ({ count: items.length, next: null, previous: null, results: items })
const json = (route, data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
const pathOf = (request) => new URL(request.url()).pathname.replace(/^\/api/, '')
const order = {
  id: 7, order_no: 'MOCK-SO-7', style: 1, style_no: 'MOCK-ST-1', style_name: 'Mock 卫衣', customer: 2,
  customer_name: 'Mock 客户', delivery_date: '2026-12-01', order_type: '自产', status: 'producing',
  remark: 'Mock 备注', images: ['/media/uploads/remark-old.png'],
  items: [{ color: '红色', size: 'M', qty: 5 }, { color: '红色', size: 'L', qty: 6 }],
  process_items: [], total_qty: 11, registered_qty: 0, progress: 0,
}
const style = {
  id: 1, style_no: 'MOCK-ST-1', name: 'Mock 卫衣', customer: 2,
  image: '/media/uploads/style-real.png', craft_note: '真实接口工艺：先合肩，再上袖。',
  craft_images: ['/media/uploads/craft-real-1.png', '/media/uploads/craft-real-2.png'],
  video: '/media/uploads/product-real.mp4', processes: [],
}
async function installSession(page) {
  await page.addInitScript(() => localStorage.setItem('yisao.web.session.v1', JSON.stringify({
    access: 'mock-token', companyId: 1, user: { id: 9, real_name: 'Mock 操作员' },
    companies: [{ id: 1, name: 'Mock 工厂', post: { name: 'Mock 管理岗位', is_admin: true } }],
  })))
}
async function installOrderMocks(page) {
  const state = { writes: [], uploads: [], queries: [] }
  await installSession(page)
  await page.route('**/api/**', async (route) => {
    const request = route.request(); const path = pathOf(request); const method = request.method()
    const body = method === 'GET' || path === '/uploads' ? null : request.postDataJSON()
    if (path === '/notifications') return json(route, pageOf([]))
    if (path === '/orders' && method === 'GET') return json(route, pageOf([order]))
    if (path === '/orders' && method === 'POST') { state.writes.push({ method, path, body }); return json(route, { ...order, ...body, id: 8 }, 201) }
    if (path === '/orders/7' && method === 'GET') return json(route, order)
    if (path === '/orders/7' && method === 'PUT') { state.writes.push({ method, path, body }); return json(route, { ...order, ...body }) }
    if (path === '/orders/7/progress') return json(route, { total_qty: 11, registered_qty: 0, progress: 0, cells: [] })
    if (path === '/styles') return json(route, pageOf([style]))
    if (path === '/customers') return json(route, pageOf([{ id: 2, customer_no: 'C-2', name: 'Mock 客户' }]))
    if (path === '/processes' || path === '/process-templates' || path === '/production-colors') return json(route, pageOf([]))
    if (path === '/production-hints') return json(route, { sizes: [] })
    if (path === '/uploads' && method === 'POST') {
      state.uploads.push(request.postData())
      return json(route, { url: `/media/uploads/remark-${state.uploads.length}.png` })
    }
    if (path === '/productions' && method === 'GET') {
      state.queries.push(new URL(request.url()).searchParams)
      if (new URL(request.url()).searchParams.get('order_id')) return json(route, pageOf([{ id: 88, bed_no: 'MOCK-BED-88', style_no: style.style_no, style_name: style.name, total_qty: 11, is_cut_done: false }]))
      return json(route, pageOf([]))
    }
    if (path === '/productions/88') return json(route, { id: 88, bed_no: 'MOCK-BED-88', style_no: style.style_no, style_name: style.name, total_qty: 11 })
    if (path === '/productions/88/bundles') return json(route, pageOf([]))
    return json(route, { detail: `未配置 mock：${method} ${path}` }, 500)
  })
  return state
}

test('订单图片多选上传/预览/删除随保存提交，四视图矩阵与行/列首值铺满使用同一色码数据', async ({ page }) => {
  const errors = []; page.on('pageerror', (error) => errors.push(error.message))
  const state = await installOrderMocks(page)
  await page.goto('/web/#orders')
  await page.getByRole('button', { name: '新建订单', exact: true }).click()
  const editor = page.getByRole('dialog', { name: '订单编辑' })
  await editor.getByLabel('款式 *').selectOption('1')
  const tenFiles = Array.from({ length: 10 }, (_, index) => ({ name: `over-${index}.png`, mimeType: 'image/png', buffer: Buffer.from('mock') }))
  await editor.locator('input[type=file]').setInputFiles(tenFiles)
  await expect(editor.getByText('备注图片最多 9 张', { exact: true })).toBeVisible()
  expect(state.uploads).toHaveLength(0)
  await editor.locator('input[type=file]').setInputFiles([
    { name: 'remark-a.png', mimeType: 'image/png', buffer: Buffer.from('first-image') },
    { name: 'remark-b.png', mimeType: 'image/png', buffer: Buffer.from('second-image') },
  ])
  await expect(editor.locator('.order-image-thumb')).toHaveCount(2)
  expect(state.uploads).toHaveLength(2)
  expect(state.uploads[0]).toContain('name="file"')
  expect(state.uploads[0]).toContain('filename="remark-a.png"')
  await editor.getByRole('button', { name: '预览备注图片' }).first().click()
  await expect(page.getByRole('dialog', { name: '备注图片预览' }).locator('img')).toHaveAttribute('src', '/media/uploads/remark-1.png')
  await page.getByRole('button', { name: '关闭图片预览' }).click()
  await editor.getByRole('button', { name: '删除备注图片 2' }).click()

  await editor.getByLabel('颜色', { exact: true }).fill('红色')
  await editor.getByRole('button', { name: '添加颜色', exact: true }).click()
  await editor.getByLabel('颜色', { exact: true }).fill('蓝色')
  await editor.getByRole('button', { name: '添加颜色', exact: true }).click()
  for (const size of ['M', 'L']) {
    await editor.getByLabel('尺码', { exact: true }).fill(size)
    await editor.getByRole('button', { name: '添加尺码', exact: true }).click()
  }
  await editor.locator('[data-cell="红色|M"] input').fill('5')
  await editor.locator('[data-cell="蓝色|L"] input').fill('7')
  await editor.getByRole('button', { name: '铺满行 红色', exact: true }).click()
  await expect(editor.locator('[data-cell="红色|L"] input')).toHaveValue('5')
  await editor.getByRole('button', { name: '铺满列 M', exact: true }).click()
  await expect(editor.locator('[data-cell="蓝色|M"] input')).toHaveValue('5')
  await editor.getByRole('button', { name: '纵向', exact: true }).click()
  await expect(editor.locator('[data-cell="蓝色|L"] input')).toHaveValue('7')
  await editor.getByRole('button', { name: '倍数', exact: true }).click()
  await editor.getByLabel('倍数基数', { exact: true }).fill('10')
  await editor.getByLabel('红色|M 份数', { exact: true }).fill('2')
  await expect(editor.locator('[data-cell="红色|M"]')).toContainText('20 件')
  await editor.getByRole('button', { name: '排列', exact: true }).click()
  await editor.getByLabel('每格件数', { exact: true }).fill('3')
  await editor.getByRole('button', { name: '一键铺满', exact: true }).click()
  await expect(editor.locator('[data-cell="蓝色|L"] input')).toHaveValue('3')
  await editor.getByRole('button', { name: '保存订单', exact: true }).click()
  await expect(editor).toHaveCount(0)
  const created = state.writes.find((write) => write.method === 'POST')
  expect(created.body.images).toEqual(['/media/uploads/remark-1.png'])
  expect(created.body.items).toEqual([
    { color: '红色', size: 'M', qty: 3 }, { color: '红色', size: 'L', qty: 3 },
    { color: '蓝色', size: 'M', qty: 3 }, { color: '蓝色', size: 'L', qty: 3 },
  ])
  expect(errors).toEqual([])
})

test('编辑回显真实备注图片，订单详情关联床次复用生产深链', async ({ page }) => {
  const state = await installOrderMocks(page)
  await page.goto('/web/#orders')
  await page.locator('.desktop-table tbody tr').first().click()
  const detail = page.getByRole('dialog', { name: '订单 MOCK-SO-7' })
  await expect(detail.locator('.order-production-link')).toContainText('MOCK-BED-88')
  expect(state.queries.some((query) => query.get('order_id') === '7')).toBe(true)
  await detail.getByRole('button', { name: '编辑订单', exact: true }).click()
  const editor = page.getByRole('dialog', { name: '订单编辑' })
  await expect(editor.locator('.order-image-thumb img')).toHaveAttribute('src', '/media/uploads/remark-old.png')
  await expect(editor.locator('[data-cell="红色|M"] input')).toHaveValue('5')
  await editor.getByRole('button', { name: '删除备注图片 1' }).click()
  await editor.getByRole('button', { name: '保存订单', exact: true }).click()
  expect(state.writes.find((write) => write.method === 'PUT').body.images).toEqual([])
  await page.locator('.desktop-table tbody tr').first().click()
  await page.getByRole('dialog', { name: '订单 MOCK-SO-7' }).locator('.order-production-link').click()
  await expect(page).toHaveURL(/#production\/88$/)
  await expect(page.getByRole('dialog', { name: '床次 MOCK-BED-88' })).toBeVisible()
})

test('扫菲工艺弹层读取真实款式详情，明确按钮与双击款号均可打开', async ({ page }) => {
  const errors = []; page.on('pageerror', (error) => errors.push(error.message))
  await installSession(page)
  const styleRequests = []
  await page.route('**/api/**', async (route) => {
    const request = route.request(); const path = pathOf(request)
    if (path === '/notifications') return json(route, pageOf([]))
    if (path === '/records/workbench') return json(route, { my_today: [], my_today_amount: '0.00' })
    if (path === '/tickets/lookup') return json(route, pageOf([{ id: 31, style_id: 1, style_no: style.style_no, style_name: style.name, bed_no: 'MOCK-BED-88', bundle_no: 1, color: '红色', size: 'M', qty: 5 }]))
    if (path === '/records/process-selector') return json(route, { results: [{ id: 3, name: '车缝', price: '1.20' }], current_process: null })
    if (path === '/employees') return json(route, pageOf([]))
    if (path === '/styles/1') { styleRequests.push(request.url()); return json(route, style) }
    return json(route, { detail: `未配置 mock：${request.method()} ${path}` }, 500)
  })
  await page.goto('/web/#scan')
  await page.getByRole('button', { name: '床次＋扎号', exact: true }).click()
  await page.getByPlaceholder('例如：C001').fill('MOCK-BED-88')
  await page.getByPlaceholder('例如：1').fill('1')
  await page.getByRole('button', { name: '查询菲票', exact: true }).click()
  await page.getByRole('button', { name: '款式与工艺', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '菲票款式与工艺' })
  await expect(dialog.getByText('真实接口工艺：先合肩，再上袖。', { exact: true })).toBeVisible()
  await expect(dialog.locator('.scan-style-image')).toHaveAttribute('src', style.image)
  await expect(dialog.locator('.scan-craft-images img')).toHaveCount(2)
  await expect(dialog.locator('video')).toHaveAttribute('src', style.video)
  await page.getByRole('button', { name: '关闭款式与工艺' }).click()
  await page.locator('.scan-ticket-style').dblclick()
  await expect(dialog).toBeVisible()
  expect(styleRequests).toHaveLength(2)
  expect(errors).toEqual([])
})
