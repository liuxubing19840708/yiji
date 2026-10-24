// -*- coding: utf-8 -*-
/**
 * UI token 过期自动登出测试（TC01-16）：
 * 往 storage 塞一个已过期 JWT → 打开首页触发接口 → 401 → 请求层 clearAuth + reLaunch 登录页。
 */
const automator = require('miniprogram-automator');

// 由后端 RefreshToken.set_exp(lifetime=-60s) 生成的过期 token
const EXPIRED = process.argv[2];
const results = [];
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond });
  console.log((cond ? 'PASS' : 'FAIL'), name, cond ? '' : '| ' + String(detail).slice(0, 140));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const mp = await automator.connect({ wsEndpoint: 'ws://localhost:9420' });
  // 1) 塞过期 token
  await mp.evaluate((t) => { wx.setStorageSync('yisao_token', t); }, EXPIRED);
  // 2) reLaunch 首页：index onShow refreshCompanyContext 会带过期 token 请求 → 401
  await mp.reLaunch('/pages/index/index');
  await sleep(4000);
  const cur = await mp.currentPage();
  check('T1 过期token触发自动登出回登录页', cur.path === 'pages/login/index', cur.path);
  // 3) 本地态已清
  const tok = await mp.evaluate(() => wx.getStorageSync('yisao_token'));
  check('T2 失效token已从storage清除', !tok, `token="${String(tok).slice(0, 20)}..."`);
  const fails = results.filter((r) => !r.ok);
  console.log('\n===== TOKEN FLOW: %d passed, %d failed =====', results.length - fails.length, fails.length);
  process.exit(fails.length ? 1 : 0);
})().catch((e) => { console.error('RUN ERROR:', e.message || e); process.exit(2); });
