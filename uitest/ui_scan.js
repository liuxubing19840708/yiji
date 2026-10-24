// -*- coding: utf-8 -*-
/**
 * UI 扫菲全流程测试（miniprogram-automator）
 * mock wx.scanCode 注入真实菲票二维码内容 → 选工序 → 确认登记 → 连续扫码第二张 → 取消退出回首页。
 * 前置: cli.bat auto --auto-port 9420 已起；后端 8000 在跑。
 * 产物: uitest/shots/scan_*.png + PASS/FAIL 清单
 */
const path = require('path');
const fs = require('fs');
const automator = require('miniprogram-automator');
const shot = require('./helpers/shot');

const SHOTS = path.join(__dirname, 'shots');
if (!fs.existsSync(SHOTS)) fs.mkdirSync(SHOTS, { recursive: true });

const QR1 = '100001|1|ST001|1|碎花蓝|M|35';   // 床次1 扎号1
const QR2 = '100001|1|ST001|10|黑色|L|20';    // 床次1 扎号10

const results = [];
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond });
  console.log((cond ? 'PASS' : 'FAIL'), name, cond ? '' : '| ' + String(detail).slice(0, 140));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 轮询等待页面 data 满足条件 */
async function waitData(page, fn, timeout = 8000, label = 'cond') {
  const t0 = Date.now();
  let d = await page.data();
  while (!fn(d)) {
    if (Date.now() - t0 > timeout) throw new Error('waitData 超时: ' + label + ' | last=' + JSON.stringify(d).slice(0, 120));
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
  const dbg = await page.data();
  console.log('[debug] after login tap:', JSON.stringify({
    path: page.path, stage: dbg.stage, submitting: dbg.submitting,
    agreed: dbg.agreed, phone: dbg.phone, pwdLen: (dbg.password || '').length
  }));
  if ((await page.data()).stage === 'pick') {
    const coItems = await page.$$('.co-item');
    check('S0 登录出公司选择', coItems.length >= 1, 'n=' + coItems.length);
    await coItems[0].tap(); // 辉腾制衣（演示）= 公司2
    await sleep(3000);
    page = await mp.currentPage();
  }
  check('S0 登录后到首页', page && page.path === 'pages/index/index', page && page.path);
  return page;
}

(async () => {
  const mp = await automator.connect({ wsEndpoint: 'ws://localhost:9420' });
  console.log('connected');

  try {
    // 0) 清存储保证干净状态 → 完整登录（进辉腾制衣）
    await mp.evaluate(() => wx.clearStorageSync());
    let page = await loginIfNeeded(mp);
    await shot(mp, path.join(SHOTS, 'scan_00_home.png'));

    // 1) 注入第一张菲票 → 进扫菲页，onShow 自动扫码应直接解析出菲票卡
    await mp.mockWxMethod('scanCode', { result: QR1 });
    page = await mp.switchTab('/pages/scan/index');
    const d1 = await waitData(page, (d) => d.state === 'process' || d.state === 'qty', 10000, '解析菲票');
    const t = d1.ticket || {};
    check('S1 扫码解析出菲票卡', !!d1.ticket, JSON.stringify(d1).slice(0, 100));
    check('S1b 扎号/床次正确', t.bundleNo === '1' && String(t.bedNo) === '1', `bundleNo=${t.bundleNo} bedNo=${t.bedNo}`);
    check('S1c 款式编号正确', t.styleNo === 'ST001', 'styleNo=' + t.styleNo);
    check('S1d 工序数=6(固定)', (d1.processes || []).length === 6, 'n=' + (d1.processes || []).length);
    await shot(mp, path.join(SHOTS, 'scan_01_ticket.png'));

    // 2) 选第一个工序 → 进件数确认（件数缺省=菲票扎数）
    const chips = await page.$$('.proc-chip');
    check('S2 工序 chip 渲染', chips.length === 6, 'n=' + chips.length);
    await chips[0].tap();
    const d2 = await waitData(page, (d) => d.state === 'qty', 6000, '选工序');
    check('S2b 件数缺省=菲票数量35', d2.qtyValue === '35', 'qtyValue=' + d2.qtyValue);
    check('S2c 选中的工序显示单价', d2.selectedName !== '', 'proc=' + d2.selectedName);
    await shot(mp, path.join(SHOTS, 'scan_02_qty.png'));

    // 3) 确认登记 → 成功 toast + 今日累计刷新 + 回扫码态（连续扫码）
    //    关键时序：submit 一发出就换 mock 为 QR2——900ms 后的自动连续扫码会吃到它
    const todayBefore = d1.todayQty;
    await (await page.$('.submit-btn')).tap();
    await mp.mockWxMethod('scanCode', { result: QR2 });
    const d3 = await waitData(page, (d) => d.state === 'idle', 8000, '登记完成回idle');
    await sleep(1500); // 等 refreshToday
    const d3b = await page.data();
    check('S3 提交后回扫码态(连续扫码)', d3.state === 'idle');
    check('S3b 今日累计刷新', d3b.todayQty !== null && d3b.todayQty >= 0, `before=${todayBefore} after=${d3b.todayQty}`);
    await shot(mp, path.join(SHOTS, 'scan_03_registered.png'));

    // 4) 连续扫码第二张：900ms autoScan 已吃到 QR2 mock，直接等解析出扎号10
    const d4 = await waitData(page, (d) => d.state === 'process' && d.ticket
                                    && d.ticket.bundleNo === '10', 12000, '第二张解析');
    check('S4 连续扫码第二张解析', d4.ticket && d4.ticket.bundleNo === '10', `bundleNo=${(d4.ticket || {}).bundleNo}`);
    await shot(mp, path.join(SHOTS, 'scan_04_ticket2.png'));
    const chips2 = await page.$$('.proc-chip');
    if (chips2.length) { await chips2[1].tap(); } // 换个工序（卷边）
    await waitData(page, (d) => d.state === 'qty', 6000, '第二张选工序');
    // 5) 预注入「取消失败」：submit 后 900ms 的 autoScan 将吃到 fail cancel → 应自动回首页
    //    （Error 实例/throw 都会卡死桥接，返回 fail errMsg 对象是唯一能正确触发 fail 回调的形式）
    await mp.mockWxMethod('scanCode', function () { return { errMsg: 'scanCode:fail cancel' }; });
    await (await page.$('.submit-btn')).tap();
    await waitData(page, (d) => d.state === 'idle', 8000, '第二张登记');
    check('S4b 第二张登记完成回扫码态', (await page.data()).state === 'idle');
    await shot(mp, path.join(SHOTS, 'scan_05_registered2.png'));
    await sleep(2500); // 等 fail-cancel 分支触发 switchTab
    const cur = await mp.currentPage();
    check('S5 取消扫码自动回首页', cur.path === 'pages/index/index', cur.path);
    await shot(mp, path.join(SHOTS, 'scan_06_cancel_home.png'));

    // 6) 相册识别（痛点 #6，切片 §22.2）：idle 态「从相册识别」按钮走同一解析管线。
    //    真机相册选图无法自动化，这里 mock scanCode 返回 QR2 验证按钮→handleCode 链路。
    await mp.mockWxMethod('scanCode', { result: QR2 });
    page = await mp.switchTab('/pages/scan/index');
    await sleep(2500); // onShow autoScan 先吃掉一次 mock（解析进流程）
    await page.setData({ state: 'idle', ticket: null, processes: [], selectedId: 0, selectedName: '', qtyValue: '' });
    await sleep(300);
    const albumBtn = await page.$('.scan-btn-album');
    check('S6a 相册识别按钮存在', !!albumBtn, 'idle 态未找到 .scan-btn-album');
    if (albumBtn) {
      await albumBtn.tap();
      const d6 = await waitData(page, (d) => d.state === 'process' && d.ticket
                                      && d.ticket.bundleNo === '10', 10000, '相册识别解析');
      check('S6b 相册识码走通解析管线', d6.ticket && d6.ticket.bundleNo === '10',
        `bundleNo=${(d6.ticket || {}).bundleNo}`);
    }
    await shot(mp, path.join(SHOTS, 'scan_07_album.png'));
  } finally {
    try { await mp.restoreWxMethod('scanCode'); } catch (e) { /* ignore */ }
    // 不 close：保留自动化窗口供继续操作
  }

  const fails = results.filter((r) => !r.ok);
  console.log('\n===== SCAN FLOW: %d passed, %d failed =====', results.length - fails.length, fails.length);
  fails.forEach((f) => console.log('  -', f.name));
  process.exit(fails.length ? 1 : 0);
})().catch((e) => {
  console.error('RUN ERROR:', e.message || e);
  process.exit(2);
});
