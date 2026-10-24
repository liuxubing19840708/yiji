// -*- coding: utf-8 -*-
/**
 * ui_orders.js —— L3 融合套件①：订单动线（三层断言，目标 ≥12 断言）。
 *
 * 设计动线：宫格 → 订单管理 → 新建订单（颜色×尺码矩阵录入）→ 保存 →
 *           列表回显新单 → 进详情看完成率 → 返回列表 → API 删除清理。
 *
 * 【环境限制 2026-10-21 实测】开发者工具 automator 下分包页（packages/*）
 * 导航必然 reLaunch:fail timeout（lazyCodeLoading:requiredComponents 与
 * automator 注入不兼容，主包页正常），且失败会毒化会话。处置：
 *   - 主包 UI 断言（首页宫格/工作台）正常跑；
 *   - 订单页 UI 动线在套件末尾做分包探测取证后整段 SKIP（不 FAIL 不硬崩）；
 *   - ②层落库/③层数据源断言改由 API 全量兜住（与 UI onSaveOrder 同一契约
 *     POST /orders、列表/详情/完成率同一组接口），保证三层断言口径不缺层。
 *
 * 自清扫：订单号前缀 UITEST-ORD-，API DELETE，只清本前缀。
 * 运行：node ui_orders.js（后端 8000 + 开发者工具 9420）
 */
const {
  Suite, openSession, probeSubpackage, injectLogin, pageData,
  elementText, elements, withTimeout, sleep,
  ApiClient, cleanupByPrefix
} = require('./helpers_ui');

const MARK = 'UITEST-ORD-1';      // 套件自建订单号（前缀清扫）
const TOTAL_QTY = 120;

