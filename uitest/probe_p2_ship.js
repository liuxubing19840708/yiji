// -*- coding: utf-8 -*-
/**
 * probe_p2_ship.js —— P2 采购补全+出货 前端探针（SF-P2-1，INT 集成门禁，2026-10-24）。
 *
 * 断言目标（只读探针：全部 GET，不建数、不改数）：
 *   1) 首页宫格：P2 三项（供应商 suppliers / 暂收IQC receipts / 出货管理 shipment）
 *      在管理员宫格可见；
 *   2) 供应商列表页（/packages/admin/suppliers）：列表拉取收敛（loaded=true；
 *      运行中 8000 为旧进程无 P2 端点时 error=true → 记 SKIP 列入 INT 待办）；
 *   3) 暂收列表页（/packages/admin/receipts）：同上；
 *   4) 出货列表页（/packages/admin/shipments）：同上；
 *   5) 需求汇总页（/packages/admin/demand-summary）：页面就绪（denied=false，
 *      旧后端 404 → SKIP）；
 *   6) 出货详情页 create 模式（/packages/admin/shipment-detail?mode=create）：
 *      表单初始化收敛（loading=false；orders/customers 为存量端点可实拉）。
 *
 * 前置：微信开发者工具自动化端口 9420；后端 8000 已启动（--noreload，
 * 注意：运行中 8000 是旧进程没有 P2 新端点，列表实拉会 404 —— 与 SF-P2-2
 * probe_p2_front 相同处理：静态核对为准，实拉 SKIP 列入 INT 待办，
 * 勿重启勿 kill 后端）。
 * 运行：cd D:\project\扫菲\uitest && node probe_p2_ship.js
 * 连接失败则打印 cli.bat auto 提示，结果=脚本交付待真机/工具验证（exitCode=3）。
 */
const {
  Suite, openSession, pageData, waitPageData, injectLogin, withTimeout
} = require('./helpers_ui.js');

const suite = new Suite('P2 采购补全+出货 前端探针');
const GRID_KEYS = ['suppliers', 'receipts', 'shipment'];

/** 列表页通用收敛断言：error=true（旧后端 404）→ SKIP 进 INT 待办，不算 FAIL */
async function assertListReady(mp, url, label) {
  const page = await withTimeout(mp.reLaunch(url), 20000, `reLaunch ${label}`);
  await waitPageData(page, (d) => d && (d.loaded === true || d.denied === true),
    { label: `${label} 收敛`, timeout: 15000 });
  const d = await pageData(page);
  if (d.denied) throw new Error(`${label} 被权限拦截 denied=true（权限点未下发？）`);
  if (d.error) {
    suite.skip(label, '列表接口报错（8000 旧进程无 P2 端点 404）→ 静态核对为准，实拉列入 INT 待办');
    return null;
  }
  suite.check(`${label} 列表加载`, d.loaded === true && Array.isArray(d.rows),
    `rows=${(d.rows || []).length}`);
  return d;
}

(async () => {
  let mp = null;
  try {
    mp = await openSession(console.log);
  } catch (e) {
    console.log('CONNECT FAIL:', (e && e.message) || e);
    console.log('运行说明：cli.bat auto --project D:\\project\\扫菲\\frontend --auto-port 9420');
    console.log('再运行 node probe_p2_ship.js；本次结果=脚本交付，待真机/工具验证。');
    process.exitCode = 3;
    suite.summary();
    return;
  }

  try {
    await suite.step('P2 注入登录态（管理员+86）', () => injectLogin(mp));

    // ---- D1：首页宫格三项可见（warehouse.supplier / warehouse.manage / shipment.view） ----
    const gridData = await suite.step('D1 读宫格数据', async () => {
      const page = await withTimeout(mp.reLaunch('/pages/index/index'), 20000, 'reLaunch index');
      await waitPageData(page, (d) => Array.isArray(d.modules) && d.modules.length > 0,
        { label: '宫格就绪', timeout: 15000 });
      return pageData(page);
    });
    if (gridData) {
      for (const key of GRID_KEYS) {
        const m = (gridData.modules || []).find((x) => x && x.key === key);
        suite.check(`D1 宫格项 ${key} 可见`, !!m && !!m.visible,
          m ? (m.visible ? '' : '(hidden)') : '(absent)');
      }
    }

    // ---- D2：供应商列表（warehouse.supplier） ----
    await suite.step('D2 供应商列表页', async () => {
      await assertListReady(mp, '/packages/admin/suppliers/index', '供应商列表');
    });

    // ---- D3：暂收列表（warehouse.manage） ----
    await suite.step('D3 暂收列表页', async () => {
      await assertListReady(mp, '/packages/admin/receipts/index', '暂收列表');
    });

    // ---- D4：出货列表（shipment.view） ----
    await suite.step('D4 出货列表页', async () => {
      await assertListReady(mp, '/packages/admin/shipments/index', '出货列表');
    });

    // ---- D5：需求汇总页就绪（warehouse.manage；旧后端 404 → SKIP） ----
    await suite.step('D5 需求汇总页就绪', async () => {
      const page = await withTimeout(mp.reLaunch('/packages/admin/demand-summary/index'),
        20000, 'reLaunch demand-summary');
      await waitPageData(page, (d) => d && (d.loaded === true || d.denied === true),
        { label: '汇总页收敛', timeout: 15000 });
      const d = await pageData(page);
      if (d.denied) throw new Error('需求汇总页被权限拦截 denied=true（warehouse.manage 未下发？）');
      if (d.error) {
        suite.skip('需求汇总页', '汇总接口报错（8000 旧进程无 /demand-summary 404）→ 列入 INT 待办');
        return;
      }
      suite.check('需求汇总页加载', d.loaded === true && Array.isArray(d.rows),
        `rows=${(d.rows || []).length}`);
    });

    // ---- D6：出货详情 create 模式表单收敛（orders/customers 为存量端点） ----
    await suite.step('D6 出货详情 create 表单收敛', async () => {
      const page = await withTimeout(
        mp.reLaunch('/packages/admin/shipment-detail/index?mode=create'),
        20000, 'reLaunch shipment-detail create');
      await waitPageData(page, (d) => d && (d.loading === false || d.denied === true),
        { label: 'create 表单收敛', timeout: 20000 });
      const d = await pageData(page);
      if (d.denied) throw new Error('出货页被权限拦截 denied=true（shipment.view 未下发？）');
      suite.check('create 表单初始化', d.mode === 'create' && d.error === false
        && Array.isArray(d.rows), `mode=${d.mode} orderLabels=${(d.orderLabels || []).length}`);
    });
  } catch (e) {
    console.log('PROBE ERROR:', (e && e.message) || e);
  } finally {
    try { await mp.disconnect(); } catch (e) { /* 忽略断开异常 */ }
  }
  suite.summary();
})();
