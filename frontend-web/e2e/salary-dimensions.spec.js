import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const session = {
  access: 'salary-dimension-fixture',
  companyId: 1,
  user: { id: 1, real_name: '薪酬管理员' },
  companies: [{ id: 1, name: '模拟工厂', post: { name: '管理员' } }],
}

const tables = {
  order: {
    dim: 'order', range: 'custom',
    columns: [
      { key: 'order_no', title: '订单号', width: 190 },
      { key: 'note', title: '客户,备注', width: 150 },
    ],
    rows: [{ key: '501', nav: 'order', cells: ['PO,"A"\n01', '客户"甲'] }],
  },
  bed: {
    dim: 'bed', range: 'custom',
    columns: [{ key: 'bed_no', title: '床号', width: 130 }, { key: 'qty', title: '登记件数', width: 120 }],
    rows: [{ key: '601', nav: 'bed', cells: ['B-01', '24'] }],
  },
  post: {
    dim: 'post', range: 'custom',
    columns: [{ key: 'name', title: '部门', width: 140 }],
    rows: [{ key: '车缝', nav: '', cells: ['车缝部'] }],
  },
  group: {
    dim: 'group', range: 'custom',
    columns: [{ key: 'name', title: '组', width: 140 }],
    rows: [{ key: '2', nav: '', cells: ['第二组'] }],
  },
  user: {
    dim: 'user', range: 'custom',
    columns: [{ key: 'name', title: '姓名', width: 140 }],
    rows: [],
  },
}

async function installApi(page, options = {}) {
  page.on('pageerror', (error) => console.error('PAGE ERROR:', error.message))
  const state = { dimensionRequests: [], failPost: Boolean(options.failPost) }
  await page.addInitScript((value) => localStorage.setItem('yisao.web.session.v1', JSON.stringify(value)), session)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace(/^\/api/, '')
    const respond = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/notifications') return respond({ count: 0, results: [] })
    if (path === '/salary/summary/completion') return respond({ styles: [], processes: [], matrix: [], employees: [], tickets: [] })
    if (path === '/dashboard/dim') {
      const dim = url.searchParams.get('dim')
      state.dimensionRequests.push({ dim, range: url.searchParams.get('range'), start: url.searchParams.get('start_date'), end: url.searchParams.get('end_date') })
      if (options.delayFirst && state.dimensionRequests.length === 1) {
        await new Promise((resolve) => setTimeout(resolve, 400))
      }
      if (dim === 'post' && state.failPost) return respond({ detail: '没有维度查看权限' }, 403)
      return respond(tables[dim] || { columns: [], rows: [] })
    }
    if (path === '/orders' || path === '/productions') return respond({ count: 0, results: [] })
    return respond({ detail: `未配置夹具：${request.method()} ${path}` }, 500)
  })
  return state
}

async function openDimensions(page) {
  await page.goto('/web/#salary/completion')
  await page.getByRole('button', { name: '维度汇总', exact: true }).click()
}

test('完成汇总五维使用 custom 日期、后端列行，并下钻订单和床次', async ({ page }) => {
  const state = await installApi(page)
  await page.goto('/web/#salary/completion')
  await page.locator('.salary-filters input[type="date"]').first().fill('2026-10-01')
  await page.locator('.salary-filters input[type="date"]').nth(1).fill('2026-10-31')
  await page.getByRole('button', { name: '查询', exact: true }).click()
  await page.getByRole('button', { name: '维度汇总', exact: true }).click()

  await expect(page.locator('.salary-dimensions tbody tr')).toContainText('PO,"A"')
  expect(state.dimensionRequests[0]).toEqual({ dim: 'order', range: 'custom', start: '2026-10-01', end: '2026-10-31' })
  await expect(page.getByRole('columnheader', { name: '客户,备注', exact: true })).toBeVisible()
  await page.locator('.salary-dimensions tbody tr').click()
  await expect(page).toHaveURL(/#orders\/501$/)
  await expect(page.locator('.welcome h2')).toHaveText('订单管理')

  await openDimensions(page)
  for (const [label, text] of [['部门', '车缝部'], ['组', '第二组']]) {
    await page.locator('.salary-dimension-tabs').getByRole('button', { name: label, exact: true }).click()
    await expect(page.locator('.salary-dimensions tbody tr')).toContainText(text)
    await expect(page.locator('.salary-dimensions tbody tr')).not.toHaveAttribute('tabindex', '0')
  }
  await page.locator('.salary-dimension-tabs').getByRole('button', { name: '人', exact: true }).click()
  await expect(page.getByText('该期间暂无登记数据', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '导出当前维度 CSV', exact: true })).toBeDisabled()

  await page.locator('.salary-dimension-tabs').getByRole('button', { name: '裁床', exact: true }).click()
  await expect(page.locator('.salary-dimensions tbody tr')).toContainText('B-01')
  await page.locator('.salary-dimensions tbody tr').press('Enter')
  await expect(page).toHaveURL(/#production\/601$/)
  await expect(page.locator('.welcome h2')).toHaveText('生产床次')
  expect(state.dimensionRequests.map((item) => item.dim)).toEqual(['order', 'order', 'post', 'group', 'user', 'bed'])
})

test('当前维度 CSV 包含 UTF-8 BOM、CRLF 和 RFC 4180 转义', async ({ page }) => {
  const state = await installApi(page)
  await openDimensions(page)
  await expect(page.locator('.salary-dimensions tbody tr')).toHaveCount(1)
  await page.locator('.salary-filters input[type="date"]').first().fill('2026-09-01')
  await page.locator('.salary-filters input[type="date"]').nth(1).fill('2026-09-30')
  await page.getByRole('button', { name: '查询', exact: true }).click()
  await expect.poll(() => state.dimensionRequests.length).toBe(2)
  expect(state.dimensionRequests[1]).toEqual({ dim: 'order', range: 'custom', start: '2026-09-01', end: '2026-09-30' })
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出当前维度 CSV', exact: true }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('完成汇总_订单_2026-09-01_2026-09-30.csv')
  const content = await readFile(await download.path(), 'utf8')
  expect(content.charCodeAt(0)).toBe(0xfeff)
  expect(content).toBe('﻿订单号,"客户,备注"\r\n"PO,""A""\n01","客户""甲"')
})

test('维度加载、后端权限错误重试与空态均独立呈现', async ({ page }) => {
  const state = await installApi(page, { delayFirst: true, failPost: true })
  await openDimensions(page)
  await expect(page.locator('.salary-dimensions').getByRole('status')).toContainText('正在读取业务数据')
  await expect(page.locator('.salary-dimensions tbody tr')).toHaveCount(1)
  await page.locator('.salary-dimension-tabs').getByRole('button', { name: '部门', exact: true }).click()
  await expect(page.locator('.salary-dimensions').getByRole('alert')).toContainText('没有维度查看权限')
  await expect(page.locator('.salary-dimensions tbody')).toHaveCount(0)
  state.failPost = false
  await page.locator('.salary-dimensions').getByRole('button', { name: '重新加载', exact: true }).click()
  await expect(page.locator('.salary-dimensions tbody tr')).toContainText('车缝部')
  await page.locator('.salary-dimension-tabs').getByRole('button', { name: '人', exact: true }).click()
  await expect(page.getByText('该期间暂无登记数据', { exact: true })).toBeVisible()
})