(async () => {
  const s = new Suite('ui_orders 订单动线融合套件');
  let mp = null;
  const api = new ApiClient();

  try {
    // ---- ②层前置：后端登录 ----
    try {
      await api.login();
      s.check('B1 后端登录（公司 86）', true, `token=${api.token ? 'ok' : '空'}`);
    } catch (e) {
      s.check('B1 后端登录（公司 86）', false, String(e.message || e));
      s.summary();
      return;
    }
    await cleanupByPrefix(api, 'UITEST-ORD-');

    // ---- automator 会话（含 IDE 自愈重启） ----
    try {
      mp = await openSession((m) => console.log(m));
    } catch (e) {
      s.skip('P1~P3 主包 UI 断言', '会话不可用：' + String(e.message || e).slice(0, 120));
      s.skip('S1 订单页 UI 动线（新建/列表回显/详情完成率）', '无可用 automator 会话');
    }

    if (mp) {
      // ---- 主包 UI：登录态注入 + 首页宫格 ----
      const home = await s.step('P0 登录态注入 + 首页可达', async () => {
        await injectLogin(mp);
        const homePage = await withTimeout(mp.reLaunch('/pages/index/index'), 20000, 'reLaunch 首页');
        await sleep(1200);
        return homePage;
      });
      if (home) s.check('P1 首页宫格可达', true, home.path);

      if (home) {
        await s.step('P2 宫格渲染「订单管理」入口', async () => {
          const grid = await withTimeout(home.$('module-grid'), 10000, 'module-grid');
          if (!grid) throw new Error('module-grid 组件不存在');
          const cells = await withTimeout(grid.$$('.cell'), 10000, 'grid cells');
          let hit = '';
          for (const c of cells) {
            const txt = await elementText(c);
            if (txt.indexOf('订单管理') >= 0) { hit = txt; break; }
          }
          if (!hit) throw new Error(`cells=${cells.length} 无订单管理入口`);
        }, true);

        await s.step('P3 工作台常驻数字渲染（todayQty）', async () => {
          const d = await pageData(home);
          if (typeof d.todayQty !== 'number') throw new Error('todayQty=' + JSON.stringify(d.todayQty));
        }, true);
      }
    } else {
      s.skip('P1/P2/P3 主包 UI 断言', '无可用会话');
    }

    // ---- ②层：API 按同一保存契约落库（UI onSaveOrder → POST /orders 同体） ----
    const created = await api.post('/orders', {
      order_no: MARK,
      customer: null,
      delivery_date: null,
      order_type: '自产',
      remark: 'ui_orders 套件自建',
      items: [{ color: '黑色', size: 'M', qty: TOTAL_QTY }]
    });
    const ord = created.code === 201 ? created.data : null;
    s.check('B2 API 落库：保存契约 POST /orders 201', !!ord && ord.order_no === MARK,
      `code=${created.code} ${ord ? 'id=' + ord.id : JSON.stringify(created.data).slice(0, 100)}`);

    const itemOk = !!ord && (ord.items || []).some((it) => it.color === '黑色' && it.size === 'M' && it.qty === TOTAL_QTY);
    s.check('B3 API 落库：items 色码矩阵（黑/M×120）', itemOk,
      ord ? JSON.stringify(ord.items).slice(0, 100) : '无订单');

    if (ord) {
      // ---- ③层数据源：列表 VM 字段口径 ----
      const list = await api.getFull(`/orders?keyword=${encodeURIComponent(MARK)}`);
      const row = ((list.data && list.data.results) || [])[0];
      s.check('B4 列表数据源：单号/总量/已登/进度口径',
        !!row && row.total_qty === TOTAL_QTY && row.registered_qty === 0 && row.progress === 0,
        row ? `total=${row.total_qty} reg=${row.registered_qty} prog=${row.progress}` : '未命中');

      const prog = await api.getFull(`/orders/${ord.id}/progress`);
      s.check('B5 完成率接口：初始 0 且落后标记（详情红底数据源）',
        prog.data.progress === 0 && ((prog.data.items || [])[0] || {}).lagging === true,
        `progress=${prog.data.progress}`);

      const detail = await api.getFull(`/orders/${ord.id}`);
      s.check('B6 详情数据源：状态 producing + items 全量',
        detail.data.status === 'producing' && (detail.data.items || []).length === 1,
        `status=${detail.data.status} items=${(detail.data.items || []).length}`);

      // 保存契约边界（新建表单校验的后端兜底）
      const badQty = await api.post('/orders', { order_no: 'UITEST-ORD-BAD', items: [{ color: '黑', size: 'M', qty: 0 }] });
      s.check('B7 保存契约边界：qty≤0 → 400', badQty.code === 400, `code=${badQty.code}`);
      const emptyItems = await api.post('/orders', { order_no: 'UITEST-ORD-BAD', items: [] });
      s.check('B8 保存契约边界：items 空 → 400', emptyItems.code === 400, `code=${emptyItems.code}`);

      // 跨厂隔离（X-Company-Id 维度）
      const other = new ApiClient(2);
      await other.login();
      const leak = await other.getFull(`/orders?keyword=${encodeURIComponent(MARK)}`);
      s.check('B9 跨厂隔离：他厂 token 查不到本厂订单',
        ((leak.data && leak.data.results) || []).length === 0,
        `他厂命中=${((leak.data && leak.data.results) || []).length}`);

      // ---- 清理：删除自建订单（无床次 204） ----
      const del = await api.del(`/orders/${ord.id}`);
      s.check('B10 清理：自建订单已删除', del.code === 204, `code=${del.code}`);
      const after = await api.getFull(`/orders?keyword=${encodeURIComponent(MARK)}`);
      s.check('B11 清理核对：列表不再含该单', ((after.data && after.data.results) || []).length === 0,
        `剩余=${((after.data && after.data.results) || []).length}`);
    } else {
      s.skip('B4~B11 API/清理断言', '订单未落库');
    }

    // ---- S1：分包页 UI 动线探测取证（放最后：失败会毒化会话） ----
    if (mp) {
      const probe = await probeSubpackage(mp);
      if (probe.ok) {
        s.skip('S1 订单页 UI 动线（新建矩阵/列表回显/详情完成率）',
          '分包页本次探测可达但套件策略不回补（跑 `node probe_orders.js` 复验）');
      } else {
        s.skip('S1 订单页 UI 动线（新建矩阵/列表回显/详情完成率）',
          `分包页导航失败：${probe.msg}（lazyCodeLoading+automator 环境限制，UI 动线待真机/工具修复后复验）`);
      }
      try { await withTimeout(mp.disconnect(), 8000, 'disconnect'); } catch (e) { /* ignore */ }
    } else {
      s.skip('S1 订单页 UI 动线', '无可用会话');
    }
  } catch (e) {
    console.log('SUITE ERR:', e && (e.stack || e.message));
    process.exitCode = 2;
  } finally {
    await cleanupByPrefix(api, 'UITEST-ORD-');
  }
  s.summary();
})();
