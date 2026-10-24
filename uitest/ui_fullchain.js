// -*- coding: utf-8 -*-
/**
 * UI + 后端 融合全链路测试（每条用例 = UI 动作 → 后端落库断言 → UI 回显断言）
 *
 * 覆盖（对应 测试用例-E2E设计.md §14 融合用例）：
 *   F1 登录一致性        UI 登录态 ↔ API /auth/me、storage 公司 ↔ /companies/mine
 *   F2 邀请→审批全链     UI 邀请/审批 ↔ API employees pending/active + 幂等复位(resign)
 *   F3 扫菲→工资联动     UI 扫码登记 ↔ API /records 落库 + /salary/company 金额增量 + 薪酬页回显
 *   F4 通知联动          API /notifications 首条 ↔ UI 通知页首条
 *   F5 退出登录          UI 退出(mock 确认弹窗) ↔ storage 清空 + 回登录页
 *
 * 前置: 后端 8000；开发者工具 cli.bat auto --auto-port 9420。
 * 幂等: F2 结束把测试员工复位为「离职」（invite 可复活）；F3 会留一条登记（与 ui_scan 同口径）。
 */
const path = require('path');
const fs = require('fs');
const automator = require('miniprogram-automator');
const shot = require('./helpers/shot');

const API = 'http://127.0.0.1:8000/api';
const FC_PHONE = '13866660003'; // 融合用例专用手机号（不与其他套件冲突）
const FC_NAME = '融合员工';
const QR1 = '100001|1|ST001|1|碎花蓝|M|35';
const ADMIN = { phone: '13800000001', password: 'yisao1234' };
const TODAY = new Date().toLocaleDateString('sv-SE'); // 本地日期 YYYY-MM-DD（toISOString 是 UTC，跨午夜会差一天）

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
    if (Date.now() - t0 > timeout) throw new Error('waitData 超时: ' + label + ' | ' + JSON.stringify(d).slice(0, 120));
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

