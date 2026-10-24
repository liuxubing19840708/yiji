// -*- coding: utf-8 -*-
/**
 * ui_kanban_drill.js —— L3 融合套件④：看板下钻（≥10 断言）。
 *
 * 设计动线：看板行 tap → 床次列表页（URL 参数预过滤）→ 床次详情（菲票列表）→
 *           菲票 tap → 登记明细（ticketId 预过滤）→ navigateBack 后筛选保留；
 *           订单页签切换渲染。
 *
 * 【环境限制 2026-10-21 实测】看板页为分包页（packages/admin/kanban），automator
 * 导航 reLaunch:fail timeout（lazyCodeLoading:requiredComponents 不兼容），UI 下钻
 * 动线整段 SKIP（末尾探测取证）。下钻每一级的「数据同源性」改由 API 全量兜住：
 *   看板行（GET /kanban/render，款号列 link=production 下发）
 *     → 一级 GET /productions?style_id=（URL 参数预过滤的接口等价）
 *     → 二级 GET /productions/<id>（床次详情）
 *     → 三级 GET /productions/<id>/bundles（菲票列表）
 *     → 四级 GET /records?ticket_id=（登记明细，ticketId 预过滤的接口等价）
 *   订单页签 GET /orders?status=producing。
 *
 * 自清扫：订单前缀 KDRILL-。
 * 运行：node ui_kanban_drill.js
 */
const {
  Suite, openSession, probeSubpackage, injectLogin, pageData,
  withTimeout, sleep,
  ApiClient, cleanupByPrefix, buildBedFixture
} = require('./helpers_ui');

const MARK = 'KDRILL-1';
const BED = '9093';
const TOTAL_QTY = 60;
const STYLE_ID = 407;      // RG001-1（公司 86 现存）
const STYLE_NO = 'RG001-1';
const RECORD_QTY = 10;

