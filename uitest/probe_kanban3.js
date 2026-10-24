/**
 * 看板维度切换 探针（probe_kanban3.js，2026-10-23 需求：按订单/裁床/部门/组/人查看）。
 *
 * 覆盖：
 *   1. 看板页编译 + 维度 tab 渲染（6 个：款式/订单/裁床/部门/组/人，默认款式=模板计划表）；
 *   2. 切「订单」维度 → /dashboard/dim 聚合表渲染（首列订单号、行带下钻标记）；
 *   3. 切「裁床」维度 → 行可下钻（nav=bed），tap 首行 → 床次详情页（带 id）；
 *   4. 切回「款式」→ 模板计划表恢复；
 *   5. reLaunch 重建后维度筛选从全局缓存恢复（返回/重进不丢上下文）。
 *
 * 前置：cli.bat auto --project <frontend> --auto-port 9420，后端 8000 已起。
 * 运行：node probe_kanban3.js（在 uitest/ 目录下）
 */
const automator = require('miniprogram-automator');

const API = 'http://127.0.0.1:8000/api';
const PASS = [], FAIL = [], SKIP = [];
function check(name, cond, detail = '') {
  (cond ? PASS : FAIL).push(name);
  console.log(cond ? 'PASS' : 'FAIL', name, detail ? `| ${detail}` : '');
}
function skip(name, detail = '') {
  SKIP.push(name);
  console.log('SKIP', name, detail ? `| ${detail}` : '');
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, { method = 'GET', token, cid, json } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (cid) headers['X-Company-Id'] = String(cid);
  let resp;
  try {
    resp = await fetch(API + path, {
      method, headers, body: json === undefined ? undefined : JSON.stringify(json)
    });
  } catch (e) {
    return { code: 0, data: {}, netErr: String(e && e.message) };
  }
  const text = await resp.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (e) { /* ignore */ }
  return { code: resp.status, data };
}

/** 等待 page.data() 中 loader 返回 true（轮询，最多 20×500ms） */
async function waitFor(page, loader, label) {
  for (let i = 0; i < 20; i++) {
    await sleep(500);
    const d = await page.data();
    if (loader(d)) return d;
  }
  console.log(`[warn] waitFor 超时: ${label}`);
  return await page.data();
}

