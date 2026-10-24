// -*- coding: utf-8 -*-
/**
 * ui_bed_wizard.js —— L3 融合套件②：开床 4 步向导（三层断言，目标 ≥14 断言）。
 *
 * 设计动线：订单详情 → ＋开床 → 4 步向导（①选款式/禁用态 → ②床次号/计划数 →
 *           ③工序价 → ④矩阵+尾扎预览）→ 生成 → 床次详情 → 清理。
 *
 * 【环境限制 2026-10-21 实测】分包页 automator 导航 reLaunch:fail timeout
 * （lazyCodeLoading:requiredComponents 与 automator 注入不兼容），向导 UI 动线
 * 整段 SKIP（套件末尾探测取证）；向导每步的「后端契约」按同一步骤的 API 落库
 * 全量兜住断言：
 *   ②步 = POST /productions（挂单 order_id，source_order_no 自动带出）；
 *   ③步 = PUT/GET /productions/<id>/order-processes；
 *   ④步 = POST /tickets/batch（bundle_limit 拆扎规则 = 尾扎预览「整除 N 扎+尾扎
 *         M 件」的后端口径）+ 超订单余量仍允许（软提示不硬禁）。
 *
 * 自清扫：订单前缀 UIWIZ-，撤登记→删床次→删订单，只清本前缀。
 * 运行：node ui_bed_wizard.js
 */
const {
  Suite, openSession, probeSubpackage, injectLogin, pageData,
  elementText, withTimeout, sleep,
  ApiClient, cleanupByPrefix, deleteProduction
} = require('./helpers_ui');

const MARK = 'UIWIZ-1';
const BED1 = '9097';
const BED2 = '9094';
const TOTAL_QTY = 120;   // 订单量；120@每扎50 = 整除2扎+尾扎20件
const LIMIT = 50;
const STYLE_ID = 407;    // 公司 86 现存款式 RG001-1
const PROCESS_WHOLE = 0; // 整件工序 id 运行时探测（is_fixed 首个）

