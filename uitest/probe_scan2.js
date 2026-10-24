// -*- coding: utf-8 -*-
/**
 * probe_scan2.js —— 扫菲页 P0 增强（SF3）：
 *   P1 满扎徽标：工序 chip「累计/扎数」（registered_qty/cap；cap=null 只显示累计；字段缺失不渲染）
 *   P2 手输码兜底动线：idle 入口 → 双输入弹层 → 404「未找到菲票」提示
 *   P3 候选列表：床次/款号/色码/扎号/数量渲染 → 选中进既有登记流程（等价扫码成功态）
 *   P4 离线显式暂存：列表摘要 → 手动重发清空队列
 *
 * mock 说明（沿用 ui_scan.js 教训：mock 自定义函数返回 errMsg 对象触发 fail 分支）：
 *   wx.request 用「对象 mock」：返回值直接进 success 回调（{statusCode,data}），
 *   按阶段整体切换（404 → 候选 200 → process-selector 200 → 重发 200），
 *   不依赖 mock 函数收不收原调用参数（automator 桥接对参数传递不可靠）。
 * 前置: cli.bat auto --auto-port 9420 已起；登录态可复用（P1/P2 不依赖后端）。
 * 产物: uitest/shots/probe_scan2_*.png + PASS/FAIL 清单
 * 退出码: 0=全过 / 1=有 FAIL / 2=运行异常 / 3=9420 不可用(脚本交付待验证)
 */
const path = require('path');
const fs = require('fs');
const automator = require('miniprogram-automator');
const shot = require('./helpers/shot');

const SHOTS = path.join(__dirname, 'shots');
if (!fs.existsSync(SHOTS)) fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond });
  console.log((cond ? 'PASS' : 'FAIL'), name, cond ? '' : '| ' + String(detail).slice(0, 160));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitData(page, fn, timeout = 8000, label = 'cond') {
  const t0 = Date.now();
  let d = await page.data();
  while (!fn(d)) {
    if (Date.now() - t0 > timeout) throw new Error('waitData 超时: ' + label + ' | last=' + JSON.stringify(d).slice(0, 160));
    await sleep(400);
    d = await page.data();
  }
  return d;
}

async function loginIfNeeded(mp) {
  let page = await mp.reLaunch('/pages/login/index');
  await page.waitFor(1500);
  page = await mp.currentPage();
  if (page.path === 'pages/index/index') return page; // 已登录

  const tabs = await page.$$('.tab');
  for (const el of tabs) if ((await el.text()).indexOf('密码') >= 0) { await el.tap(); break; }
  await sleep(400);
  const inputs = await page.$$('input');
  if (inputs.length >= 2) { await inputs[0].input('13800000001'); await inputs[1].input('yisao1234'); }
  const agree = await page.$('.agree');
  if (agree) await agree.tap();
  const btns = await page.$$('button');
  for (const el of btns) if ((await el.text()).trim() === '登录') { await el.tap(); break; }
  await sleep(3000);
  page = await mp.currentPage();
  if ((await page.data()).stage === 'pick') {
    const coItems = await page.$$('.co-item');
    check('L0 登录出公司选择', coItems.length >= 1, 'n=' + coItems.length);
    await coItems[0].tap();
    await sleep(3000);
    page = await mp.currentPage();
  }
  check('L0b 登录后到首页', page && page.path === 'pages/index/index', page && page.path);
  return page;
}

