// -*- coding: utf-8 -*-
/**
 * 管理端「编菲链路」融合测试（客户/款式→工序配置→建床次→编菲矩阵→菲票落库）
 * 每条用例 = UI 动作 → API 落库断言 → UI 回显断言。
 *
 * 覆盖（测试用例-E2E设计.md §14 补充）：
 *   B1 客户创建   UI 表单 → POST /customers → 列表回显
 *   B2 款式创建   UI 表单 → POST /styles → 列表回显
 *   B3 工序单价   ⋮菜单→工序单价页添加工序/录价 → PUT /styles process_items
 *   B4 编菲全链   选款→床次→矩阵(颜色/尺码/件数)→扎号预览→保存菲票
 *                → POST /tickets/batch 落库（拆扎 70/20=4扎）
 *   B5 登记＋sync-process：API 建登记 → sync-process need_confirm→confirm→审计日志
 *   B6 TC12-06    mock 请求失败 → 客户页错误兜底态 → 恢复后重新加载成功
 *
 * 幂等：所有数据带 MARK 前缀，开头/结尾 API 清扫（productions→styles→customers 顺序删）。
 * 前置: 后端 8000；开发者工具 cli.bat auto --auto-port 9420。
 */
const path = require('path');
const fs = require('fs');
const automator = require('miniprogram-automator');
const shot = require('./helpers/shot');

const API = 'http://127.0.0.1:8000/api';
const ADMIN = { phone: '13800000001', password: 'yisao1234' };
const MARK = '全链UI';
const RUN = String(Date.now()).slice(-6);
const CUST_NO = 'UC' + RUN, CUST_NAME = MARK + '客户' + RUN;
const STY_NO = 'UY' + RUN, STY_NAME = MARK + '款式' + RUN;
const BED_NO = '88' + RUN.slice(-2); // 床次号（数字）

const SHOTS = path.join(__dirname, 'shots');
if (!fs.existsSync(SHOTS)) fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond });
  console.log((cond ? 'PASS' : 'FAIL'), name, cond ? '' : '| ' + String(detail).slice(0, 160));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitData(page, fn, timeout = 10000, label = 'cond') {
  const t0 = Date.now();
  let d = await page.data();
  while (!fn(d)) {
    if (Date.now() - t0 > timeout) throw new Error('waitData 超时: ' + label + ' | ' + JSON.stringify(d).slice(0, 150));
    await sleep(400);
    d = await page.data();
  }
  return d;
}
async function findByText(els, needle) {
  for (const el of els) {
    const t = await el.text();
    if (t.indexOf(needle) >= 0) return el;
  }
  return null;
}
// 轮询等待元素出现再点（data 就绪但 DOM 渲染可能滞后）
async function tapSel(page, sel, tries = 12) {
  for (let i = 0; i < tries; i++) {
    const el = await page.$(sel);
    if (el) { await el.tap(); return el; }
    await sleep(400);
  }
  throw new Error('tapSel 未找到元素: ' + sel);
}

