// -*- coding: utf-8 -*-
/**
 * probe_p1.js —— P1 成本定额BOM 前端探针（INT 集成门禁，2026-10-24）。
 *
 * 断言目标：
 *   1) 首页宫格：P1 四项（物料管理/BOM管理/按床领料/成本管理）在管理员宫格中可见；
 *   2) 物料页（/packages/admin/tech-materials）：列表加载完成不报错（空库允许 0 行）；
 *   3) 成本页（/packages/admin/cost）：列表加载完成不报错（空库允许 0 行）。
 *
 * 前置：微信开发者工具自动化端口 9420；后端 8000 已启动。
 * 运行：cd D:\project\扫菲\uitest && node probe_p1.js
 * 只读探针：全部 GET，不建数、不改数（真实厂 86 零接触原则）。
 */
const {
  Suite, openSession, pageData, waitPageData, injectLogin, withTimeout
} = require('./helpers_ui.js');

const suite = new Suite('P1 成本定额BOM 前端探针');
const GRID_KEYS = ['tech-materials', 'tech-boms', 'material-issue', 'cost'];

(async () => {
  let mp = null;
  try {
    mp = await openSession(console.log);
  } catch (e) {
    console.log('CONNECT FAIL:', (e && e.message) || e);
    console.log('运行说明：cli.bat auto --project D:\\project\\扫菲\\frontend --auto-port 9420');
    console.log('再运行 node probe_p1.js；本次结果=脚本交付，待真机/工具验证。');
    process.exitCode = 3;
    suite.summary();
    return;
  }

  try {
    await suite.step('P1 注入登录态（管理员+86）', () => injectLogin(mp));

    // ---- D1：首页宫格四项可见 ----
    await suite.step('D1 首页宫格 P1 四项可见', async () => {
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

    // ---- D2：物料页加载 ----
    await suite.step('D2 物料页列表加载（error=false 且 loading 收敛）', async () => {
      const page = await withTimeout(mp.reLaunch('/packages/admin/tech-materials/index'),
        20000, 'reLaunch tech-materials');
      await waitPageData(page, (d) => d && d.loading === false,
        { label: '物料列表收敛', timeout: 15000 });
      const d = await pageData(page);
      if (d.error) throw new Error('物料列表接口报错 error=true');
      if (!Array.isArray(d.materials)) throw new Error('materials 非数组');
    }, true);

    // ---- D3：BOM 页加载 ----
    await suite.step('D3 BOM 页加载', async () => {
      const page = await withTimeout(mp.reLaunch('/packages/admin/tech-boms/index'),
        20000, 'reLaunch tech-boms');
      await waitPageData(page, (d) => d && d.loading === false,
        { label: 'BOM 列表收敛', timeout: 15000 });
      const d = await pageData(page);
      if (d.error) throw new Error('BOM 列表接口报错 error=true');
    }, true);

    // ---- D4：成本页加载 ----
    await suite.step('D4 成本页列表加载（error=false 且 loading 收敛）', async () => {
      const page = await withTimeout(mp.reLaunch('/packages/admin/cost/index'),
        20000, 'reLaunch cost');
      await waitPageData(page, (d) => d && d.loading === false,
        { label: '成本列表收敛', timeout: 15000 });
      const d = await pageData(page);
      if (d.error) throw new Error('成本列表接口报错 error=true');
      if (!Array.isArray(d.rows)) throw new Error('rows 非数组');
    }, true);
  } catch (e) {
    console.log('PROBE ERROR:', (e && e.message) || e);
  } finally {
    try { await mp.disconnect(); } catch (e) { /* 忽略断开异常 */ }
  }
  suite.summary();
})();
