// -*- coding: utf-8 -*-
/**
 * probe_p4.js —— F1 前端探针（订单建单改版 + 工序模板，2026-10-21）。
 *
 * 断言目标（对应 F1 交付）：
 *   1) 建单页：单号输入框已移除（自动生成说明行存在，无「留空自动生成」输入框）；
 *   2) 建单页：数量矩阵行头/列头复制（点行头铺满整行 / 点列头铺满整列 / 空行提示不崩）；
 *   3) 建单页：工序模板选择填充（GET /process-templates 就绪时，选模板 → procRows 填充）。
 *
 * 前置：微信开发者工具自动化端口 9420（cli.bat auto --project D:\project\扫菲\frontend --auto-port 9420）。
 * 连不上/会话僵死：复用 helpers_ui.openSession（连接→探针→cli quit→cli auto 重启自愈，最多 2 轮）；
 * 仍失败 → 打印运行说明并以退出码 3 结束（脚本交付待验证，不算 FAIL）。
 * 后端 /process-templates 由并行 B1 开发中：404 时模板段记 SKIP（前端兜底已在页内）。
 * 运行：cd D:\project\扫菲\uitest && node probe_p4.js
 */
const {
  Suite, sleep, openSession, pageData, waitPageData, injectLogin,
  ApiClient, withTimeout, elementTap
} = require('./helpers_ui.js');

const MARK = 'PROBE-P4-建单探针';
const suite = new Suite('F1 建单页探针');