// ---- API 通道 ----
let tok = '', cid = 0;
async function api(method, urlPath, body) {
  const res = await fetch(API + urlPath, {
    method: method.toUpperCase(),
    headers: {
      'Content-Type': 'application/json',
      ...(tok ? { Authorization: 'Bearer ' + tok } : {}),
      ...(cid ? { 'X-Company-Id': String(cid) } : {})
    },
    body: body ? JSON.stringify(body) : undefined
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
  return { status: res.status, data };
}
const rows = (d) => (Array.isArray(d) ? d : (d && d.results) || []);

async function loginIfNeeded(mp) {
  let page = await mp.reLaunch('/pages/login/index');
  await page.waitFor(1500);
  page = await mp.currentPage();
  if (page.path === 'pages/index/index') return page;
  const tabs = await page.$$('.tab');
  for (const el of tabs) if ((await el.text()).indexOf('密码') >= 0) { await el.tap(); break; }
  await sleep(400);
  const inputs = await page.$$('input');
  if (inputs.length >= 2) { await inputs[0].input(ADMIN.phone); await inputs[1].input(ADMIN.password); }
  const agree = await page.$('.agree');
  if (agree) await agree.tap();
  const btns = await page.$$('button');
  for (const el of btns) if ((await el.text()).trim() === '登录') { await el.tap(); break; }
  await sleep(3000);
  page = await mp.currentPage();
  if ((await page.data()).stage === 'pick') {
    const coItems = await page.$$('.co-item');
    if (coItems.length) await coItems[0].tap();
    await sleep(3000);
    page = await mp.currentPage();
  }
  return page;
}

// ---- MARK 残留清扫：撤销登记→删菲票→删床次→删款式（各自有前置校验），→删客户 ----
async function sweep(label = '清扫') {
  const styles = rows((await api('GET', '/styles?keyword=' + encodeURIComponent(MARK) + '&page_size=50')).data);
  for (const s of styles) {
    const prods = rows((await api('GET', '/productions?style_id=' + s.id + '&page_size=50')).data);
    for (const p of prods) {
      const tks = rows((await api('GET', '/tickets?production_id=' + p.id + '&page_size=100')).data);
      for (const t of tks) {
        const recs = rows((await api('GET', '/records?page_size=100')).data)
          .filter((r) => r.ticket === t.id);
        for (const r of recs) await api('DELETE', '/records/' + r.id); // 撤销登记
        await api('DELETE', '/tickets/' + t.id); // 删菲票
      }
      await api('DELETE', '/productions/' + p.id); // 删床次
    }
    await api('DELETE', '/styles/' + s.id);
  }
  const custs = rows((await api('GET', '/customers?keyword=' + encodeURIComponent(MARK) + '&page_size=50')).data);
  for (const c of custs) await api('DELETE', '/customers/' + c.id);
  const left1 = rows((await api('GET', '/styles?keyword=' + encodeURIComponent(MARK))).data).length;
  const left2 = rows((await api('GET', '/customers?keyword=' + encodeURIComponent(MARK))).data).length;
  check(label, left1 === 0 && left2 === 0, `styles=${left1} customers=${left2}`);
}

(async () => {
  const mp = await automator.connect({ wsEndpoint: 'ws://localhost:9420' });
  console.log('connected');
  let page;
  try {
    // ===== B0 前置 =====
    const lg = await api('POST', '/auth/login', ADMIN);
    check('B0a API 登录', lg.status === 200 && lg.data && lg.data.access, `status=${lg.status}`);
    tok = lg.data.access;
    const mine = await api('GET', '/companies/mine');
    cid = rows(mine.data).find((c) => String(c.company_no) === '100001').id;
    // 必须先 UI 登录：其他套件（如 ui_fullchain F5）会把会话登出，
    // 无 token/无 X-Company-Id 的页面请求会被后端 400/401
    const home = await loginIfNeeded(mp);
    check('B0c UI 登录到首页', home.path === 'pages/index/index', home.path);
    const uiCid = await mp.evaluate(() => wx.getStorageSync('yisao_company_id'));
    check('B0d UI 公司上下文就绪', String(uiCid) === String(cid), `ui=${uiCid} api=${cid}`);
    await sweep('B0b 开跑前 MARK 残留清扫');

    // ===== B1 客户创建 =====
    page = await mp.navigateTo('/packages/admin/customers/index');
    await waitData(page, (d) => !d.loading, 10000, '客户页加载');
    const custTab = await findByText(await page.$$('.tab'), '全部客户');
    if (custTab) { await custTab.tap(); await sleep(800); }
    await (await findByText(await page.$$('.add-btn'), '添加客户')).tap();
    await waitData(page, (d) => d.formVisible, 5000, '客户弹层');
    const cInputs = await page.$$('.sheet input');
    await cInputs[0].input(CUST_NO);
    await cInputs[1].input(CUST_NAME);
    await cInputs[2].input('139' + RUN);
    await (await page.$('.sheet-save')).tap();
    await waitData(page, (d) => !d.formVisible, 8000, '客户保存');
    const cApi = rows((await api('GET', '/customers?keyword=' + encodeURIComponent(CUST_NAME))).data)[0];
    check('B1a API 客户落库(编号/名称/电话)', cApi && cApi.customer_no === CUST_NO
      && cApi.name === CUST_NAME && cApi.phone === '139' + RUN,
      cApi ? JSON.stringify(cApi).slice(0, 100) : '未找到');
    const cTexts = await page.$$('[class*="item-title"]');
    let seen = false;
    for (const el of cTexts) if ((await el.text()).indexOf(CUST_NAME) >= 0) seen = true;
    check('B1b UI 客户列表回显', seen);
    await shot(mp, path.join(SHOTS, 'admin_customer.png'));

    // ===== B2 款式创建 =====
    page = await mp.navigateTo('/packages/admin/styles/index');
    await waitData(page, (d) => !d.loading, 10000, '款式页加载');
    await (await page.$('.add-btn')).tap();
    await waitData(page, (d) => d.formVisible, 5000, '款式弹层');
    const sInputs = await page.$$('.sheet input');
    await sInputs[0].input(STY_NO);
    await sInputs[1].input(STY_NAME);
    await (await page.$('.sheet-save')).tap();
    await waitData(page, (d) => !d.formVisible, 8000, '款式保存');
    const sApi = rows((await api('GET', '/styles?keyword=' + encodeURIComponent(STY_NO))).data)[0];
    check('B2a API 款式落库', sApi && sApi.style_no === STY_NO && sApi.name === STY_NAME,
      sApi ? JSON.stringify(sApi).slice(0, 100) : '未找到');
    const sTexts = await page.$$('[class*="item-title"]');
    let seenS = false;
    for (const el of sTexts) if ((await el.text()).indexOf(STY_NO) >= 0) seenS = true;
    check('B2b UI 款式列表回显', seenS);

    // ===== B3 款式工序单价配置（⋮菜单 → 工序单价页）=====
    await mp.mockWxMethod('showActionSheet', { tapIndex: 2 });
    // 找到新款式卡片上的 ⋮（按卡片文本逐卡定位）
    const cards = await page.$$('.style-card');
    let menuEl = null;
    for (const c of cards) {
      if ((await c.text()).indexOf(STY_NO) >= 0) { menuEl = await c.$('.menu-dot'); break; }
    }
    check('B3a 找到新款式卡片菜单', !!menuEl);
    await menuEl.tap();
    await sleep(1500);
    await mp.restoreWxMethod('showActionSheet');
    page = await mp.currentPage();
    check('B3b 进入工序单价页', page.path === 'packages/admin/style-process/index', page.path);
    await waitData(page, (d) => !d.loading, 10000, '工序页加载');
    const procNames = (await page.data()).processNames || [];
    check('B3c 工序档案非空(可配置)', procNames.length >= 2, 'n=' + procNames.length);
    // picker 交互用 callMethod 驱动真实 handler（picker 原生组件自动化限制）
    await page.callMethod('onPickProcess', { detail: { value: '0' } });
    await page.callMethod('onPickProcess', { detail: { value: '1' } });
    await waitData(page, (d) => d.rows.length === 2, 5000, '添加2个工序');
    const priceInputs = await page.$$('.p-price');
    await priceInputs[0].input('0.85');
    await priceInputs[1].input('0.42');
    await (await page.$('.btn-info')).tap(); // 保存配置
    await waitData(page, (d) => !d.saving, 8000, '保存配置');
    await sleep(1000);
    const sApi2 = await api('GET', '/styles/' + sApi.id);
    const items2 = sApi2.data.process_items || sApi2.data.processes || [];
    const prices = items2.map((it) => Number(it.price)).sort();
    check('B3d API 工序配置落库(0.42/0.85)', JSON.stringify(prices) === '[0.42,0.85]',
      JSON.stringify(items2).slice(0, 120));
    const cfgProcs = items2.map((it) => it.process_id || it.process);

    // ===== B4 编菲全链 =====
    page = await mp.navigateTo('/packages/admin/production-create/index');
    await waitData(page, (d) => d.stage === 'form', 8000, '编菲表单');
    await tapSel(page, '.field'); // 款式 * 字段 → 打开选款面板
    await waitData(page, (d) => d.stylePanelVisible, 5000, '选款面板');
    const search = await page.$('.search');
    await search.input(STY_NO);
    const searchBtn = await findByText(await page.$$('.search-btn'), '搜索');
    if (searchBtn) await searchBtn.tap(); else await page.callMethod('onStyleSearch', {});
    await waitData(page, (d) => (d.styleOptions || []).length >= 1, 8000, '搜到款式');
    await tapSel(page, '.style-item');
    await waitData(page, (d) => d.style && d.style.id === sApi.id, 5000, '选中款式');
    const fInputs = await page.$$('input');
    // 表单 input：床次号 / 总量 / 面料（款式字段是 view 伪输入，不在 input 里）
    if (fInputs.length < 2) throw new Error('表单 input 数量异常: ' + fInputs.length);
    await fInputs[0].input(BED_NO);
    await fInputs[1].input('70');
    await (await findByText(await page.$$('button'), '保存并进入编菲')).tap();
    await waitData(page, (d) => d.stage === 'matrix', 10000, '进入矩阵');
    let prod = rows((await api('GET', '/productions?keyword=' + encodeURIComponent(STY_NO) + '&page_size=20')).data)
      .find((p) => p.bed_no === BED_NO);
    check('B4a API 床次落库(款/床/总量70)', !!prod && prod.total_qty === 70,
      prod ? `bed=${prod.bed_no} qty=${prod.total_qty}` : '未找到');
    check('B4b UI 矩阵上下文(床次号)', (await page.data()).prodBedNo === BED_NO);

    // 颜色①：点选面板（痛点：颜色手写繁琐）——「＋添加」开面板，点厂内常用色 chip 直接上色
    await (await page.$('.chip-add')).tap();
    await waitData(page, (d) => (d.colorOptions || []).length > 0, 5000, '颜色面板');
    const copts = (await page.data()).colorOptions;
    const pick = copts.find((o) => !o.added);
    await page.callMethod('onPickColorChip', { currentTarget: { dataset: { name: pick.name } } });
    const dm = await waitData(page, (d) => (d.colors || []).indexOf(pick.name) >= 0, 5000, '点选加颜色');
    check('B4c UI 颜色点选加入且默认尺码就绪', dm.colors.length === 1 && (dm.sizes || []).length >= 1,
      `colors=${JSON.stringify(dm.colors)} sizes=${JSON.stringify(dm.sizes)}`);

    // 颜色②：「自定义…」手写兜底（wx.showModal editable → mock 返回 content）
    await mp.mockWxMethod('showModal', { confirm: true, content: '测试蓝' });
    await page.callMethod('onOpenColorCustom');
    const dm2 = await waitData(page, (d) => (d.colors || []).indexOf('测试蓝') >= 0, 5000, '手写加颜色');
    await mp.restoreWxMethod('showModal');
    check('B4c2 UI 自定义颜色兜底', dm2.colors.length === 2 && dm2.colors.indexOf('测试蓝') >= 0,
      `colors=${JSON.stringify(dm2.colors)}`);

    // 点格子录件数（sheet 是 numeric-keypad：callMethod 驱动真实 handler）
    const cellEls = await page.$$('.mtx-cell');
    let cellEl = null;
    for (const el of cellEls) if ((await el.text()).trim() === '·') { cellEl = el; break; }
    check('B4d 找到待录格子', !!cellEl);
    await cellEl.tap();
    await waitData(page, (d) => d.sheetVisible, 5000, '件数弹层');
    await page.callMethod('onSheetInput', { detail: { value: '70' } });
    await page.callMethod('onSheetConfirm');
    await waitData(page, (d) => d.grandTotal === 70, 5000, '总件数70');

    // 扎号视图：70/20 → 4 扎预览
    const bundleTab = await findByText(await page.$$('.tab'), '扎号');
    await bundleTab.tap();
    await waitData(page, (d) => (d.bundleRows || []).length > 0, 5000, '拆扎预览');
    const bd = await page.data();
    check('B4e UI 拆扎预览(70/上限20→4扎)', bd.bundleRows.length === 4 && bd.bundleTotal === 70,
      `rows=${bd.bundleRows.length} total=${bd.bundleTotal}`);
    const startNo = Number(bd.startBundleNo || '1');
    await shot(mp, path.join(SHOTS, 'admin_matrix.png'));

    await (await page.$('.save-btn')).tap(); // 保存菲票
    await sleep(2500); // toast + navigateBack
    // 轮询等 tickets/batch 落库（最长 12s）
    let tickets = [];
    for (let i = 0; i < 30 && !tickets.length; i++) {
      await sleep(400);
      if (prod) tickets = rows((await api('GET', '/tickets?production_id=' + prod.id + '&page_size=50')).data);
    }
    check('B4f API 菲票落库(70件→4扎)', tickets.length === 4,
      `n=${tickets.length}`);
    const bNo = tickets.map((t) => Number(t.bundle_no)).sort((a, b) => a - b);
    const tQty = tickets.reduce((s, t) => s + t.qty, 0);
    const expect = [0, 1, 2, 3].map((i) => startNo + i);
    check('B4g API 扎号自起始号顺延/件数拆分正确', JSON.stringify(bNo) === JSON.stringify(expect) && tQty === 70,
      `bundles=${bNo} expect=${expect} qty=${tQty}`);
    const tk0 = tickets[0];
    check('B4h API 菲票字段(颜色/尺码/款)', tk0.color === pick.name
      && (dm.sizes || []).indexOf(tk0.size) >= 0
      && String(tk0.style_no || '').indexOf(STY_NO) >= 0,
      JSON.stringify(tk0).slice(0, 140));

    // ===== B5 登记 + sync-process（后端补测：need_confirm → confirm → 审计）=====
    const rec0 = await api('POST', '/records', {
      ticket_id: tk0.id, process_id: cfgProcs[0], qty: 10
    });
    check('B5a API 生成登记(供 sync 二次确认)', rec0.status === 201, `status=${rec0.status} ${JSON.stringify(rec0.data).slice(0, 80)}`);
    const sync0 = await api('POST', `/styles/${sApi.id}/sync-process`, {});
    check('B5b sync-process 首调需确认(涉及1票)', sync0.status === 200 && sync0.data.need_confirm === true
      && sync0.data.ticket_count === 1, JSON.stringify(sync0.data).slice(0, 100));
    const sync1 = await api('POST', `/styles/${sApi.id}/sync-process`, { confirm: true });
    check('B5c sync-process 确认后同步成功', sync1.status === 200 && sync1.data.synced === true,
      JSON.stringify(sync1.data).slice(0, 80));
    const logs = rows((await api('GET', '/audit-logs?page_size=10')).data);
    check('B5d 操作日志记录同步动作', logs.some((l) => (l.message || '').indexOf('同步款式') >= 0
      && (l.message || '').indexOf(STY_NO) >= 0), '未找到日志');
    const sync2 = await api('POST', `/styles/${sApi.id}/sync-process`, { confirm: true });
    check('B5e 幂等：无记录票数0直接同步', sync2.data.synced === true && sync2.data.ticket_count === 1,
      JSON.stringify(sync2.data).slice(0, 80));

    // ===== B6 请求失败兜底（TC12-06）=====
    await mp.mockWxMethod('request', function () { return { errMsg: 'request:fail mock-500' }; });
    let cpage = await mp.reLaunch('/packages/admin/customers/index');
    await sleep(2500);
    cpage = await mp.currentPage();
    const dErr = await cpage.data();
    const hasErr = !!(dErr.error || dErr.groupError);
    check('B6a 加载失败进入兜底态(无白屏)', hasErr, JSON.stringify({ error: dErr.error, groupError: dErr.groupError }));
    const retryBtn = await findByText(await cpage.$$('button'), '重新加载');
    check('B6b 兜底态提供重新加载按钮', !!retryBtn);
    await shot(mp, path.join(SHOTS, 'admin_fallback.png'));
    await mp.restoreWxMethod('request');
    if (retryBtn) {
      await retryBtn.tap();
      await waitData(cpage, (d) => !d.loading && !d.error, 10000, '恢复加载');
      check('B6c 恢复后重新加载成功', true);
    }
  } finally {
    for (const m of ['request', 'showModal', 'showActionSheet', 'scanCode']) {
      try { await mp.restoreWxMethod(m); } catch (e) { /* */ }
    }
    // ===== 收尾清扫 =====
    try { await sweep('B9 收尾 MARK 清扫'); } catch (e) { console.log('sweep error:', e.message); }
  }
  const fails = results.filter((r) => !r.ok);
  console.log('\n===== ADMIN FUSED(UI+API): %d passed, %d failed =====', results.length - fails.length, fails.length);
  fails.forEach((f) => console.log('  -', f.name));
  process.exit(fails.length ? 1 : 0);
})().catch((e) => {
  console.error('RUN ERROR:', e.message || e);
  if (e.stack) console.error(e.stack.split('\n').slice(1, 4).join('\n'));
  process.exit(2);
});
