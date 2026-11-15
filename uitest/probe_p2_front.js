// -*- coding: utf-8 -*-
/**
 * probe_p2_front.js —— P2 报价+应收 前端探针（SF-P2-2，INT 集成门禁，2026-10-24）。
 *
 * 断言目标（只读探针：全部 GET，不建数、不改数）：
 *   1) 首页宫格：P2 两项（报价管理 quotation / 应收管理 receivable）在管理员宫格可见；
 *   2) 报价列表页（/packages/admin/quote-list）：列表拉取收敛（loaded=true、error=false，
 *      空库允许 0 行）；
 *   3) 应收列表页（/packages/admin/receivables）：列表拉取收敛（loaded=true、error=false）；
 *   4) 库存估值页（/packages/admin/stock-valuation）：页面就绪（denied=false，待查询引导态）。
 *
 * 前置：微信开发者工具自动化端口 9420；后端 8000 已启动（--noreload）。
 * 运行：cd D:\project\扫菲\uitest && node probe_p2_front.js
 * 连接失败则打印 cli.bat auto 提示，结果=脚本交付待真机/工具验证（exitCode=3）。
 */
const {
  Suite, openSession, pageData, waitPageData, injectLogin, withTimeout
} = require('./helpers_ui.js');

const suite = new Suite('P2 报价+应收 前端探针');
const GRID_KEYS = ['quotation', 'receivable'];

(async () => {
  let mp = null;
  try {
    mp = await openSession(console.log);
  } catch (e) {
    console.log('CONNECT FAIL:', (e && e.message) || e);
    console.log('运行说明：cli.bat auto --project D:\\project\\扫菲\\frontend --auto-port 9420');
    console.log('再运行 node probe_p2_front.js；本次结果=脚本交付，待真机/工具验证。');
    process.exitCode = 3;
    suite.summary();
    return;
  }

  try {
    await suite.step('P2 注入登录态（管理员+86）', () => injectLogin(mp));

    // ---- D1：首页宫格两项可见（quotation.view / finance.view） ----
    await suite.step('D1 首页宫格 P2 两项可见', async () => {
      const page = await withTimeout(mp.reLaunch('/pages/index/index'), 20000, 'reLaunch index');
      await waitPageData(page, (d) => Array.isArray(d.modules) && d.modules.length > 0,
        { label: '宫格就绪', timeout: 15000 });
      const d = await pageData(page);
      const missing = [];
      for (const key of GRID_KEYS) {
        const m = (d.modules || []).find((x) => x && x.key === key);
        if (!m || !m.visible) missing.push(key + (m ? '(hidden)' : '(absent)'));
      }
      if (missing.length) throw new Error('宫格缺失/隐藏: ' + missing.join(', '));
    }, true);

    // ---- D2：报价列表页加载 ----
    await suite.step('D2 报价列表加载（loaded 且 error=false）', async () => {
      const page = await withTimeout(mp.reLaunch('/packages/admin/quote-list/index'),
        20000, 'reLaunch quote-list');
      await waitPageData(page, (d) => d && d.loaded === true && d.loading === false,
        { label: '报价列表收敛', timeout: 15000 });
      const d = await pageData(page);
      if (d.error) throw new Error('报价列表接口报错 error=true');
      if (d.denied) throw new Error('报价页被权限拦截 denied=true');
      if (!Array.isArray(d.rows)) throw new Error('rows 非数组');
    }, true);

    // ---- D3：应收列表页加载 ----
    await suite.step('D3 应收列表加载（loaded 且 error=false）', async () => {
      const page = await withTimeout(mp.reLaunch('/packages/admin/receivables/index'),
        20000, 'reLaunch receivables');
      await waitPageData(page, (d) => d && d.loaded === true && d.loading === false,
        { label: '应收列表收敛', timeout: 15000 });
      const d = await pageData(page);
      if (d.error) throw new Error('应收列表接口报错 error=true');
      if (d.denied) throw new Error('应收页被权限拦截 denied=true');
      if (!Array.isArray(d.rows)) throw new Error('rows 非数组');
    }, true);

    // ---- D4：库存估值页就绪（finance.manage；引导态 searched=false 不发请求） ----
    await suite.step('D4 库存估值页就绪（denied=false）', async () => {
      const page = await withTimeout(mp.reLaunch('/packages/admin/stock-valuation/index'),
        20000, 'reLaunch stock-valuation');
      await waitPageData(page, (d) => d && d.searched === false,
        { label: '估值页就绪', timeout: 15000 });
      const d = await pageData(page);
      if (d.denied) throw new Error('库存估值页被权限拦截 denied=true（finance.manage 未下发？）');
    }, true);
  } catch (e) {
    console.log('PROBE ERROR:', (e && e.message) || e);
  } finally {
    try { await mp.disconnect(); } catch (e) { /* 忽略断开异常 */ }
  }
  suite.summary();
})();