(async () => {
  let mp = null;

  // ---- B 层：后端准备 ----
  let api = null;
  let tplReady = false;
  let tpl = null;
  try {
    api = new ApiClient();
    await api.login();
    suite.check('B1 后端登录', !!api.token, 'company=' + api.cid);
    const res = await api.getFull('/process-templates');
    const list = Array.isArray(res.data) ? res.data : ((res.data && res.data.results) || []);
    if (res.code === 200 && list.length) {
      tplReady = true;
      tpl = list[0];
      suite.check('B2 工序模板接口就绪', true, `首个模板=${tpl.name} ${tpl.items.length} 道工序`);
    } else if (res.code === 200) {
      suite.skip('B2 工序模板接口就绪', '200 但无模板数据（可先在「工序」页建模板）');
    } else {
      suite.skip('B2 工序模板接口就绪', `code=${res.code}（/process-templates 未就绪，B1 并行中，前端段降级验证）`);
    }
  } catch (e) {
    suite.skip('B1 后端准备', '异常: ' + String((e && e.message) || e).slice(0, 120));
  }

  // ---- 会话（9420 桥可能僵死：openSession 内置重启自愈） ----
  try {
    mp = await openSession(console.log);
  } catch (e) {
    console.log('CONNECT FAIL:', (e && e.message) || e);
    console.log('运行说明：cli.bat auto --project D:\\project\\扫菲\\frontend --auto-port 9420');
    console.log('再运行 node probe_p4.js；本次结果=脚本交付，待真机/工具验证。');
    process.exitCode = 3;
    suite.summary();
    return;
  }

  try {
    // ---- 注入登录态（管理员 + 公司 86） ----
    await suite.step('P1 注入登录态', () => injectLogin(mp));

    // ---- 建单页（新建表单） ----
    const page = await suite.step('P2 reLaunch 订单页', () =>
      withTimeout(mp.reLaunch('/packages/admin/orders/index'), 20000, 'reLaunch orders'));
    await suite.step('P3 进入新建表单', async () => {
      await waitPageData(page, (d) => d && d.stage === 'list' && (d.orders || []).length >= 0, { label: '列表就绪', timeout: 15000 });
      await withTimeout(page.callMethod('onAddOrder'), 10000, 'callMethod onAddOrder');
      await waitPageData(page, (d) => d && d.stage === 'edit', { label: '编辑态', timeout: 10000 });
    }, true);

    // ---- D1：单号无输入框（自动生成说明行存在 + 旧「留空自动生成」输入不存在） ----
    await suite.step('D1a 自动生成说明行存在', async () => {
      const el = await withTimeout(page.$('.auto-no-tip'), 10000, '$ .auto-no-tip');
      if (!el) throw new Error('.auto-no-tip 不存在');
    }, true);
    await suite.step('D1b 旧单号输入框已移除', async () => {
      // 旧输入框特征：placeholder=留空自动生成（SO0001…）
      const inputs = await withTimeout(page.$$('input'), 10000, '$$ input');
      for (const inp of (inputs || [])) {
        let ph = '';
        try { ph = String((await inp.attribute('placeholder')) || ''); } catch (e) { /* attribute 不可读则跳过 */ }
        if (ph.indexOf('留空自动生成') >= 0) throw new Error('仍存在留空自动生成输入框');
      }
    }, true);
    await suite.step('D1c 契约新字段已进 data（styleId/images/processRows）', async () => {
      const d = await pageData(page);
      if (typeof d.styleId === 'undefined') throw new Error('缺 styleId');
      if (!Array.isArray(d.images)) throw new Error('缺 images');
      if (!Array.isArray(d.procRows)) throw new Error('缺 procRows');
      if (d.styleId !== 0) throw new Error('新建态 styleId 应为 0');
    }, true);

    // ---- D2：行头/列头复制（数据层驱动：setData + callMethod） ----
    await suite.step('D2a 点行头：首个已填数量铺满整行', async () => {
      await withTimeout(page.setData({ colors: ['黑色', '白色'], sizes: ['M', 'L'], cells: { '黑色|M': 5 } }), 8000, 'setData');
      await withTimeout(page.callMethod('rebuildMatrix'), 8000, 'rebuildMatrix');
      await withTimeout(page.callMethod('onTapRowHead', { currentTarget: { dataset: { row: 0 } } }), 8000, 'onTapRowHead');
      const d = await pageData(page);
      const rowOK = d.cells['黑色|M'] === 5 && d.cells['黑色|L'] === 5;
      const otherOK = !d.cells['白色|M'] && !d.cells['白色|L'];
      if (!rowOK) throw new Error('行内未铺满: ' + JSON.stringify(d.cells));
      if (!otherOK) throw new Error('串行污染: ' + JSON.stringify(d.cells));
    }, true);
    await suite.step('D2b 点列头：首个已填数量铺满整列', async () => {
      await withTimeout(page.setData({ cells: { '黑色|M': 3 } }), 8000, 'setData');
      await withTimeout(page.callMethod('rebuildMatrix'), 8000, 'rebuildMatrix');
      await withTimeout(page.callMethod('onTapColHead', { currentTarget: { dataset: { col: 0 } } }), 8000, 'onTapColHead');
      const d = await pageData(page);
      const colOK = d.cells['黑色|M'] === 3 && d.cells['白色|M'] === 3;
      const otherOK = !d.cells['黑色|L'] && !d.cells['白色|L'];
      if (!colOK) throw new Error('列内未铺满: ' + JSON.stringify(d.cells));
      if (!otherOK) throw new Error('串列污染: ' + JSON.stringify(d.cells));
    }, true);
    await suite.step('D2c 空行点行头：toast 提示分支（格值不变不崩）', async () => {
      await withTimeout(page.setData({ cells: {} }), 8000, 'setData');
      await withTimeout(page.callMethod('rebuildMatrix'), 8000, 'rebuildMatrix');
      await withTimeout(page.callMethod('onTapRowHead', { currentTarget: { dataset: { row: 0 } } }), 8000, 'onTapRowHead');
      const d = await pageData(page);
      if (Object.keys(d.cells).length) throw new Error('空行不应铺值: ' + JSON.stringify(d.cells));
    }, true);

    // ---- D3：模板选择填充（B1 就绪才断言；未就绪记 SKIP） ----
    if (tplReady && tpl) {
      await suite.step('D3 模板选择填充（选模板 → procRows=模板工序数）', async () => {
        await withTimeout(page.callMethod('onOpenTplSheet'), 10000, 'onOpenTplSheet');
        await waitPageData(page, (d) => (d.templates || []).length > 0 || d.tplDown, { label: '模板列表', timeout: 15000 });
        const d0 = await pageData(page);
        if (d0.tplDown) throw new Error('页面侧模板接口报「服务升级中」');
        await withTimeout(page.callMethod('onPickTemplate', { currentTarget: { dataset: { index: 0 } } }), 10000, 'onPickTemplate');
        const d = await pageData(page);
        const expect = (tpl.items || []).length;
        if (!d.procRows || d.procRows.length !== expect) {
          throw new Error(`procRows=${d.procRows && d.procRows.length} 期望=${expect}`);
        }
        if (d.tplSheetVisible) throw new Error('选模板后弹层未关闭');
      }, true);
    } else {
      suite.skip('D3 模板选择填充', '/process-templates 未就绪（B1 并行中），待联调重跑');
    }

    // ---- 截图留档 ----
    try {
      await mp.screenshot({ path: 'shots/probe_p4.png' });
      console.log('screenshot: shots/probe_p4.png');
    } catch (e) {
      console.log('[warn] screenshot 失败(跳过):', (e && e.message) || e);
    }
  } catch (e) {
    console.log('PROBE ERR:', (e && e.message) || e);
    process.exitCode = 2;
  } finally {
    try { if (mp) await withTimeout(mp.disconnect(), 8000, 'disconnect'); } catch (e) { /* ignore */ }
  }
  suite.summary();
})();
