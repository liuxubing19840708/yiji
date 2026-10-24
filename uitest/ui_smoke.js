// -*- coding: utf-8 -*-
/**
 * UI 冒烟测试（miniprogram-automator 驱动微信开发者工具）
 * 用法: node ui_smoke.js
 * 前置: 开发者工具已登录 + 服务端口已开启；后端 runserver 8000 在跑
 * 产物: D:/project/扫菲/uitest/shots/*.png 截图 + 控制台 PASS/FAIL 清单
 */
const path = require('path');
const fs = require('fs');
const automator = require('miniprogram-automator');

const CLI = 'D:/Program Files/微信web开发者工具/cli.bat';
const PROJECT = 'D:/project/扫菲/frontend';
const SHOTS = path.join(__dirname, 'shots');
if (!fs.existsSync(SHOTS)) fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond });
  console.log((cond ? 'PASS' : 'FAIL'), name, cond ? '' : '| ' + String(detail).slice(0, 120));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 截图容错化：本套件截图密集，开发者工具偶发 UNKNOWN（还会拖垮桥接），重试一次并容忍失败 */
async function shot(mp, name) {
  for (let i = 0; i < 2; i++) {
    try {
      await mp.screenshot({ path: path.join(SHOTS, name) });
      return;
    } catch (e) {
      await sleep(800);
    }
  }
  console.log('[warn] screenshot 失败(已跳过):', name);
}

(async () => {
  console.log('connecting to ws://localhost:9420 ...');
  // Node18+ 无法 spawn .bat，故由外部先 `cli.bat auto --auto-port 9420` 再 connect
  const mp = await automator.connect({ wsEndpoint: 'ws://localhost:9420' });
  console.log('connected');

  try {
    // S1 登录页
    let page = await mp.reLaunch('/pages/login/index');
    await page.waitFor(1500);
    page = await mp.currentPage();
    const alreadyIn = page && page.path === 'pages/index/index';
    check('UI1 登录页打开', alreadyIn || page.path === 'pages/login/index', page && page.path);
    await shot(mp, '01_login.png');

    if (!alreadyIn) {
    // （未登录态才走填表流程；已登录则直接落首页）

    // 切到「密码登录」tab（默认是短信 tab）
    const tabs2 = await page.$$('.tab');
    let switched = false;
    for (const el of tabs2) {
      if ((await el.text()).indexOf('密码') >= 0) { await el.tap(); switched = true; break; }
    }
    check('UI1b 切到密码登录', switched);
    await sleep(500);

    // inputs[0]=手机号 inputs[1]=密码
    const inputs = await page.$$('input');
    check('UI1c 登录表单两个输入框', inputs.length >= 2, 'n=' + inputs.length);
    if (inputs.length >= 1) await inputs[0].input('13800000001');
    if (inputs.length >= 2) await inputs[1].input('yisao1234');

    // 勾选隐私协议（不勾会抖动拒绝）
    const agree = await page.$('.agree');
    if (agree) await agree.tap();
    await shot(mp, '02_login_filled.png');

    // 点登录按钮（含「登录」文字的 button，跳过「微信一键登录」）
    let tapped = false;
    const btns = await page.$$('button');
    for (const el of btns) {
      const t = await el.text();
      if (t && t.trim() === '登录') { await el.tap(); tapped = true; break; }
    }
    check('UI2 点到登录按钮', tapped);
    await sleep(3000); // 等登录请求
    page = await mp.currentPage();

    // 多公司用户：登录成功先停在「选择公司」阶段 → 点第一家公司进首页
    let stage = (await page.data()).stage;
    if (stage === 'company' || stage === 'pick') { // 实际阶段名是 pick（登录页多公司选择）
      const coItems = await page.$$('.co-item');
      check('UI3a 登录成功出公司选择', coItems.length >= 1, 'n=' + coItems.length);
      await shot(mp, '03_company_pick.png');
      if (coItems.length) await coItems[0].tap(); // 辉腾制衣(演示)=公司2 管理员
      await sleep(3000);
      page = await mp.currentPage();
    }
    check('UI3 登录后到首页', page && page.path === 'pages/index/index', page && page.path);
    await shot(mp, '03_index_admin.png');
    } // end not-alreadyIn

    // S2 首页管理员宫格（应有管理模块而非仅 员工2宫格）
    const pageData = await page.data();
    const modules = pageData.modules || pageData.visibleModules || [];
    check('UI4 管理员宫格模块数>2', Array.isArray(modules) && modules.length > 2,
      'modules=' + (Array.isArray(modules) ? modules.length : typeof modules));

    // S5 tabBar 五个 tab 逐个切（扫菲页会调 wx.scanCode，开发者工具里会 fail → 验证不崩）
    const tabs = ['pages/process/index', 'pages/scan/index', 'pages/notifications/index', 'pages/profile/index'];
    const tabNames = ['05_process', '06_scan', '07_notifications', '08_profile'];
    for (let i = 0; i < tabs.length; i++) {
      try {
        page = await mp.switchTab('/' + tabs[i]);
        await page.waitFor(2000);
        page = await mp.currentPage();
        check('UI5-' + i + ' tab ' + tabs[i], page && page.path === tabs[i].replace('pages/', 'pages/'), page && page.path);
      } catch (e) {
        check('UI5-' + i + ' tab ' + tabs[i], false, e.message);
      }
      await shot(mp, tabNames[i] + '.png');
    }

    // S6 扫菲页在工具里（相机 fail 路径）：停留 idle 态、有「扫 码」按钮
    page = await mp.switchTab('/pages/scan/index');
    await page.waitFor(1500);
    page = await mp.currentPage();
    const scanData = await page.data();
    check('UI6 扫菲页停留idle不崩', page && page.path === 'pages/scan/index' && scanData.state === 'idle',
      'state=' + scanData.state);

    // S7 管理分包页面直达（员工管理）
    try {
      page = await mp.navigateTo('/packages/admin/employees/index');
      await page.waitFor(2000);
      page = await mp.currentPage();
      check('UI7 员工管理页打开', page && page.path.indexOf('employees') >= 0, page && page.path);
      await shot(mp, '09_employees.png');
      await mp.navigateBack();
    } catch (e) {
      check('UI7 员工管理页打开', false, e.message);
    }

    // S8 薪酬分包（公司汇总，管理员）
    try {
      page = await mp.navigateTo('/packages/salary/company/index');
      await page.waitFor(2000);
      page = await mp.currentPage();
      check('UI8 公司薪酬页打开', page && page.path.indexOf('salary') >= 0, page && page.path);
      await shot(mp, '10_salary_company.png');
      await mp.navigateBack();
    } catch (e) {
      check('UI8 公司薪酬页打开', false, e.message);
    }

    const fails = results.filter((r) => !r.ok);
    console.log('\n===== UI SMOKE: %d passed, %d failed =====', results.length - fails.length, fails.length);
    fails.forEach((f) => console.log('  -', f.name));
  } finally {
    try { await mp.close(); } catch (e) { /* ignore */ }
  }
})().catch((e) => {
  console.error('LAUNCH/RUN ERROR:', e.message || e);
  process.exit(2);
});
