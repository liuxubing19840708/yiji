// -*- coding: utf-8 -*-
/**
 * ui_scan2.js —— L3 融合套件③：扫菲页（满扎徽标 + 手输码动线，≥12 断言）。
 *
 * 动线（扫菲页为主包 pages/scan/index，automator 可达，全 UI 真跑）：
 *   扫码失败停留 idle（mock scanCode，errMsg 教训）→ 手输码入口弹层 →
 *   404 提示（床次 99999）→ 真实候选查询（API 构造床次 9095：整件工序价已配、
 *   菲票 50 件、预登记 45 件≈满扎）→ 候选列表渲染 → 选中进入工序选择态 →
 *   满扎徽标「45/50」渲染（与 API /records/process-selector 一致）→ 清理。
 *
 * 前置 API 构造（UISCAN- 前缀，真实库自清扫）：
 *   订单 UISCAN-1（黑/M×50）→ 床次 9095（挂单）→ 配整件工价 →
 *   POST /tickets/batch 生成 1 张菲票（每扎上限=50）→ POST /records 登记 45 件。
 *
 * 降级预案（任务书要求）：若 automator 对 scan 页超时，完成 API 侧断言并把
 * UI 断言标 SKIP 输出原因，不硬崩（本次实测 scan 页为主包页可稳定驱动）。
 * 运行：node ui_scan2.js
 */
const {
  Suite, openSession, injectLogin, pageData, waitPageData,
  elementTap, elementText, elements, withTimeout, sleep,
  ApiClient, cleanupByPrefix, buildBedFixture
} = require('./helpers_ui');

const MARK = 'UISCAN-1';
const BED = '9095';
const TOTAL_QTY = 50;
const REGISTERED = 45;   // 接近满扎：徽标 45/50