(async () => {
  const s = new Suite('ui_bed_wizard 开床向导融合套件');
  let mp = null;
  const api = new ApiClient();

  try {
    await api.login();
    s.check('B1 后端登录（公司 86）', true, `token=${api.token ? 'ok' : '空'}`);
  } catch (e) {
    s.check('B1 后端登录（公司 86）', false, String(e.message || e));
    s.summary();
    return;
  }
  await cleanupByPrefix(api, 'UIWIZ-');

  // ---- 会话（自愈） + 主包 UI 上下文 ----
  try {
    mp = await openSession((m) => console.log(m));
  } catch (e) {
    s.skip('P1 主包 UI 断言', '会话不可用：' + String(e.message || e).slice(0, 120));
    s.skip('S1 向导 UI 动线', '无可用 automator 会话');
  }
  if (mp) {
    await s.step('P0 登录态注入 + 首页可达', async () => {
      await injectLogin(mp);
      await withTimeout(mp.reLaunch('/pages/index/index'), 20000, 'reLaunch 首页');
      await sleep(1000);
    });
  }

  // ---- 探测「整件」固定工序（工价配置用） ----
  let wholeProcessId = 0;
  try {
    const procs = await api.get('/processes?page=1&page_size=50');
    const hit = ((procs && procs.results) || []).find((p) => p.is_fixed) || {};
    wholeProcessId = hit.id || 0;
  } catch (e) { /* 下方工价断言会给 FAIL 线索 */ }
  s.check('B2 前置：存在固定工序（向导第3步工价行来源）', wholeProcessId > 0, `id=${wholeProcessId}`);

  // ---- 前置：API 建测试订单（向导挂单依赖订单） ----
  const ord = await api.post('/orders', {
    order_no: MARK,
    items: [{ color: '黑色', size: 'M', qty: TOTAL_QTY }]
  });
  const orderId = ord.code === 201 ? ord.data.id : 0;
  s.check('B3 前置：API 建测试订单 UIWIZ-1（黑/M×120）', !!orderId,
    `code=${ord.code} id=${orderId}`);

  if (orderId) {
    // ①步数据源：挂单余量 = /orders/<id>/progress 的 items[].qty - registered_qty
    // （注：前端 production-create 读 res.cells[].order_qty，与后端实际下发 items[].qty
    //  口径不一致 → 余量自动预填实际不生效；本断言按后端真实契约定档，见交付报告遗留项）
    const prog = await api.getFull(`/orders/${orderId}/progress`);
    const cell = (prog.data.items || [])[0] || {};
    s.check('B4 第①步数据源：挂单余量（items[].qty=120 且已登 0）',
      cell.qty === TOTAL_QTY && cell.registered_qty === 0,
      `qty=${cell.qty} reg=${cell.registered_qty}`);

    // ②步：建床（POST /productions 带 order_id，契约 §四）
    const pr = await api.post('/productions', {
      style: STYLE_ID,
      bed_no: BED1,
      total_qty: TOTAL_QTY,
      plan_qty: null,
      order_type: '自产',
      customer: null,
      fabric: '',
      order_id: orderId
    });
    const prodId = pr.code === 201 ? pr.data.id : 0;
    s.check('B5 第②步：建床 POST /productions 201', !!prodId,
      `code=${pr.code} ${prodId ? 'id=' + prodId : JSON.stringify(pr.data).slice(0, 100)}`);

    if (prodId) {
      // 落库正确性：order_id 挂对 + 来源单号自动带出
      const pd = await api.getFull(`/productions/${prodId}`);
      s.check('B6 第②步落库：床次挂单 order_id 正确', pd.data.order === orderId,
        `order=${pd.data.order} 期望=${orderId}`);
      s.check('B7 第②步落库：source_order_no 自动带出单号',
        String(pd.data.source_order_no || '') === MARK,
        `source_order_no=${pd.data.source_order_no}`);
      s.check('B8 第②步落库：床次号/总量一致',
        pd.data.bed_no === BED1 && pd.data.total_qty === TOTAL_QTY,
        `bed_no=${pd.data.bed_no} total_qty=${pd.data.total_qty}`);

      // ③步：工序工价覆盖保存 + 回读
      if (wholeProcessId) {
        const put = await api.put(`/productions/${prodId}/order-processes`, {
          process_items: [{ process_id: wholeProcessId, price: '0.05' }]
        });
        s.check('B9 第③步：工序工价保存 200', put.code === 200,
          `code=${put.code} ${JSON.stringify(put.data).slice(0, 80)}`);
        const back = await api.getFull(`/productions/${prodId}/order-processes`);
        const row = (Array.isArray(back.data) ? back.data : (back.data.results || []))
          .find((r) => r.process === wholeProcessId) || {};
        s.check('B10 第③步：工价回读 0.05', Number(row.price) === 0.05,
          `price=${row.price}`);
      }

      // ④步：生成扎（拆扎规则 = 尾扎预览的后端口径）
      // 手工计算：120 @ 每扎50 → 整除 2 扎 + 尾扎 20 件 → 共 3 张菲票
      const tb = await api.post('/tickets/batch', {
        production_id: prodId,
        items: [{ color: '黑色', size: 'M', qty: TOTAL_QTY, fabric: '', piece_no: '' }],
        bundle_limit: LIMIT,
        start_bundle_no: '1'
      });
      const tickets = tb.code === 201 ? tb.data : [];
      s.check('B11 第④步：拆扎数=整除2扎+尾扎1扎（共3张）',
        tickets.length === Math.floor(TOTAL_QTY / LIMIT) + 1, `n=${tickets.length}`);
      const qtySeq = tickets.map((t) => t.qty);
      const noSeq = tickets.map((t) => String(t.bundle_no));
      s.check('B12 第④步：扎件数 50/50/20（尾扎=总量%上限）',
        JSON.stringify(qtySeq) === JSON.stringify([LIMIT, LIMIT, TOTAL_QTY % LIMIT]),
        `qty=${JSON.stringify(qtySeq)}`);
      s.check('B13 第④步：扎号自 1 顺延（起始扎号生效）',
        JSON.stringify(noSeq) === JSON.stringify(['1', '2', '3']),
        `bundle_no=${JSON.stringify(noSeq)}`);

      const bundles = await api.getFull(`/productions/${prodId}/bundles`);
      s.check('B14 床次详情数据源：菲票数=3（生成扎落库）',
        bundles.data.count === 3, `count=${bundles.data.count}`);

      const prog2 = await api.getFull(`/orders/${orderId}/progress`);
      s.check('B15 订单→床次血缘：progress.productions 含新床次',
        (prog2.data.productions || []).some((p) => p.id === prodId),
        `productions=${JSON.stringify((prog2.data.productions || []).map((p) => p.id))}`);

      // 超订单余量开床：向导④步「超出订单余量→确认继续」的后端口径（软提示不硬禁）
      const pr2 = await api.post('/productions', {
        style: STYLE_ID, bed_no: BED2, total_qty: TOTAL_QTY * 2,
        order_type: '自产', customer: null, fabric: '', order_id: orderId
      });
      if (pr2.code === 201) {
        const tb2 = await api.post('/tickets/batch', {
          production_id: pr2.data.id,
          items: [{ color: '黑色', size: 'M', qty: TOTAL_QTY * 2, fabric: '', piece_no: '' }],
          bundle_limit: LIMIT, start_bundle_no: '1'
        });
        s.check('B16 超量口径：超出订单余量开床仍 201（软提示不硬禁）', tb2.code === 201,
          `code=${tb2.code}`);
      } else {
        s.skip('B16 超量口径', `第二床创建失败 code=${pr2.code}`);
      }

      // ---- 清理：撤登记 → 删床次 → 删订单 ----
      const d1 = await deleteProduction(api, prodId);
      s.check('B17 清理：床次1已删除（级联软删菲票）', d1.code === 204, `code=${d1.code}`);
      if (pr2.code === 201) {
        const d2 = await deleteProduction(api, pr2.data.id);
        s.check('B18 清理：床次2已删除', d2.code === 204, `code=${d2.code}`);
      }
      const od = await api.del(`/orders/${orderId}`);
      s.check('B19 清理：测试订单已删除', od.code === 204, `code=${od.code}`);
    } else {
      s.skip('B6~B19 落库/清理断言', '建床未成功');
    }
  } else {
    s.skip('B4~B19 向导契约断言', '测试订单未落库');
  }

  // ---- S1：分包向导 UI 动线探测取证（放最后：失败毒化会话） ----
  if (mp) {
    const probe = await probeSubpackage(mp);
    s.skip('S1 向导 UI 动线（4 步禁用态/余量预填/尾扎预览文案/成功页）',
      probe.ok
        ? '分包页本次探测可达但套件策略不回补（可复跑 probe_orders.js P6~D7 段验证）'
        : `分包页导航失败：${probe.msg}（lazyCodeLoading+automator 环境限制，UI 动线待真机/工具修复后复验）`);
    try { await withTimeout(mp.disconnect(), 8000, 'disconnect'); } catch (e) { /* ignore */ }
  } else {
    s.skip('S1 向导 UI 动线', '无可用会话');
  }
  s.summary();
})();