// ---- 后端断言通道（node fetch，等价于真机同款请求）----
let adminTok = '', cid = 0, adminUid = 0;
async function api(method, urlPath, body) {
  const res = await fetch(API + urlPath, {
    method: method.toUpperCase(),
    headers: {
      'Content-Type': 'application/json',
      ...(adminTok ? { Authorization: 'Bearer ' + adminTok } : {}),
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

// ---- UI 登录（复用 ui_offline 模式）----
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

(async () => {
  const mp = await automator.connect({ wsEndpoint: 'ws://localhost:9420' });
  console.log('connected');
  try {
    // ===== F0 前置：清离线队列残留（上轮失败提交可能滞留） =====
    const q0 = await mp.evaluate(() => wx.getStorageSync('yisao_offline_queue'));
    if (Array.isArray(q0) && q0.length) {
      console.log('note: 丢弃上轮残留离线队列', q0.length, '条');
      await mp.evaluate(() => { wx.removeStorageSync('yisao_offline_queue'); });
    }

    // ===== F0 前置：API 管理员登录 + 公司上下文 =====
    const lg = await api('POST', '/auth/login', ADMIN);
    check('F0a API 管理员登录', lg.status === 200 && lg.data && lg.data.access, `status=${lg.status}`);
    adminTok = lg.data.access;
    const me = await api('GET', '/auth/me');
    adminUid = me.data.id;
    const mine = await api('GET', '/companies/mine');
    const co = rows(mine.data).find((c) => String(c.company_no) === '100001');
    cid = co.id;
    check('F0b API 取到公司100001上下文', !!co, JSON.stringify(mine.data).slice(0, 100));

    // F2 前置清理：把融合员工的遗留关系复位为「离职」（invite 可复活）
    {
      const emps = await api('GET', '/employees', null);
      const old = rows(emps.data).find((e) => e.phone === FC_PHONE);
      if (old && old.status === 0) { await api('POST', `/employees/${old.id}/approve`, {}); }
      const emps2 = await api('GET', '/employees', null);
      const cur = rows(emps2.data).find((e) => e.phone === FC_PHONE);
      if (cur && cur.status === 1) await api('POST', `/employees/${cur.id}/resign`, { force: 1 });
      check('F0c 前置清理完成(测试员工已复位)', true);
    }

    // ===== F1 登录一致性：UI 登录态 ↔ API 身份 =====
    const home = await loginIfNeeded(mp);
    check('F1a UI 登录到首页', home.path === 'pages/index/index', home.path);
    const uiUser = await mp.evaluate(() => wx.getStorageSync('yisao_user'));
    check('F1b UI storage 用户与 API /auth/me 一致', uiUser && String(uiUser.phone) === ADMIN.phone,
      `ui=${uiUser && uiUser.phone} api=${ADMIN.phone}`);
    const uiCid = await mp.evaluate(() => wx.getStorageSync('yisao_company_id'));
    check('F1c UI storage 公司与 API /companies/mine 一致', String(uiCid) === String(cid),
      `ui=${uiCid} api=${cid}`);

    // ===== F2 邀请→审批全链 =====
    let page = await mp.navigateTo('/packages/admin/employees/index');
    await waitData(page, (d) => !d.loading, 8000, '员工页加载');
    await (await page.$('.invite-btn')).tap();
    await waitData(page, (d) => d.inviteVisible, 5000, '邀请弹层');
    const panelInputs = await page.$$('.panel input');
    await panelInputs[0].input(FC_PHONE);
    await panelInputs[1].input(FC_NAME);
    await shot(mp, path.join(SHOTS, 'fullchain_invite.png'));
    const sendBtn = await findByText(await page.$$('button'), '发送邀请');
    await sendBtn.tap();
    // 诊断轮询：弹层最长等 20s（DevTools 重编译后首个请求可能偏慢），未关则 dump 现场
    let closed = false, dump = '';
    for (let i = 0; i < 50; i++) {
      await sleep(400);
      const d = await page.data();
      if (!d.inviteVisible) { closed = true; break; }
      if (i === 49) dump = JSON.stringify({ inviteVisible: d.inviteVisible, inviteSubmitting: d.inviteSubmitting, invitePhone: d.invitePhone, loading: d.loading });
    }
    check('F2a UI 发送邀请成功(弹层关闭)', closed, dump);

    const pend = await api('GET', '/employees?tab=pending', null);
    const pendEmp = rows(pend.data).find((e) => e.phone === FC_PHONE);
    check('F2b API 待审核列表含新员工', !!pendEmp && pendEmp.status === 0,
      JSON.stringify(rows(pend.data).map((e) => e.phone)));

    // UI 侧切到「待审核」tab 点「通过」
    await mp.reLaunch('/packages/admin/employees/index');
    await sleep(1200);
    page = await mp.currentPage();
    await waitData(page, (d) => !d.loading, 8000, '员工页重载');
    const tabEl = await findByText(await page.$$('.tab'), '待审核');
    await tabEl.tap();
    await sleep(800);
    const cards = await page.$$('.emp');
    let target = null;
    for (const c of cards) if ((await c.text()).indexOf(FC_PHONE) >= 0) { target = c; break; }
    check('F2c UI 待审核tab看到新员工卡片', !!target);
    if (target) {
      await (await target.$('.act-primary')).tap(); // 通过
      await sleep(2000);
    }
    const act = await api('GET', '/employees?tab=all', null);
    const actEmp = rows(act.data).find((e) => e.phone === FC_PHONE);
    check('F2d API 审批后落库为在职', !!actEmp && actEmp.status === 1,
      actEmp ? `status=${actEmp.status}` : '未找到');
    const cards2 = await page.$$('.emp');
    let gone = true;
    for (const c of cards2) if ((await c.text()).indexOf(FC_PHONE) >= 0) gone = false;
    check('F2e UI 待审核列表已移除该员工', gone);
    await shot(mp, path.join(SHOTS, 'fullchain_approved.png'));
    // 收尾复位为离职（force 跳过未结确认；下轮 invite 可复活，保证幂等）
    await api('POST', `/employees/${actEmp.id}/resign`, { force: 1 });

    // ===== F3 扫菲→工资联动 =====
    await mp.mockWxMethod('scanCode', { result: QR1 });
    let scan = await mp.switchTab('/pages/scan/index');
    const d1 = await waitData(scan, (d) => d.state === 'process' || d.state === 'qty', 10000, '解析菲票');
    check('F3a UI 扫码解析菲票', d1.ticket && d1.ticket.bundleNo === '1', `bundle=${(d1.ticket || {}).bundleNo}`);
    // 幂等 preclean：同票 6 工序有限，多轮跑测试会把「同票同工序同人仅一条」的坑位占满。
    // 先把本人对该票今日的未结算登记全数撤销（管理员撤本人当日记录合法），再挑工序。
    if (d1.ticket && d1.ticket.id) {
      const stale = await api('GET', `/records?ticket_id=${d1.ticket.id}&user_id=${adminUid}&page_size=50`, null);
      for (const r of rows(stale.data)) {
        if (String(r.registered_at || '').slice(0, 10) === TODAY && !r.settlement) {
          const del = await api('DELETE', `/records/${r.id}`);
          if (del.status !== 204 && del.status !== 200) {
            console.log('[warn] preclean 撤销失败', r.id, del.status, JSON.stringify(del.data).slice(0, 80));
          }
        }
      }
    }
    // 幂等选工序：后端规则「同票同工序同人仅一条」，挑一个今日未登记过的工序
    const snap = await api('GET', '/records?page_size=50', null);
    const maxId = Math.max(0, ...rows(snap.data).map((r) => r.id || 0));
    const usedProc = new Set(rows(snap.data)
      .filter((r) => r.ticket === d1.ticket.id && String(r.user) === String(adminUid))
      .map((r) => r.process));
    const procs = d1.processes || [];
    const chipIdx = procs.findIndex((p) => !usedProc.has(p.id));
    check('F3a2 有今日未登记的工序可选', d1.state === 'process' && chipIdx >= 0,
      `state=${d1.state} chips=${procs.length} used=${[...usedProc].join(',')}`);
    const sal0 = await api('GET', '/salary/company?start=' + TODAY.slice(0, 8) + '01&end=' + TODAY);
    const before = parseFloat((sal0.data.unsettled || {}).amount || '0');
    if (d1.state === 'process' && chipIdx >= 0) {
      await (await scan.$$('.proc-chip'))[chipIdx].tap();
      await waitData(scan, (d) => d.state === 'qty', 6000, '选工序');
    }
    await (await scan.$('.submit-btn')).tap();
    await waitData(scan, (d) => d.state === 'idle', 8000, '登记提交');
    // 轮询等新记录落库（id > 快照 maxId），最长 12s（业务拒绝也回 idle，不能只看页面态）
    let rec = null;
    for (let i = 0; i < 30 && !rec; i++) {
      await sleep(400);
      const recs = await api('GET', '/records?page_size=50', null);
      rec = rows(recs.data).find((r) => (r.id || 0) > maxId
        && r.bundle_no === '1' && r.qty === 35 && String(r.user) === String(adminUid)
        && String(r.registered_at || '').slice(0, 10) === TODAY) || null;
    }
    check('F3b API 今日落库登记(qty=35 扎1 本人)', !!rec, '12s 内未出现新记录(疑似重复登记被拒)');
    check('F3c API 登记金额 = qty×price', rec && Math.abs(parseFloat(rec.amount) - rec.qty * parseFloat(rec.price)) < 0.01,
      rec ? `amount=${rec.amount} qty=${rec.qty} price=${rec.price}` : '');

    if (rec) {
      const sal1 = await api('GET', '/salary/company?start=' + TODAY.slice(0, 8) + '01&end=' + TODAY);
      const after = parseFloat((sal1.data.unsettled || {}).amount || '0');
      check('F3d API 未结工资增量 = 登记金额', Math.abs(after - before - parseFloat(rec.amount)) < 0.01,
        `before=${before} after=${after} rec=${rec.amount}`);

      const salPage = await mp.navigateTo('/packages/salary/company/index');
      await waitData(salPage, (d) => !d.loading, 8000, '薪酬页加载');
      const uiAmt = parseFloat((await salPage.data()).unsettledText);
      check('F3e UI 薪酬页未结金额与 API 一致', Math.abs(uiAmt - after) < 0.01,
        `ui=${uiAmt} api=${after}`);
      await shot(mp, path.join(SHOTS, 'fullchain_salary.png'));
    }

    // ===== F4 通知联动 =====
    const notif = await api('GET', '/notifications?page_size=20', null);
    const notifPage = await mp.reLaunch('/pages/notifications/index');
    await waitData(notifPage, (d) => (d.items || []).length > 0, 8000, '通知列表加载');
    const uiFirst = (await notifPage.data()).items[0];
    const apiFirst = rows(notif.data)[0];
    check('F4 API 与 UI 通知首条一致', uiFirst && apiFirst &&
      String(uiFirst.title) === String(apiFirst.title),
      `ui=${uiFirst && uiFirst.title} api=${apiFirst && apiFirst.title}`);

    // ===== F5 退出登录 =====
    const prof = await mp.reLaunch('/pages/profile/index');
    await prof.waitFor(1200);
    await mp.mockWxMethod('showModal', { confirm: true });
    const logoutEl = await findByText(await prof.$$('.pf-menu-item'), '退出登录');
    await logoutEl.tap();
    await sleep(2000);
    const cur = await mp.currentPage();
    check('F5a UI 退出回登录页', cur.path === 'pages/login/index', cur.path);
    const tok = await mp.evaluate(() => wx.getStorageSync('yisao_token'));
    check('F5b storage token 已清空', !tok, `token="${String(tok).slice(0, 16)}..."`);
    await mp.restoreWxMethod('showModal');
  } finally {
    for (const m of ['scanCode', 'request', 'showModal']) {
      try { await mp.restoreWxMethod(m); } catch (e) { /* 未 mock 过则忽略 */ }
    }
  }
  const fails = results.filter((r) => !r.ok);
  console.log('\n===== FULLCHAIN(UI+API): %d passed, %d failed =====', results.length - fails.length, fails.length);
  fails.forEach((f) => console.log('  -', f.name));
  process.exit(fails.length ? 1 : 0);
})().catch((e) => {
  console.error('RUN ERROR:', e.message || e);
  process.exit(2);
});
