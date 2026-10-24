// -*- coding: utf-8 -*-
/**
 * UI + 后端 融合测试：员工结算申请全链（薪酬分包）
 *
 * 覆盖：
 *   P1 员工短信登录       UI 验证码登录 ↔ storage 用户/公司 ↔ 单公司自动进入首页
 *   P2 薪酬页入口         首页宫格「我的薪酬」→ /salary/mine 金额回显
 *   P3 登记→未结联动      API 代员工登记 ↔ /salary/employee 未结增加
 *   P4 发起结算申请       UI 申请面板提交 ↔ API settle-applies 落库(待审核) + UI 徽标
 *   P5 管理员审核发放     UI 审核记录「通过/发放」(mock 弹窗) ↔ API 状态 0→1→2 + 金额迁移
 *   P5f 异常分支          管理员拒绝第二笔申请 ↔ API 状态 3
 *
 * 前置: 后端 8000；开发者工具 cli.bat auto --auto-port 9420。
 * 幂等: preclean 清 SMSCode/历史申请；cleanup 删申请+结算单(SET_NULL 记录自动回未结)+本套件登记。
 */
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const automator = require('miniprogram-automator');

const API = 'http://127.0.0.1:8000/api';
const EMP = { phone: '13800000003', name: '李翠花', uid: 8 };
const ADMIN = { phone: '13800000001', password: 'yisao1234' };
const BE_DIR = 'D:\\project\\扫菲\\backend';

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
let adminTok = '', cid = 0, empTok = '';
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

// ---- python 侧通道：SMSCode 读取 / 清理（验证码桩落库不回响应）----
// 注意：cmd.exe 双引号内多行会被拆行执行，python 代码必须单行分号连接。
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
function payCleanup(extraRecordId) {
  const py =
    "from apps.accounts.models import SMSCode, User; " +
    "from apps.salary.models import SettleApply, Settlement; " +
    "from apps.records.models import Record; " +
    "u=User.objects.get(phone='" + EMP.phone + "'); " +
    "SMSCode.objects.filter(phone='" + EMP.phone + "').delete(); " +
    "SettleApply.all_objects.filter(user=u).delete(); " +
    "Settlement.all_objects.filter(user=u).delete(); " +
    (extraRecordId ? "Record.all_objects.filter(id=" + extraRecordId + ").delete(); " : "") +
    "print('cleaned')";
  return pyExec(py).includes('cleaned');
}
async function unsettledOf(uid) {
  const r = await api('GET', '/salary/employee/' + uid);
  return { status: r.status, unsettled: parseFloat((r.data && r.data.unsettled) || '0'), data: r.data };
}