(async () => {
  let mp = null;
  try {
    // ---- 0. 连 automator（9420 不可用 → 交付待验证） ----
    try {
      mp = await automator.connect({ wsEndpoint: 'ws://localhost:9420' });
      console.log('connected to 9420');
    } catch (e) {
      console.log('CONNECT ERR:', e.message);
      console.log('\n=== 端口 9420 不可用：探针无法运行（待真机/开发者工具 automator 端口后复跑）===');
      process.exitCode = 3;
      return;
    }

    // ---- 1. 后端登录（端到端测试厂 cid=159：有拟真登记数据，维度表才有行） ----
    const cid = 159;
    const login = await api('/auth/login', {
      method: 'POST',
      json: { phone: '13988720901', password: 'yisao1234' }
    });
    const backendUp = login.code === 200 && login.data.access;
    check('B1 后端登录', backendUp, `code=${login.code}${login.netErr ? ' ' + login.netErr : ''}`);
    if (!backendUp) {
      skip('B1 之后的全部断言', '后端 8000 未就绪');
      return;
    }
    const at = login.data.access;
    const me = await api('/auth/me', { token: at, cid });
    const user = (me.data && (me.data.user || me.data)) || { real_name: 'probe' };

    // ---- 2. 注入登录态 → reLaunch 看板页 ----
    await mp.callWxMethod('setStorageSync', 'yisao_token', at);
    await mp.callWxMethod('setStorageSync', 'yisao_refresh', login.data.refresh || '');
    await mp.callWxMethod('setStorageSync', 'yisao_user', user);
    await mp.callWxMethod('setStorageSync', 'yisao_company_id', cid);
    await mp.callWxMethod('setStorageSync', 'yisao_is_admin', 1);

    const page = await mp.reLaunch('/packages/admin/kanban/index');
    check('K1 reLaunch 看板页', !!page, page && page.path);
    await waitFor(page, (d) => d && d.summary && !d.loading, '看板渲染数据');
    // 复位维度到款式（上一轮探针可能把 dim=order 留在全局缓存）
    const dReset = await page.data();
    if (dReset.dim !== 'style') {
      await page.callMethod('onDimTap', { currentTarget: { dataset: { key: 'style' } } });
      await sleep(500);
    }
    const d0 = await page.data();

    check('K2 维度 tab 六项且默认款式',
      (d0.dimTabs || []).length === 6 && d0.dim === 'style'
      && d0.dimTabs[1].label === '订单' && d0.dimTabs[5].label === '人',
      `dimTabs=${(d0.dimTabs || []).map((t) => t.label).join('/')} dim=${d0.dim}`);
    check('K3 款式维度=模板计划表已渲染',
      (d0.columns || []).length > 0 && (d0.groups || []).length > 0,
      `cols=${(d0.columns || []).length} groups=${(d0.groups || []).length}`);

    // ---- 3. 切自定义宽区间（厂内造数日期不确定，避免 month 空数据误报） ----
    await page.callMethod('onRangeTap', { currentTarget: { dataset: { key: 'custom' } } });
    await page.callMethod('onCustomStart', { detail: { value: '2000-01-01' } });
    await page.callMethod('onCustomEnd', { detail: { value: '2100-01-01' } });
    await waitFor(page, (d) => !d.loading, '宽区间重查');

    // ---- 4. 切「订单」维度 → 聚合表 ----
    await page.callMethod('onDimTap', { currentTarget: { dataset: { key: 'order' } } });
    const d1 = await waitFor(page, (d) => d.dim === 'order' && !d.dimLoading, '订单维度数据');
    check('K4 订单维度聚合表渲染',
      d1.dim === 'order' && !d1.dimError && (d1.dimRows || []).length > 0,
      `rows=${(d1.dimRows || []).length} err=${d1.dimError}`);
    check('K5 订单维度首列=订单号且行带下钻标记',
      ((d1.dimCols || [])[0] || {}).title === '订单号'
      && d1.dimRows.every((r) => r.nav === 'order'),
      `cols=${(d1.dimCols || []).map((c) => c.title).join('/')}`);

    // ---- 4. 切「裁床」维度 → 行下钻床次详情 ----
    await page.callMethod('onDimTap', { currentTarget: { dataset: { key: 'bed' } } });
    const d2 = await waitFor(page, (d) => d.dim === 'bed' && !d.dimLoading, '裁床维度数据');
    check('K6 裁床维度聚合表渲染',
      d2.dim === 'bed' && !d2.dimError && (d2.dimRows || []).length > 0
      && ((d2.dimCols || []).slice(-1)[0] || {}).title === '状态',
      `rows=${(d2.dimRows || []).length} err=${d2.dimError}`);
    check('K7 裁床维度行均带下钻标记',
      d2.dimRows.every((r) => r.nav === 'bed'), `rows=${(d2.dimRows || []).length}`);

    if (d2.dimRows.length) {
      await page.callMethod('onDimRowTap', { currentTarget: { dataset: { index: 0 } } });
      await sleep(1500);
      const cur = await mp.currentPage();
      check('K8 裁床行 tap → 床次详情页',
        cur && cur.path === 'packages/admin/production-detail/index',
        `path=${cur && cur.path}`);
      await mp.navigateBack();
      await sleep(800);
    } else {
      skip('K8 裁床行下钻', '当前 range 无裁床登记数据');
    }

    // ---- 5. 切回「款式」→ 模板计划表恢复 ----
    await page.callMethod('onDimTap', { currentTarget: { dataset: { key: 'style' } } });
    await sleep(500);
    const d3 = await page.data();
    check('K9 切回款式维度=模板计划表',
      d3.dim === 'style' && (d3.groups || []).length > 0,
      `dim=${d3.dim} groups=${(d3.groups || []).length}`);

    // ---- 6. 页面重建后维度筛选从全局缓存恢复 ----
    await page.callMethod('onDimTap', { currentTarget: { dataset: { key: 'order' } } });
    await sleep(800);
    const page2 = await mp.reLaunch('/packages/admin/kanban/index');
    const d4 = await page2.data();
    check('K10 页面重建后维度筛选保留（缓存恢复）', d4.dim === 'order', `dim=${d4.dim}`);

    try {
      await mp.screenshot({ path: 'shots/probe_kanban3.png' });
      console.log('screenshot: shots/probe_kanban3.png');
    } catch (e) {
      console.log('[warn] screenshot 失败(跳过):', e.message);
    }
  } catch (e) {
    console.log('PROBE ERR:', e && (e.stack || e.message));
    process.exitCode = 2;
  } finally {
    try { if (mp) await mp.disconnect(); } catch (e) { /* ignore */ }
  }
  console.log(`\n=== 看板维度探针完成：PASS=${PASS.length} FAIL=${FAIL.length} SKIP=${SKIP.length} ===`);
  if (FAIL.length) { console.log('失败项:', FAIL); process.exitCode = 1; }
})();
