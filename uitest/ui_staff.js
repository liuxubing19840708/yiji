// -*- coding: utf-8 -*-
/**
 * UI + 后端 融合测试：员工端窗口全链（员工/管理员窗口差异专项）
 *
 * 覆盖：
 *   T1 员工短信登录   UI 验证码登录 ↔ 非管理员登录态
 *   T2 员工首页差异   宫格=我的薪酬/工作中 两项（无管理员模块）+ 员工统计卡
 *   T3 员工扫菲动线   员工身份 mock 扫码 → 选工序 → 登记 ↔ API 落库
 *   T4 工作中页       床次进度 + 本人今日登记可见 + UI 撤销本人登记
 *   T5 通知页(员工)   列表加载正常
 *   T6 我的页(员工)   无管理员徽标 + 登录人/品牌脚注「衣计」+ 员工专属菜单「与我相关的变更」（切片 §22.3）
 *   T7 退出登录       回登录表单
 *
 * 前置: 后端 8000；开发者工具 cli.bat auto --auto-port 9420。
 * 幂等: T3 登记在 T4 内撤销，无业务残留；清理只删 SMSCode。两连跑结果一致。
 */
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const automator = require('miniprogram-automator');
const shot = require('./helpers/shot');

const SHOTS = path.join(__dirname, 'shots');
if (!fs.existsSync(SHOTS)) fs.mkdirSync(SHOTS, { recursive: true });

const API = 'http://127.0.0.1:8000/api';
const EMP = { phone: '13800000003', name: '李翠花', uid: 8 };
const ADMIN = { phone: '13800000001', password: 'yisao1234' };
const BE_DIR = 'D:\\project\\扫菲\\backend';
const ADMIN_MODULE_NAMES = ['款式管理', '生产管理', '薪酬管理', '员工管理', '客户管理',
  '完成汇总', '操作日志', '协助扫菲', '企业二维码', '指定工序', '订单薪酬'];

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
    if (Date.now() - t0 > timeout) throw new Error('waitData 超时: ' + label + ' | ' + JSON.stringify(d).slice(0, 150));
    await sleep(400);
    d = await page.data();
  }
  return d;
}
async function tapSel(page, sel, tries = 12) {
  for (let i = 0; i < tries; i++) {
    const el = await page.$(sel);
    if (el) { await el.tap(); return el; }
    await sleep(400);
  }
  throw new Error('tapSel 未找到元素: ' + sel);
}
const rows = (d) => (Array.isArray(d) ? d : (d && d.results) || []);