(async () => {
  const mp = await automator.connect({ wsEndpoint: 'ws://localhost:9420' });
  console.log('connected');
  try {
    // ===== P0 前置 =====
    const lg = await api('POST', '/auth/login', ADMIN);
    check('P0a API 管理员登录', lg.status === 200 && lg.data && lg.data.access, `status=${lg.status}`);
    adminTok = lg.data.access;
    const mine = await api('GET', '/companies/mine');
    const co = rows(mine.data).find((c) => String(c.company_no) === '100001');
    cid = co.id;
    check('P0b API 取公司100001上下文', !!co, JSON.stringify(mine.data).slice(0, 100));
    check('P0c 前置清理(SMSCode/历史申请/结算单)', payCleanup(0));

    const base = await unsettledOf(EMP.uid);
    const recs0 = await api('GET', `/records?user_id=${EMP.uid}&page_size=200`);
    const maxRecId = Math.max(0, ...rows(recs0.data).map((r) => r.id));

    // ===== P1 员工短信登录（UI 全流程，验证码从桩库回读） =====
    await mp.evaluate(() => { wx.clearStorage(); });
    let page = await mp.reLaunch('/pages/login/index');
    await page.waitFor(1500);
    page = await mp.currentPage();
    await tapSel(page, '.agree .checkbox'); // 协议必勾，否则 onSendSms/onSmsLogin 直接 toast 拦截
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
    check('P1a UI 短信登录→首页', page.path === 'pages/index/index', page.path);
    const su = (await mp.evaluate(() => wx.getStorageSync('yisao_user'))) || {};
    const empIsAdmin = await mp.evaluate(() => !!wx.getStorageSync('yisao_is_admin'));
    check('P1b 登录态为李翠花(非管理员)', su && su.id === EMP.uid && !empIsAdmin,
      `user=${JSON.stringify(su).slice(0, 80)} isAdmin=${empIsAdmin}`);

    // ===== P2 首页宫格 → 我的薪酬 =====
    let gridHit = false;
    try {
      const grid = await page.$('module-grid');
      const cells = (grid ? await grid.$$('.cell') : []) || (await page.$$('.cell'));
      for (const c of cells) {
        const n = await c.$('.cell-name');
        if (n && (await n.text()) === '我的薪酬') { await c.tap(); gridHit = true; break; }
      }
    } catch (e) { /* 走兜底直跳 */ }
    if (!gridHit) { await mp.navigateTo('/packages/salary/mine/index'); console.log('note: 宫格未命中，直跳薪酬页'); }
    await sleep(1500);
    page = await mp.currentPage();
    const md = await waitData(page, (d) => d.unsettledText !== undefined && d.unsettledText !== '', 8000, '薪酬页金额');
    check('P2a 薪酬页打开且金额回显', page.path.indexOf('salary/mine') >= 0 && md.unsettledText.length > 0,
      `${page.path} unsettled=${md.unsettledText}`);

    // ===== P3 API 代员工登记 → 未结增加 =====
    const procs = await api('GET', '/processes?page_size=100');
    const procList = rows(procs.data);
    const tks = await api('GET', '/tickets?page_size=100');
    let created = null;
    for (const t of rows(tks.data)) {
      const pd = await api('GET', '/productions/' + t.production);
      if (!pd.data || pd.data.is_locked || pd.data.is_cut_done) continue;
      for (const p of procList) {
        const dup = await api('GET', `/records?ticket_id=${t.id}&process_id=${p.id}&user_id=${EMP.uid}&page_size=5`);
        if (rows(dup.data).length === 0) {
          const cr = await api('POST', '/records', { ticket_id: t.id, process_id: p.id, qty: 3, user_id: EMP.uid });
          if (cr.status === 201 || cr.status === 200) { created = cr.data; break; }
        }
      }
      if (created) break;
    }
    const recs1 = await api('GET', `/records?user_id=${EMP.uid}&page_size=200`);
    const newRec = rows(recs1.data).find((r) => r.id > maxRecId);
    check('P3a UI会话下API代登记落库', !!newRec, JSON.stringify(created).slice(0, 80));
    const after = await unsettledOf(EMP.uid);
    check('P3b 未结金额增加', after.status === 200 && after.unsettled > base.unsettled,
      `base=${base.unsettled} after=${after.unsettled}`);
    // 薪酬页回显联动（onShow 未触发，此处以重进页面验证）
    await mp.reLaunch('/pages/index/index');
    await sleep(800);
    await mp.navigateTo('/packages/salary/mine/index');
    await sleep(1200);
    page = await mp.currentPage();
    const md2 = await waitData(page, (d) => d.unsettledText !== undefined && d.unsettledText !== '', 8000, '薪酬页金额2');
    check('P3c 薪酬页未结金额联动', parseFloat(md2.unsettledText.replace(/[^\d.]/g, '')) >= after.unsettled,
      `page=${md2.unsettledText} api=${after.unsettled}`);

    // ===== P4 UI 发起结算申请 =====
    await page.callMethod('onToggleApply');
    await waitData(page, (d) => d.showApply === true, 5000, '申请面板');
    await page.callMethod('onNoteInput', { detail: { value: 'UI支付链路测试' } });
    await page.callMethod('onSubmitApply');
    await waitData(page, (d) => d.showApply === false, 6000, '申请提交完成');
    const aps = await api('GET', `/salary/settle-applies?user_id=${EMP.uid}&status=0`);
    const apply1 = rows(aps.data).find((a) => (a.note || '').indexOf('UI支付链路') >= 0) || rows(aps.data)[0];
    check('P4a 申请落库待审核', !!apply1 && apply1.status === 0, JSON.stringify(aps.data).slice(0, 100));
    await page.callMethod('onToggleRecords');
    const rd = await waitData(page, (d) => (d.records || []).length > 0, 6000, '结算记录');
    const myCard = (rd.records || []).find((r) => r.id === (apply1 && apply1.id));
    check('P4b 记录页徽标待审核', !!myCard && (myCard.statusDisplay || '').indexOf('待审核') >= 0,
      JSON.stringify(myCard || {}).slice(0, 100));

    // P4c 员工 API 第二笔申请（供拒绝分支，未发放前申请不受未结清零影响）
    const sc = pyExec(
      "from apps.accounts.models import SMSCode; from django.utils import timezone; " +
      "from datetime import timedelta; " +
      "SMSCode.objects.create(phone='" + EMP.phone + "', code='246810', expires_at=timezone.now()+timedelta(minutes=10)); " +
      "print('ok')");
    check('P4c-0 植入验证码', sc.includes('ok'), sc.slice(-80));
    const el = await api('POST', '/auth/login-sms', { phone: EMP.phone, code: '246810' }, '');
    empTok = el.data && el.data.access;
    const now = new Date();
    const monthStart = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
    const today = now.toLocaleDateString('sv-SE'); // 本地日期（toISOString 是 UTC，跨午夜会差一天）
    const ap2 = await api('POST', '/salary/settle-applies',
      { period_start: monthStart, period_end: today }, empTok);
    const apply2 = ap2.data && ap2.data.id ? ap2.data : rows(ap2.data)[0];
    check('P4c 员工API第二笔申请', ap2.status === 201 && !!apply2, `status=${ap2.status} ${JSON.stringify(ap2.data).slice(0, 80)}`);

    // ===== P5 管理员审核/发放 =====
    await mp.evaluate(() => { wx.clearStorage(); });
    page = await mp.reLaunch('/pages/login/index');
    await page.waitFor(1500);
    page = await mp.currentPage();
    const tabs = await page.$$('.tab');
    for (const tb of tabs) if ((await tb.text()).indexOf('密码') >= 0) { await tb.tap(); break; }
    await sleep(400);
    const aInputs = await page.$$('input');
    await aInputs[0].input(ADMIN.phone);
    await aInputs[1].input(ADMIN.password);
    await tapSel(page, '.agree .checkbox');
    await tapSel(page, '.btn.btn-primary');
    await sleep(3000);
    page = await mp.currentPage();
    if ((await page.data()).stage === 'pick') {
      const items = await page.$$('.co-item');
      for (const it of items) if ((await it.text()).indexOf('100001') >= 0) { await it.tap(); break; }
      await sleep(3000);
      page = await mp.currentPage();
    }
    check('P5a 管理员登录', page.path === 'pages/index/index', page.path);
    await mp.navigateTo('/packages/salary/company/index');
    await sleep(1500);
    page = await mp.currentPage();
    await waitData(page, (d) => (d.empList || d.employees || []).length > 0 || d.loaded === true, 8000, '薪酬管理员工列表').catch(() => {});
    await page.callMethod('onGoApplies');
    const ad = await waitData(page, (d) => (d.applies || []).length >= 2, 8000, '申请记录列表');
    const card1 = (ad.applies || []).find((a) => a.id === apply1.id);
    check('P5b 申请记录可见两笔待审核', !!card1 && card1.status === 0 &&
      (ad.applies || []).filter((a) => a.status === 0).length >= 2, JSON.stringify(ad.applies || []).slice(0, 120));

    await page.callMethod('onApproveApply', { currentTarget: { dataset: { id: apply1.id } } });
    const ap1After = await api('GET', '/salary/settle-applies?user_id=' + EMP.uid);
    const st1 = rows(ap1After.data).find((a) => a.id === apply1.id);
    check('P5c 审核通过→API状态1', st1 && st1.status === 1, JSON.stringify(st1 || {}).slice(0, 80));

    await mp.mockWxMethod('showModal', { confirm: true });
    await page.callMethod('onPayApply', { currentTarget: { dataset: { id: apply1.id } } });
    await mp.restoreWxMethod('showModal');
    await sleep(1200);
    const ap1Paid = await api('GET', '/salary/settle-applies?user_id=' + EMP.uid);
    const st2 = rows(ap1Paid.data).find((a) => a.id === apply1.id);
    check('P5d 发放→API状态2', st2 && st2.status === 2, JSON.stringify(st2 || {}).slice(0, 80));

    const fin = await unsettledOf(EMP.uid);
    const recs2 = await api('GET', `/records?user_id=${EMP.uid}&page_size=200`);
    const settledNow = rows(recs2.data).filter((r) => r.settlement).length;
    check('P5e 发放后登记入结算单+未结清零', fin.unsettled === 0 && settledNow >= 5,
      `unsettled=${fin.unsettled} base=${base.unsettled} settledRecs=${settledNow}`);

    // P5f 异常分支：拒绝第二笔
    await mp.mockWxMethod('showModal', { confirm: true, content: '自动化拒绝' });
    await page.callMethod('onRejectApply', { currentTarget: { dataset: { id: apply2.id } } });
    await mp.restoreWxMethod('showModal');
    await sleep(1000);
    const ap2After = await api('GET', '/salary/settle-applies?user_id=' + EMP.uid);
    const st3 = rows(ap2After.data).find((a) => a.id === apply2.id);
    check('P5f 拒绝→API状态3', st3 && st3.status === 3, JSON.stringify(st3 || {}).slice(0, 80));

    // ===== P6 清理复位 =====
    check('P6 清理(申请/结算单/验证码删除,记录回未结)', payCleanup(newRec && newRec.id));
    const fin2 = await unsettledOf(EMP.uid);
    const apCnt = await api('GET', '/salary/settle-applies?user_id=' + EMP.uid);
    check('P6b 复位:未结=基线且申请清空', fin2.unsettled === base.unsettled && rows(apCnt.data).length === 0,
      `unsettled=${fin2.unsettled} applies=${rows(apCnt.data).length}`);
  } catch (e) {
    console.log('RUN ERROR:', e && e.stack || e);
    results.push({ name: 'RUN-COMPLETED', ok: false });
  } finally {
    const np = results.filter((r) => r.ok).length;
    console.log(`\n===== UI_PAY RESULT: ${np} passed, ${results.length - np} failed =====`);
    if (results.some((r) => !r.ok)) console.log('FAILED:', ...results.filter((r) => !r.ok).map((r) => r.name), '\n');
    process.exit(0);
  }
})();
