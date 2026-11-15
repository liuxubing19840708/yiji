import { test, expect } from '@playwright/test'

const schedule = {
  id: 81, name: '停用的日报', saved_report_id: null, saved_report: null,
  plan: { dataset: 'piecework_records', filters: [{ value: '绝密筛选值' }] },
  frequency: 'daily', hour: 9, minute: 30, weekday: null, day_of_month: null,
  enabled: false, timezone: 'Asia/Shanghai', next_run_at: '2026-11-12T09:30:00+08:00',
}
const failedRun = {
  id: 901, schedule_id: 81, schedule_name: '停用的日报', kind: 'natural',
  scheduled_at: '2026-11-11T09:30:00+08:00', started_at: '2026-11-11T09:30:01+08:00',
  finished_at: '2026-11-11T09:30:02+08:00', status: 'failed', outcome: 'failed',
  error_code: 'execution_error', error_summary: '执行失败，请稍后重试', retry_of_id: null,
}

async function mockRunCenter(page, { empty = false, failFirstLoad = false, delayRuns = false } = {}) {
  const state = {
    schedules: empty ? [] : [schedule], runs: empty ? [] : [failedRun],
    manualCalls: 0, retryCalls: 0, runListCalls: 0, failFirstLoad,
  }
  await page.addInitScript(() => {
    localStorage.setItem('yisao.web.session.v1', JSON.stringify({
      access: 'schedule-run-center-local', companyId: 1, user: { id: 1, real_name: '本地测试' },
      companies: [{ id: 1, name: '本地模拟工厂', status: 1, post: { name: '测试岗位' } }],
    }))
  })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    const json = (data, status = 200) => route.fulfill({
      status, contentType: 'application/json', body: JSON.stringify(data),
    })
    if (path === '/api/notifications') return json({ count: 0, results: [] })
    if (path === '/api/ai/report/catalog') return json({
      capabilities: { export: false, full_data: false, share: false },
      datasets: [{
        key: 'piecework_records', label: '计件登记', description: '本地模拟数据集',
        time_field: 'registered_date', default_dimensions: ['process_name'],
        default_metrics: ['registered_qty'], supports_as_of: false,
        fields: [
          { key: 'registered_date', label: '登记日', can_dimension: true, can_filter: true, operators: ['between'] },
          { key: 'process_name', label: '工序', can_dimension: true, can_filter: true, operators: ['eq'] },
        ],
        metrics: [{ key: 'registered_qty', label: '登记件数', default_aggregation: 'sum', aggregations: ['sum'] }],
      }],
    })
    if (path === '/api/ai/report/saved') return json({ results: [] })
    if (path === '/api/ai/report/schedules' && method === 'GET') return json({ results: state.schedules })
    if (path === '/api/ai/report/schedules/runs' && method === 'GET') {
      state.runListCalls += 1
      if (delayRuns && state.runListCalls === 1) await new Promise((resolve) => setTimeout(resolve, 250))
      if (state.failFirstLoad) {
        state.failFirstLoad = false
        return json({ detail: '运行中心暂不可用' }, 503)
      }
      return json({ results: state.runs })
    }
    if (path === '/api/ai/report/schedules/81/run' && method === 'POST') {
      state.manualCalls += 1
      expect(request.postDataJSON().request_id).toMatch(/^[0-9a-f-]{36}$/)
      const run = {
        ...failedRun, id: 902, kind: 'manual', status: 'success', outcome: 'success',
        error_code: '', error_summary: '', scheduled_at: '2026-11-11T12:00:00+08:00',
      }
      state.runs = [run, ...state.runs]
      return json(run, 201)
    }
    if (path === '/api/ai/report/schedules/runs/901/retry' && method === 'POST') {
      state.retryCalls += 1
      expect(request.postDataJSON().request_id).toMatch(/^[0-9a-f-]{36}$/)
      const run = {
        ...failedRun, id: 903, kind: 'retry', retry_of_id: 901,
        status: 'success', outcome: 'success', error_code: '', error_summary: '',
      }
      state.runs = [run, ...state.runs]
      return json(run, 201)
    }
    return json({ detail: `未模拟接口：${method} ${path}` }, 500)
  })
  return state
}

async function openDesigner(page) {
  await page.goto('/web/#ai')
  await page.getByRole('tab', { name: '报表设计器', exact: true }).click()
}

test('移动运行中心查看、手动执行停用任务、失败重试与刷新均使用 mock', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 })
  const state = await mockRunCenter(page)
  await openDesigner(page)
  await expect(page.locator('.schedule-run')).toHaveCount(1)
  await expect(page.locator('.schedule-run')).toContainText('自然调度')
  await expect(page.locator('.schedule-run')).toContainText('执行失败，请稍后重试')
  await expect(page.locator('.schedule-run')).not.toContainText('绝密筛选值')
  await expect(page.locator('.run-center')).toContainText('不包含业务结果、行数或筛选值')

  await page.locator('.schedule-item').getByRole('button', { name: '手动执行', exact: true }).click()
  await expect(page.locator('.schedule-run[data-status="success"]')).toHaveCount(1)
  expect(state.manualCalls).toBe(1)
  await page.locator('.schedule-run[data-status="failed"]').getByRole('button', { name: '失败重试', exact: true }).click()
  await expect(page.locator('.schedule-run[data-status="success"]')).toHaveCount(2)
  await expect(page.locator('.schedule-run[data-status="success"]').first()).toContainText('失败重试')
  expect(state.retryCalls).toBe(1)
  const beforeRefresh = state.runListCalls
  await page.getByRole('button', { name: '刷新运行记录', exact: true }).click()
  expect(state.runListCalls).toBe(beforeRefresh + 1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('运行中心显示加载和错误态并可在错误后刷新恢复', async ({ page }) => {
  const state = await mockRunCenter(page, { failFirstLoad: true, delayRuns: true })
  await openDesigner(page)
  await expect(page.getByText('正在加载运行记录…', { exact: false })).toBeVisible()
  await expect(page.getByRole('alert').filter({ hasText: '运行中心暂不可用' })).toBeVisible()
  await page.getByRole('button', { name: '刷新运行记录', exact: true }).click()
  await expect(page.locator('.schedule-run')).toHaveCount(1)
  expect(state.runListCalls).toBe(2)
})

test('运行中心和任务列表均有明确空态', async ({ page }) => {
  await mockRunCenter(page, { empty: true })
  await openDesigner(page)
  await expect(page.getByText('暂无定时任务', { exact: true })).toBeVisible()
  await expect(page.getByText('暂无运行记录，可在上方手动执行一个任务', { exact: true })).toBeVisible()
})