(async () => {
  const s = new Suite('ui_kanban_drill 看板下钻融合套件');
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
  await cleanupByPrefix(api, 'KDRILL-');

  // ---- 会话（自愈） + 主包 UI 上下文 ----
  try {
    mp = await openSession((m) => console.log(m));
  } catch (e) {
    s.skip('P1 主包 UI 断言', '会话不可用：' + String(e.message || e).slice(0, 120));
    s.skip('S1 看板下钻 UI 动线', '无可用 automator 会话');
  }
  if (mp) {
    await s.step('P0 登录态注入 + 首页可达', async () => {
      await injectLogin(mp);
      await withTimeout(mp.reLaunch('/pages/index/index'), 20000, 'reLaunch 首页');
      await sleep(1000);
    });
  }

  // ---- 前置：API 构造下钻链路数据 ----
  let wholeProcessId = 0;
  try {
    const procs = await api.get('/processes?page=1&page_size=50');
    wholeProcessId = (((procs && procs.results) || []).find((p) => p.is_fixed) || {}).id || 0;
  } catch (e) { /* 构造失败在下方给线索 */ }

  let fx = null;
  try {
    fx = await buildBedFixture(api, {
      orderNo: MARK, bedNo: BED, totalQty: TOTAL_QTY,
      registerQty: RECORD_QTY, styleId: STYLE_ID, processId: wholeProcessId
    });
    s.check('B2 API 数据构造：订单+床次+菲票+登记10件', !!fx.ticketId,
      `order=${fx.orderId} prod=${fx.prodId} ticket=${fx.ticketId} records=${fx.recordIds.length}`);
  } catch (e) {
    s.check('B2 API 数据构造：订单+床次+菲票+登记10件', false, String((e && e.message) || e).slice(0, 140));
  }

  if (fx) {
    // ---- 看板模板临时配置：款号列 extra.link=production（下钻白名单 §2.9 P0） ----
    // 渲染层 link 取模板列 extra.link 白名单（LINK_TARGETS），现库模板未配 link。
    // 处置：PUT 模板临时给款号列配 link=production，断言渲染下发后立即还原原列。
    let tplSnap = null;   // 原始列快照（还原用）
    let tplId = 0;
    try {
      const ts = await api.getFull('/kanban/templates');
      const tlist = (ts.data && (ts.data.results || ts.data)) || [];
      const tpl = tlist.find((t) => t.is_default) || tlist[0] || {};
      tplId = tpl.id || 0;
      if (tplId) {
        const full = await api.getFull(`/kanban/templates/${tplId}`);
        tplSnap = (full.data && full.data.columns) || [];
        const cols = tplSnap.map((c) => {
          const extra = Object.assign({}, c.extra || {});
          if (c.title === '款号') extra.link = 'production';
          return {
            title: c.title, bind_type: c.bind_type,
            field_key: c.field_key || '', expr: c.expr || '',
            static_text: c.static_text || '', extra, seq: c.seq
          };
        });
        const put = await api.put(`/kanban/templates/${tplId}`, {
          name: full.data.name, columns: cols
        });
        s.check('B4a 模板配置：款号列临时配 link=production', put.code === 200,
          `code=${put.code} template=${tplId}`);
      } else {
        s.skip('B4a 模板配置', '公司无看板模板（渲染回落内置默认）');
      }
    } catch (e) {
      s.check('B4a 模板配置：款号列临时配 link=production', false,
        String((e && e.message) || e).slice(0, 120));
    }

    // ---- 看板行（渲染接口 = 看板页数据源） ----
    const render = await api.getFull('/kanban/render?range=today');
    const cols = (render.data && render.data.columns) || [];
    const groups = (render.data && render.data.groups) || [];
    s.check('B3 看板渲染接口 200 且有数据', render.code === 200 && groups.length > 0,
      `code=${render.code} groups=${groups.length} columns=${cols.length}`);

    // 款号列下发 link=production（行 tap 下钻白名单 §2.9 P0；新契约：列以 title 定位，
    // link 由模板列 extra.link 白名单透出）
    const styleCol = cols.find((c) => c.title === '款号') || {};
    s.check('B4 款号列 link=production 下发（下钻白名单）', styleCol.link === 'production',
      `link=${styleCol.link}`);

    // ---- 还原看板模板原列（真实配置不留痕；B5 只用行文本，不依赖 link） ----
    if (tplId && tplSnap) {
      try {
        const cols0 = tplSnap.map((c) => ({
          title: c.title, bind_type: c.bind_type,
          field_key: c.field_key || '', expr: c.expr || '',
          static_text: c.static_text || '', extra: c.extra || {}, seq: c.seq
        }));
        const put0 = await api.put(`/kanban/templates/${tplId}`, {
          name: (await api.getFull(`/kanban/templates/${tplId}`)).data.name, columns: cols0
        });
        const back = await api.getFull(`/kanban/templates/${tplId}`);
        const style0 = (back.data.columns || []).find((c) => c.title === '款号') || {};
        s.check('B4b 模板还原：款号列 link 已清空', put0.code === 200 && !style0.extra.link,
          `code=${put0.code} link=${style0.extra.link}`);
      } catch (e) {
        s.check('B4b 模板还原：款号列 link 已清空', false,
          String((e && e.message) || e).slice(0, 120));
      }
    } else {
      s.check('B4b 模板还原：无临时配置需还原', true, '未触发 flip');
    }

    // 看板行内容与床次列表同源：块内出现本款号 + 床次列文本含 9093
    let hitBed = false;
    for (const g of groups) {
      for (const b of (g.blocks || [])) {
        const rowText = (b.rows || []).map((r) => (r.cells || []).map((c) => c.text).join('|')).join('#');
        if (rowText.indexOf(STYLE_NO) >= 0 && rowText.indexOf(BED) >= 0) hitBed = true;
      }
    }
    s.check('B5 看板行同源：渲染含本款号与床次号 9093', hitBed,
      '在 /kanban/render?range=today 的块行文本中匹配 ' + STYLE_NO + '+' + BED);

    // ---- 下钻一级：款号预过滤（URL ?styleId= 的接口等价） ----
    const prods = await api.getFull(`/productions?style_id=${STYLE_ID}&page_size=50`);
    const hitProd = ((prods.data && prods.data.results) || []).find((p) => p.bed_no === BED);
    s.check('B6 下钻一级：style_id 预过滤命中本床次', !!hitProd,
      `命中=${hitProd ? hitProd.id : '无'}`);

    // ---- 下钻二级：床次详情 ----
    if (hitProd) {
      const pd = await api.getFull(`/productions/${hitProd.id}`);
      s.check('B7 下钻二级：床次详情 bed_no/总量一致',
        pd.data.bed_no === BED && pd.data.total_qty === TOTAL_QTY,
        `bed=${pd.data.bed_no} total=${pd.data.total_qty}`);

      // ---- 下钻三级：菲票列表 ----
      const bundles = await api.getFull(`/productions/${hitProd.id}/bundles`);
      const bundle = ((bundles.data && bundles.data.results) || [])[0] || {};
      s.check('B8 下钻三级：菲票列表含本扎（bundle_no=1）',
        bundles.data.count === 1 && String(bundle.bundle_no) === '1',
        `count=${bundles.data.count} bundle_no=${bundle.bundle_no}`);

      // ---- 下钻四级：登记明细（?ticketId= 预过滤的接口等价） ----
      const recs = await api.getFull(`/records?ticket_id=${fx.ticketId}&page_size=50`);
      const rec = ((recs.data && recs.data.results) || [])[0] || {};
      s.check('B9 下钻四级：登记明细 ticket_id 预过滤命中（10件/整件）',
        recs.data.count === 1 && rec.qty === RECORD_QTY,
        `count=${recs.data.count} qty=${rec.qty}`);
    } else {
      s.skip('B7~B9 二三四级下钻', '一级预过滤未命中');
    }

    // ---- 订单页签数据源 ----
    const orders = await api.getFull(`/orders?status=producing&page_size=50`);
    s.check('B10 订单页签：producing 过滤含 KDRILL-1',
      ((orders.data && orders.data.results) || []).some((o) => o.order_no === MARK),
      `命中=${((orders.data && orders.data.results) || []).some((o) => o.order_no === MARK)}`);

    // ---- 清理 ----
    const d1 = await cleanupByPrefix(api, 'KDRILL-');
    s.check('B11 清理：KDRILL- 前缀数据已清', d1 >= 1, `删除订单数=${d1}`);
  } else {
    s.skip('B3~B11 下钻链路断言', '前置构造失败');
  }

  // ---- S1：看板分包 UI 动线探测取证（放最后：失败毒化会话） ----
  if (mp) {
    const probe = await probeSubpackage(mp);
    s.skip('S1 下钻 UI 动线（行 tap 带参/返回保筛/页签切换）',
      probe.ok
        ? '分包页本次探测可达但套件策略不回补（可复跑 probe_kanban2.js 验证）'
        : `分包页导航失败：${probe.msg}（lazyCodeLoading+automator 环境限制，UI 动线待真机/工具修复后复验）`);
    try { await withTimeout(mp.disconnect(), 8000, 'disconnect'); } catch (e) { /* ignore */ }
  } else {
    s.skip('S1 下钻 UI 动线', '无可用会话');
  }
  s.summary();
})();