// ---- 后端断言通道（管理员 token）----
let adminTok = '', cid = 0;
async function api(method, urlPath, body, tok) {
  const res = await fetch(API + urlPath, {
    method: method.toUpperCase(),
    headers: {
      'Content-Type': 'application/json',
      ...(tok || adminTok ? { Authorization: 'Bearer ' + (tok || adminTok) } : {}),
      ...(cid ? { 'X-Company-Id': String(cid) } : {})
    },
    body: body ? JSON.stringify(body) : undefined
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
  return { status: res.status, data };
}

// ---- python 侧通道：SMSCode 读取/清理（验证码桩落库不回响应）----
function pyExec(pyOneLine) {
  return execSync('.venv\\Scripts\\python.exe manage.py shell -c "' + pyOneLine + '"', {
    cwd: BE_DIR, env: { ...process.env, PYTHONIOENCODING: 'utf-8' }, encoding: 'utf-8'
  });
}
async function readSmsCode(phone) {
  for (let i = 0; i < 5; i++) {
    try {
      const out = pyExec(
        "from apps.accounts.models import SMSCode; c=SMSCode.objects.filter(phone='" + phone +
        "', used_at__isnull=True).order_by('-id').first(); print(c.code if c else '-')");
      const m = out.trim().split('\n').pop().trim().match(/^(\d{6})$/);
      if (m) return m[1];
    } catch (e) { /* shell 启动偶发慢，重试 */ }
    await sleep(1200);
  }
  throw new Error('未能从库中读到验证码');
}
const smsCleanup = () => pyExec(
  "from apps.accounts.models import SMSCode; SMSCode.objects.filter(phone='" + EMP.phone + "').delete(); print('cleaned')"
).includes('cleaned');

(async () => {
  const mp = await automator.connect({ wsEndpoint: 'ws://localhost:9420' });
  console.log('connected');
  try {
    // ===== T0 前置 =====
    const lg = await api('POST', '/auth/login', ADMIN);
    check('T0a API 管理员登录', lg.status === 200 && lg.data && lg.data.access, `status=${lg.status}`);
    adminTok = lg.data.access;
    const mine = await api('GET', '/companies/mine');
    const co = rows(mine.data).find((c) => String(c.company_no) === '100001');
    cid = co.id;
    check('T0b API 取公司100001上下文', !!co, JSON.stringify(mine.data).slice(0, 100));
    check('T0c 清理验证码', smsCleanup());
    const recs0 = await api('GET', `/records?user_id=${EMP.uid}&page_size=200`);
    const maxRecId = Math.max(0, ...rows(recs0.data).map((r) => r.id));

    // ===== T1 员工短信登录（UI 全流程） =====
    await mp.evaluate(() => { wx.clearStorage(); });
    let page = await mp.reLaunch('/pages/login/index');
    await page.waitFor(1500);
    page = await mp.currentPage();
    await tapSel(page, '.agree .checkbox');
    const phoneInput = (await page.$$('input'))[0];
    await phoneInput.input(EMP.phone);
    await tapSel(page, '.sms-btn');
    const code = await readSmsCode(EMP.phone);
    const inputs = await page.$$('input');
    await inputs[1].input(code);
    await tapSel(page, '.btn.btn-primary');
    await sleep(3000);
    page = await mp.currentPage();
    if ((await page.data()).stage === 'pick') {
      const items = await page.$$('.co-item');
      for (const it of items) if ((await it.text()).indexOf('100001') >= 0) { await it.tap(); break; }
      await sleep(3000);
      page = await mp.currentPage();
    }
    check('T1a UI 短信登录→首页', page.path === 'pages/index/index', page.path);
    const su = (await mp.evaluate(() => wx.getStorageSync('yisao_user'))) || {};
    const empIsAdmin = await mp.evaluate(() => !!wx.getStorageSync('yisao_is_admin'));
    check('T1b 登录态为李翠花(非管理员)', su && su.id === EMP.uid && !empIsAdmin,
      `user=${JSON.stringify(su).slice(0, 80)} isAdmin=${empIsAdmin}`);

    // ===== T2 员工首页差异（宫格 + 统计卡） =====
    const hd = await waitData(page, (d) => (d.modules || []).length > 0 && (d.statCards || []).length > 0,
      10000, '员工首页数据');
    const names = (hd.modules || []).map((m) => m.name);
    check('T2a 员工宫格=2项(我的薪酬/工作中)',
      names.length === 2 && names.includes('我的薪酬') && names.includes('工作中'), JSON.stringify(names));
    check('T2b 员工宫格无管理员模块', !names.some((n) => ADMIN_MODULE_NAMES.includes(n)), JSON.stringify(names));
    const cardTitles = (hd.statCards || []).map((c) => c.title);
    check('T2c 员工统计卡=今日登记/收入(+本月工资)',
      cardTitles[0] === '今日登记' && cardTitles.includes('今日收入') &&
      !cardTitles.some((t) => ['完成件数', '裁货数', '已结算', '未结算'].includes(t)), JSON.stringify(cardTitles));
    const grid = await page.$('module-grid');
    const cells = (grid ? await grid.$$('.cell') : []) || [];
    check('T2d 宫格DOM渲染2项', cells.length === 2, 'n=' + cells.length);
    await shot(mp, path.join(SHOTS, 'staff_01_home.png'));

    // ===== T3 员工扫菲动线 =====
    const procs = rows((await api('GET', '/processes?page_size=100')).data);
    const tks = rows((await api('GET', '/tickets?page_size=200')).data);
    let pick = null;
    for (const t of tks) {
      const pd = await api('GET', '/productions/' + t.production);
      if (!pd.data || pd.data.is_locked || pd.data.is_cut_done) continue;
      const used = rows((await api('GET', `/records?ticket_id=${t.id}&user_id=${EMP.uid}&page_size=100`)).data)
        .map((r) => r.process);
      const free = procs.find((p) => !used.includes(p.id));
      if (free) { pick = { ticket: t, proc: free }; break; }
    }
    check('T3-0 找到可用菲票+空闲工序', !!pick,
      pick ? `${pick.ticket.bed_no}/${pick.ticket.bundle_no} proc=${pick.proc.name}` : '全部占用');

    await mp.mockWxMethod('scanCode', { result: pick.ticket.qr_code });
    page = await mp.switchTab('/pages/scan/index');
    const d1 = await waitData(page, (d) => d.state === 'process' || d.state === 'qty', 10000, '解析菲票');
    check('T3a 员工扫码解析菲票', !!d1.ticket, JSON.stringify(d1).slice(0, 100));
    check('T3b 扎号/床次与菲票一致',
      d1.ticket && d1.ticket.bundleNo === pick.ticket.bundle_no && String(d1.ticket.bedNo) === String(pick.ticket.bed_no),
      `ui=${d1.ticket && d1.ticket.bundleNo}/${d1.ticket && d1.ticket.bedNo} api=${pick.ticket.bundle_no}/${pick.ticket.bed_no}`);
    const used2 = rows((await api('GET', `/records?ticket_id=${pick.ticket.id}&user_id=${EMP.uid}&page_size=100`)).data)
      .map((r) => r.process);
    const idx = (d1.processes || []).findIndex((p) => !used2.includes(p.id));
    check('T3c 员工工序列表含空闲项', idx >= 0, 'n=' + (d1.processes || []).length);
    const chips = await page.$$('.proc-chip');
    await chips[idx].tap();
    const d2 = await waitData(page, (d) => d.state === 'qty', 6000, '选工序');
    check('T3d 进入件数确认', d2.state === 'qty' && d2.selectedName !== '', `proc=${d2.selectedName}`);
    await shot(mp, path.join(SHOTS, 'staff_02_qty.png'));
    // 提交后立刻把 mock 换成「取消失败」：900ms 自动连续扫码吃 fail → 自动回首页
    await (await page.$('.submit-btn')).tap();
    await mp.mockWxMethod('scanCode', function () { return { errMsg: 'scanCode:fail cancel' }; });
    await waitData(page, (d) => d.state === 'idle', 8000, '登记完成回idle');
    check('T3e 提交后回扫码态', (await page.data()).state === 'idle');
    await shot(mp, path.join(SHOTS, 'staff_03_registered.png'));
    await sleep(2500); // 等 fail-cancel 分支 switchTab 回首页
    const recsNew = rows((await api('GET', `/records?user_id=${EMP.uid}&page_size=200`)).data);
    const newRec = recsNew.find((r) => r.id > maxRecId);
    check('T3f 员工登记落库(本人名下)', !!newRec && newRec.qty > 0, JSON.stringify(newRec || {}).slice(0, 120));

    // ===== T4 工作中页（员工宫格第 2 项） =====
    await mp.restoreWxMethod('scanCode');
    await mp.reLaunch('/pages/index/index');
    await sleep(800);
    await mp.navigateTo('/packages/admin/in-progress/index');
    await sleep(1200);
    page = await mp.currentPage();
    const wd = await waitData(page, (d) => d.loading === false, 8000, '工作中页加载');
    check('T4a 工作中页打开无错误', page.path.indexOf('in-progress') >= 0 && wd.loadError === false,
      `${page.path} err=${wd.loadError}`);
    check('T4b 床次进度列表渲染', Array.isArray(wd.beds), 'beds=' + (wd.beds || []).length);
    const myRec = (wd.records || []).find((r) => r.id === (newRec && newRec.id));
    check('T4c 本人今日登记可见且可撤销', !!myRec && myRec.canRevoke === true, JSON.stringify(myRec || {}).slice(0, 140));
    if (myRec) {
      await mp.mockWxMethod('showModal', { confirm: true });
      await page.callMethod('onRevoke', { currentTarget: { dataset: { id: newRec.id } } });
      await mp.restoreWxMethod('showModal');
      await waitData(page, (d) => !(d.records || []).some((r) => r.id === newRec.id), 8000, '撤销后刷新');
      const gone = await api('GET', `/records?user_id=${EMP.uid}&page_size=200`);
      check('T4d UI撤销本人登记→API已删', !rows(gone.data).some((r) => r.id === newRec.id), `id=${newRec.id}`);
    }
    await shot(mp, path.join(SHOTS, 'staff_04_inprogress.png'));

    // ===== T5 通知页（员工身份） =====
    page = await mp.switchTab('/pages/notifications/index');
    await sleep(1500);
    const nd = await waitData(page, (d) => d.loading === false, 8000, '通知加载');
    check('T5a 员工通知页打开无错误', page.path === 'pages/notifications/index' && nd.error === false,
      `${page.path} err=${nd.error}`);
    check('T5b 通知列表渲染', Array.isArray(nd.items), 'n=' + (nd.items || []).length);

    // ===== T6 我的页（员工身份） =====
    page = await mp.switchTab('/pages/profile/index');
    await sleep(1200);
    const pd = await page.data();
    check('T6a 员工无管理员徽标', pd.isAdmin === false, 'isAdmin=' + pd.isAdmin);
    check('T6b 菜单渲染', (pd.menu || []).length > 0, 'n=' + (pd.menu || []).length);
    check('T6c 登录人显示李翠花', pd.userName === EMP.name, 'name=' + pd.userName);
    const foot = await page.$('.pf-foot');
    check('T6d 品牌脚注为「衣计」', !!foot && (await foot.text()).indexOf('衣计') >= 0,
      foot ? await foot.text() : '未找到脚注');
    await shot(mp, path.join(SHOTS, 'staff_05_profile.png'));

    // ===== T6e/f 变更透明（切片 §22.3）：员工专属菜单 + 「与我相关的变更」页 =====
    check('T6e 员工菜单含「与我相关的变更」', (pd.menu || []).some((m) => m.key === 'changes'),
      JSON.stringify((pd.menu || []).map((m) => m.key)));
    await page.callMethod('onAction', { currentTarget: { dataset: { key: 'changes' } } });
    await sleep(1500);
    page = await mp.currentPage();
    const cd = await waitData(page, (d) => d.loading === false, 8000, '变更页加载');
    check('T6f 变更页打开无错误', page.path === 'packages/salary/changes/index' && cd.error === false,
      `${page.path} err=${cd.error}`);
    await shot(mp, path.join(SHOTS, 'staff_06_changes.png'));
    await mp.navigateBack();
    await sleep(800);
    page = await mp.currentPage();

    // ===== T7 退出登录 =====
    await mp.mockWxMethod('showModal', { confirm: true });
    await page.callMethod('onLogout');
    await mp.restoreWxMethod('showModal');
    await sleep(2000);
    const cur = await mp.currentPage();
    check('T7a 退出回登录页', cur.path === 'pages/login/index', cur.path);

    // ===== 清理 =====
    check('T8 清理验证码', smsCleanup());
  } catch (e) {
    console.log('RUN ERROR:', e && e.stack || e);
    results.push({ name: 'RUN-COMPLETED', ok: false });
  } finally {
    for (const m of ['scanCode', 'showModal']) {
      try { await mp.restoreWxMethod(m); } catch (e) { /* ignore */ }
    }
    const np = results.filter((r) => r.ok).length;
    console.log(`\n===== UI_STAFF RESULT: ${np} passed, ${results.length - np} failed =====`);
    if (results.some((r) => !r.ok)) console.log('FAILED:', ...results.filter((r) => !r.ok).map((r) => r.name), '\n');
    process.exit(0);
  }
})();
