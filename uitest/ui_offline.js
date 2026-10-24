// -*- coding: utf-8 -*-
/**
 * UI 离线队列测试（TC07-22）：断网(mock wx.request fail)提交登记 → 入本地队列 →
 * 恢复网络后 app.onShow 触发 flushOfflineQueue 补传 → 落库 + 队列清空。
 * 前置: cli.bat auto --auto-port 9420；后端 8000。
 */
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const automator = require('miniprogram-automator');
const shot = require('./helpers/shot');

const BE_DIR = 'D:\\project\\扫菲\\backend';

/** python 侧通道：清理本套件在扎号2×车领上的历史登记残留（重复登记会让补传 400、队列清不掉） */
function pyExec(pyOneLine) {
  return execSync('.venv\\Scripts\\python.exe manage.py shell -c "' + pyOneLine + '"', {
    cwd: BE_DIR, env: { ...process.env, PYTHONIOENCODING: 'utf-8' }, encoding: 'utf-8'
  });
}
function precleanRecords() {
  const py = "from apps.records.models import Record; from apps.catalog.models import Ticket; "
    + "tks=Ticket.objects.filter(company_id=2, production__bed_no='1', production__style__style_no='ST001', bundle_no='2'); "
    + "n=Record.objects.filter(ticket__in=tks, process__name='车领', user__phone='13800000001').delete(); print('cleaned')";
  return pyExec(py).includes('cleaned');
}

const SHOTS = path.join(__dirname, 'shots');
if (!fs.existsSync(SHOTS)) fs.mkdirSync(SHOTS, { recursive: true });

const QR3 = '100001|1|ST001|2|黑色|L|20'; // 床次1 扎号2（预先清掉该票上测试账号的车领登记）

const results = [];
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond });
  console.log((cond ? 'PASS' : 'FAIL'), name, cond ? '' : '| ' + String(detail).slice(0, 140));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitData(page, fn, timeout = 8000, label = 'cond') {
  const t0 = Date.now();
  let d = await page.data();
  while (!fn(d)) {
    if (Date.now() - t0 > timeout) throw new Error('waitData 超时: ' + label + ' | ' + JSON.stringify(d).slice(0, 120));
    await sleep(400);
    d = await page.data();
  }
  return d;
}

async function loginIfNeeded(mp) {
  let page = await mp.reLaunch('/pages/login/index');
  await page.waitFor(1500);
  page = await mp.currentPage();
  if (page.path === 'pages/index/index') return page;
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
    if (coItems.length) await coItems[0].tap();
    await sleep(3000);
    page = await mp.currentPage();
  }
  check('O0 登录到首页', page.path === 'pages/index/index', page.path);
  return page;
}

(async () => {
  const mp = await automator.connect({ wsEndpoint: 'ws://localhost:9420' });
  console.log('connected');
  try {
    // 0) 清离线队列残留 → 清历史登记残留 → 登录
    await mp.evaluate(() => { wx.removeStorageSync('yisao_offline_queue'); });
    check('O-0 清理历史登记残留', precleanRecords());
    await loginIfNeeded(mp);

    // 1) 正常网络下扫码解析（此时 request 未 mock）→ 选工序到 qty 态
    await mp.mockWxMethod('scanCode', { result: QR3 });
    let page = await mp.switchTab('/pages/scan/index');
    const d1 = await waitData(page, (d) => d.state === 'process' || d.state === 'qty', 10000, '解析扎号2');
    check('O1 扫码解析出扎号2', d1.ticket && d1.ticket.bundleNo === '2', `bundle=${(d1.ticket || {}).bundleNo}`);
    const chips = await page.$$('.proc-chip');
    if (d1.state === 'process') { await chips[0].tap(); } // 车领
    await waitData(page, (d) => d.state === 'qty', 6000, '选工序');

    // 2) 注入"断网"：mock wx.request 全部失败 → 提交应入离线队列
    await mp.mockWxMethod('request', function () { return { errMsg: 'request:fail offline-mock' }; });
    await (await page.$('.submit-btn')).tap();
    const d2 = await waitData(page, (d) => d.state === 'idle', 8000, '离线提交回idle');
    check('O2 提交后回扫码态', d2.state === 'idle');
    const q1 = await mp.evaluate(() => wx.getStorageSync('yisao_offline_queue').length);
    check('O3 登记入离线队列(storage)', q1 >= 1, 'queue=' + q1);
    await sleep(1000);
    const d3 = await page.data();
    check('O3b 页面离线计数>0', (d3.offlineCount || 0) >= 1, 'offlineCount=' + d3.offlineCount);
    await shot(mp, path.join(SHOTS, 'offline_01_queued.png'));

    // 3) 恢复网络 → 触发 app.onShow 补传
    await mp.restoreWxMethod('request');
    await mp.evaluate(() => { getApp().onShow(); });
    await sleep(2500); // 等 flush 网络往返 + storage 落盘
    const q2 = await mp.evaluate(() => wx.getStorageSync('yisao_offline_queue').length);
    check('O4 补传后队列清空', q2 === 0, 'queue=' + q2);
    page = await mp.reLaunch('/pages/scan/index');
    await page.waitFor(1200);
    const d4 = await page.data();
    check('O4b 页面离线计数归零', (d4.offlineCount || 0) === 0, 'offlineCount=' + d4.offlineCount);
    await shot(mp, path.join(SHOTS, 'offline_02_flushed.png'));
  } finally {
    try { await mp.restoreWxMethod('scanCode'); } catch (e) { /* */ }
    try { await mp.restoreWxMethod('request'); } catch (e) { /* */ }
  }
  const fails = results.filter((r) => !r.ok);
  console.log('\n===== OFFLINE FLOW: %d passed, %d failed =====', results.length - fails.length, fails.length);
  fails.forEach((f) => console.log('  -', f.name));
  process.exit(fails.length ? 1 : 0);
})().catch((e) => {
  console.error('RUN ERROR:', e.message || e);
  process.exit(2);
});