(async () => {
  const s = new Suite('ui_scan2 扫菲页融合套件');
  let mp = null;
  let page = null;         // 扫菲页（可达时）
  const api = new ApiClient();
  let wholeProcessId = 0;
  let styleId = 0;
  let fx = null;

  try {
    await api.login();
    s.check('B1 后端登录（隔离测试公司）', true, `token=${api.token ? 'ok' : '空'}`);
  } catch (e) {
    s.check('B1 后端登录（隔离测试公司）', false, String(e.message || e));
    s.summary();
    return;
  }
  // 主流程包 try/finally：任何异常都保证清理数据 + 还原临时固定工序
  try {
  await cleanupByPrefix(api, 'UISCAN-');

  // ---- 前置 API 数据构造（②层底座） ----
  try {
    const procs = await api.get('/processes?page=1&page_size=50');
    wholeProcessId = (((procs && procs.results) || []).find((p) => p.is_fixed) || {}).id || 0;
  } catch (e) { /* 构造失败在下方给出线索 */ }

  try {
    const style = await api.post('/styles', {
      style_no: 'UISCAN-S1', name: 'UI扫码隔离测试款'
    });
    if (style.code !== 201) throw new Error(`创建隔离款式失败 code=${style.code}`);
    styleId = style.data.id;
    fx = await buildBedFixture(api, {
      orderNo: MARK, bedNo: BED, totalQty: TOTAL_QTY,
      registerQty: REGISTERED, styleId, processId: wholeProcessId
    });
    s.check('B2 API 数据构造：订单+床次+菲票+预登记45件', !!fx.ticketId,
      `order=${fx.orderId} prod=${fx.prodId} ticket=${fx.ticketId} records=${fx.recordIds.length}`);
    // selector 口径 = 固定工序 ∪ 员工指定工序（不含床次自配工序）：隔离测试公司 只有
    // 新公司默认只有「整件」1 个固定工序 → 单选态会跳过工序选择。
    // 在本隔离公司新建第二个固定工序，不修改任何共享档案。
    const secondProcess = await api.post('/processes', {
      name: 'UISCAN-辅助工序', default_price: '0.05', is_fixed: true, sort: 90
    });
    if (secondProcess.code !== 201) throw new Error('创建隔离辅助工序失败');
  } catch (e) {
    s.check('B2 API 数据构造：订单+床次+菲票+预登记45件', false, String((e && e.message) || e).slice(0, 140));
  }

  if (fx && wholeProcessId) {
    // ②层：满扎徽标的数据源（/records/process-selector registered_qty/cap）
    const sel = await api.getFull(`/records/process-selector?ticket_id=${fx.ticketId}`);
    const row = ((sel.data && sel.data.results) || []).find((p) => p.id === wholeProcessId) || {};
    s.check('B3 徽标数据源：selector registered_qty=45 / cap=50（≥2 工序不单选）',
      row.registered_qty === REGISTERED && row.cap === TOTAL_QTY &&
      ((sel.data && sel.data.results) || []).length >= 2,
      `reg=${row.registered_qty} cap=${row.cap} n=${((sel.data && sel.data.results) || []).length} single=${sel.data && sel.data.single}`);
  } else {
    s.skip('B3 徽标数据源', '前置构造不完整');
  }

  // ---- automator 会话（自愈） + 扫菲页 UI ----
  try {
    mp = await openSession((m) => console.log(m));
  } catch (e) {
    s.skip('P1~D2 扫菲页 UI 断言', '会话不可用：' + String(e.message || e).slice(0, 120));
  }

  if (mp) {
    page = await s.step('P0 登录态注入 + 扫菲页可达', async () => {
      await injectLogin(mp);
      // mock 扫码失败：onShow autoScan 吃掉 fail 停留 idle（mock 必带 errMsg）
      await withTimeout(mp.mockWxMethod('scanCode',
        function () { return { errMsg: 'scanCode:fail ui_scan2' }; }), 10000, 'mock scanCode');
      const p = await withTimeout(mp.reLaunch('/pages/scan/index'), 20000, 'reLaunch 扫菲页');
      await sleep(1800);
      return p;
    });

    if (page) {
      // P1 扫码失败停留 idle（不弹相机不白屏）
      await s.step('P1 扫码失败停留 idle', async () => {
        const d = await pageData(page);
        if (d.state !== 'idle') throw new Error('state=' + d.state);
      }, true);

      // P2/P3 手输码入口 → 弹层双输入
      await s.step('P2 idle 态手输码入口存在并点开弹层', async () => {
        await elementTap(page, '.lookup-entry', '手输查找入口');
        await waitPageData(page, (d) => d.lookupShow === true, { label: '手输弹层打开' });
      }, true);
      await s.step('P3 弹层双输入框（床次号+扎号）', async () => {
        const inputs = await elements(page, '.lookup-input');
        if (inputs.length !== 2) throw new Error('inputs=' + inputs.length);
      }, true);

      // P4 404 提示（真实 API：不存在的床次）
      await s.step('P4 404：不存在床次 → 「未找到菲票」提示', async () => {
        const inputs = await elements(page, '.lookup-input');
        await withTimeout(inputs[0].input('99999'), 8000, 'input 床次');
        await withTimeout(inputs[1].input('1'), 8000, 'input 扎号');
        await elementTap(page, '.lookup-search', '查询按钮');
        await waitPageData(page, (d) => !!d.lookupError, { label: '404 提示' });
        const d = await pageData(page);
        if (d.lookupError.indexOf('未找到菲票') < 0) throw new Error('提示异常: ' + d.lookupError);
        if (d.lookupShow !== true) throw new Error('404 后弹层被关闭（应保留）');
      }, true);

      // P5~D2 真实候选：床次 9095 + 扎号 1 → 候选渲染 → 选中 → 徽标
      await s.step('P5 真实查询：候选列表渲染（1 行）', async () => {
        const inputs = await elements(page, '.lookup-input');
        await withTimeout(inputs[0].input(BED), 8000, 'input 床次');
        await withTimeout(inputs[1].input('1'), 8000, 'input 扎号');
        await elementTap(page, '.lookup-search', '查询按钮');
        await waitPageData(page, (d) => (d.lookupRows || []).length === 1,
          { label: '候选列表' });
      }, true);
      await s.step('D1 候选行字段（款号/床次/色码/扎号/数量）', async () => {
        const rows = await elements(page, '.lookup-row');
        if (rows.length < 1) throw new Error('无候选行');
        const txt = await elementText(rows[0]);
        const need = ['UISCAN-S1', BED, '黑色', '1', String(TOTAL_QTY)];
        const miss = need.filter((k) => txt.indexOf(k) < 0);
        if (miss.length) throw new Error('缺字段: ' + miss.join(',') + ' | 行文本: ' + txt.slice(0, 80));
      }, true);
      await s.step('P6 选中候选 → 进入工序选择态（等价扫码成功）', async () => {
        const rows = await elements(page, '.lookup-row');
        await withTimeout(rows[0].tap(), 8000, 'tap 候选行');
        await waitPageData(page, (d) => d.state === 'process' && d.ticket &&
          String(d.ticket.bedNo) === BED, { label: '进入工序态', timeout: 20000 });
        const d = await pageData(page);
        if (d.lookupShow !== false) throw new Error('选中后弹层未关闭');
      }, true);
      await s.step('P7 工序 chip 渲染（2 工序，非单选态）', async () => {
        const chips = await elements(page, '.proc-chip');
        if (chips.length < 2) throw new Error('chips=' + chips.length);
      }, true);

      // ③层：满扎徽标 UI 渲染 = API selector 值
      await s.step('D2 满扎徽标 45/50 渲染且与 API selector 一致', async () => {
        const badges = await elements(page, '.proc-cap');
        if (!badges.length) throw new Error('徽标未渲染');
        let hit = '';
        for (const b of badges) {
          const txt = await elementText(b);
          if (txt === `${REGISTERED}/${TOTAL_QTY}`) { hit = txt; break; }
        }
        if (!hit) {
          const all = [];
          for (const b of badges) all.push(await elementText(b));
          throw new Error('徽标列表=' + JSON.stringify(all));
        }
        const d = await pageData(page);
        const chip = (d.processes || []).find((p) => p.id === wholeProcessId) || {};
        if (chip.registeredQty !== REGISTERED || chip.capQty !== TOTAL_QTY) {
          throw new Error(`VM reg=${chip.registeredQty} cap=${chip.capQty}`);
        }
      }, true);

      // 复位 + mock 还原（离场干净，不影响后续套件）
      await s.step('P8 复位扫菲页状态', async () => {
        await withTimeout(page.setData({
          state: 'idle', ticket: null, processes: [], selectedId: 0,
          selectedName: '', qtyValue: ''
        }), 8000, 'setData 复位');
        await withTimeout(mp.restoreWxMethod('scanCode'), 8000, 'restore scanCode');
      });
    } else {
      // 任务书降级预案：scan 页不可达 → UI 断言整段 SKIP，API 侧已完成（B2/B3）
      s.skip('P1~D2 扫菲页 UI 断言', '扫菲页 reLaunch 超时（UI 断言降级，API 侧断言已给出）');
    }
  } else {
    s.skip('P1~D2 扫菲页 UI 断言', '无可用会话');
  }

  } finally {
    // ---- 清理（撤登记 → 删床次 → 删订单）：任何异常路径都执行 ----
    try {
      const cleaned = await cleanupByPrefix(api, 'UISCAN-');
      s.check('B4 清理：UISCAN- 前缀数据已清', cleaned >= 1 || !fx, `删除订单数=${cleaned}`);
    } catch (e) {
      s.check('B4 清理：UISCAN- 前缀数据已清', false, String((e && e.message) || e).slice(0, 120));
    }

    if (mp) {
      try { await withTimeout(mp.disconnect(), 8000, 'disconnect'); } catch (e) { /* ignore */ }
    }
    s.summary();
  }
})();