(async () => {
  let mp;
  try {
    mp = await automator.connect({ wsEndpoint: 'ws://localhost:9420' });
  } catch (e) {
    console.log('SKIPPED: 自动化端口 9420 不可用（' + (e.message || e) + '）');
    console.log('待验证：请先启动 cli.bat auto --auto-port 9420 后重跑 probe_scan2.js');
    process.exit(3);
  }
  console.log('connected');

  try {
    let page = await loginIfNeeded(mp);

    // ===== P1 满扎徽标：mock 扫码失败停留 idle → setData 注入带 cap 数据的工序 =====
    await mp.mockWxMethod('scanCode', function () { return { errMsg: 'scanCode:fail probe-mock' }; });
    page = await mp.switchTab('/pages/scan/index');
    await page.waitFor(1800); // onShow autoScan 吃掉 fail mock → 停留 idle（_cameraDone 置位不再弹相机）
    const d0 = await page.data();
    check('P1-0 扫码失败停留 idle（不弹相机不白屏）', d0.state === 'idle', 'state=' + d0.state);

    await page.setData({
      state: 'process',
      ticket: { id: 9001, styleNo: 'ST001', styleName: '徽标测试款', bedNo: '7', bundleNo: '3', color: '黑色', size: 'L', qty: 100, registeredQty: null },
      processes: [
        { id: 1, name: '车缝', price: '0.50', priceText: '¥0.50', unpriced: false, registeredQty: 96, capQty: 100 },
        { id: 2, name: '锁边', price: '0.30', priceText: '¥0.30', unpriced: false, registeredQty: 50, capQty: null },
        { id: 3, name: '剪线', price: '0.20', priceText: '¥0.20', unpriced: false, registeredQty: null, capQty: null }
      ],
      selectedId: 0,
      selectedName: '',
      qtyValue: ''
    });
    await sleep(400);
    const chips1 = await page.$$('.proc-chip');
    check('P1a 三工序 chip 渲染', chips1.length === 3, 'n=' + chips1.length);
    const badges = await page.$$('.proc-cap');
    check('P1b 徽标仅渲染有累计的工序(2/3)', badges.length === 2, 'n=' + badges.length);
    if (badges.length >= 2) {
      const t1 = await badges[0].text();
      const t2 = await badges[1].text();
      check('P1c 满扎样式 96/100', t1.replace(/\s/g, '') === '96/100', t1);
      check('P1d cap=null 只显示累计数 50', t2.replace(/\s/g, '') === '50', t2);
    }
    await shot(mp, path.join(SHOTS, 'probe_scan2_01_badge.png'));

    // ===== P2 手输码兜底：入口 → 双输入弹层 → 404 提示 =====
    await page.setData({ state: 'idle', ticket: null, processes: [], selectedId: 0, selectedName: '', qtyValue: '' });
    await sleep(300);
    const entry = await page.$('.lookup-entry');
    check('P2a idle 态手输入口存在', !!entry);
    if (!entry) throw new Error('手输入口缺失，后续用例无法继续');
    await entry.tap();
    await waitData(page, (d) => d.lookupShow === true, 3000, '手输弹层打开');
    const inputs = await page.$$('.lookup-input');
    check('P2b 弹层双输入框', inputs.length === 2, 'n=' + inputs.length);
    await inputs[0].input('7');
    await inputs[1].input('3');
    // 阶段 mock：lookup 404（0 命中 / 接口未就绪同分支）
    await mp.mockWxMethod('request', { statusCode: 404, data: { detail: 'Not Found' } });
    await (await page.$('.lookup-search')).tap();
    await waitData(page, (d) => !!d.lookupError, 5000, '404 提示');
    const dErr = await page.data();
    check('P2c 404 提示「未找到菲票」', dErr.lookupError.indexOf('未找到菲票') >= 0 && dErr.lookupError.indexOf('床次号和扎号') >= 0,
      dErr.lookupError);
    check('P2d 404 后弹层未白屏仍开启', dErr.lookupShow === true && dErr.lookupLoading === false);
    await mp.restoreWxMethod('request');
    await shot(mp, path.join(SHOTS, 'probe_scan2_02_lookup404.png'));

    // ===== P3 候选列表渲染 + 选中进登记流程 =====
    await mp.mockWxMethod('request', {
      statusCode: 200,
      data: [{ id: 9002, bed_no: '7', bundle_no: '3', color: '黑色', size: 'L', qty: 100, style_no: 'ST001', style_name: '手输测试款' }]
    });
    await (await page.$('.lookup-search')).tap();
    await waitData(page, (d) => (d.lookupRows || []).length === 1, 5000, '候选列表');
    const rows = await page.$$('.lookup-row');
    check('P3a 候选行渲染(1 条)', rows.length === 1, 'n=' + rows.length);
    if (rows.length === 1) {
      const rowText = await rows[0].text();
      const need = ['ST001', '7', '黑色', '3', '100'];
      check('P3b 行含款号/床次/色码/扎号/数量', need.every((k) => rowText.indexOf(k) >= 0), rowText.slice(0, 120));
      // 阶段 mock：选中后 loadProcesses 的 process-selector 返回
      await mp.mockWxMethod('request', {
        statusCode: 200,
        data: { results: [{ id: 1, name: '车缝', is_fixed: true, price: '0.50' }, { id: 2, name: '锁边', is_fixed: true, price: '0.30' }], single: false }
      });
      await rows[0].tap();
      await waitData(page, (d) => d.state === 'process' && d.ticket && String(d.ticket.bundleNo) === '3', 6000, '选中进流程');
      const dPick = await page.data();
      check('P3c 选中进入工序选择(等价扫码成功态)', dPick.state === 'process' && dPick.ticket.id === 9002,
        'state=' + dPick.state + ' id=' + (dPick.ticket || {}).id);
      check('P3d 选中后弹层关闭', dPick.lookupShow === false);
      check('P3e 床次/款号带出', String(dPick.ticket.bedNo) === '7' && dPick.ticket.styleNo === 'ST001',
        `bed=${dPick.ticket.bedNo} style=${dPick.ticket.styleNo}`);
      const chips2 = await page.$$('.proc-chip');
      check('P3f 工序 chip 渲染(2)', chips2.length === 2, 'n=' + chips2.length);
    }
    await mp.restoreWxMethod('request');
    await shot(mp, path.join(SHOTS, 'probe_scan2_03_lookup_pick.png'));

    // ===== P4 离线显式暂存列表 + 手动重发 =====
    await page.setData({ state: 'idle', ticket: null, processes: [], selectedId: 0, selectedName: '', qtyValue: '' });
    await mp.evaluate(() => {
      wx.setStorageSync('yisao_offline_queue', [
        { url: '/records', method: 'POST', data: { ticket_id: 9001, process_id: 1, qty: 12 }, header: {}, ts: Date.now() - 60000 },
        { url: '/records', method: 'POST', data: { ticket_id: 9002, process_id: 1, qty: 8 }, header: {}, ts: Date.now() }
      ]);
    });
    await page.setData({ offlineCount: 2 });
    await sleep(300);
    const offEntry = await page.$('.offline-entry');
    check('P4a 离线暂存入口存在', !!offEntry);
    if (offEntry) {
      await offEntry.tap();
      await waitData(page, (d) => d.offlineShow === true && (d.offlineRows || []).length === 2, 3000, '离线列表');
      const dOff = await page.data();
      check('P4b 列表条目摘要(件数带出)', dOff.offlineRows[0].qty === 12 && dOff.offlineRows[1].qty === 8,
        JSON.stringify(dOff.offlineRows).slice(0, 140));
      await shot(mp, path.join(SHOTS, 'probe_scan2_04_offline.png'));
      // 阶段 mock：重发成功（flush 逐条 POST /records）
      await mp.mockWxMethod('request', { statusCode: 200, data: { id: 1 } });
      await (await page.$('.offline-resend')).tap();
      await sleep(3000); // flush 网络往返 + 页面 2s 后回读
      const q = await mp.evaluate(() => wx.getStorageSync('yisao_offline_queue').length);
      check('P4c 手动重发后队列清空', q === 0, 'queue=' + q);
      const dEnd = await page.data();
      check('P4d 页面离线计数归零', (dEnd.offlineCount || 0) === 0, 'offlineCount=' + dEnd.offlineCount);
      await mp.restoreWxMethod('request');
    }
    await shot(mp, path.join(SHOTS, 'probe_scan2_05_done.png'));
  } finally {
    try { await mp.restoreWxMethod('scanCode'); } catch (e) { /* ignore */ }
    try { await mp.restoreWxMethod('request'); } catch (e) { /* ignore */ }
    // 不 close：保留自动化窗口供继续操作
  }

  const fails = results.filter((r) => !r.ok);
  console.log('\n===== PROBE SCAN2: %d passed, %d failed =====', results.length - fails.length, fails.length);
  fails.forEach((f) => console.log('  -', f.name));
  process.exit(fails.length ? 1 : 0);
})().catch((e) => {
  console.error('RUN ERROR:', e.message || e);
  process.exit(2);
});
