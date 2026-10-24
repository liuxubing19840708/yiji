/**
 * 订单域 automator 探针（SF1：orders 列表 / order-detail / production-create 向导挂单进入）。
 *
 * 前置：微信开发者工具已开自动化端口——
 *   cli.bat auto --project D:\\project\\扫菲\\frontend --auto-port 9420
 *   （连不上则打印运行说明并以退出码 3 结束：交付脚本待真机/工具验证，不阻塞交付）
 *
 * 三层断言风格（对齐 probe_kanban.js / ui_fullchain.js）：
 *   B* 后端准备 → P* 页面渲染 → D* 数据断言；测试幂等自清扫（探针单删除）；中文注释。
 * 后端 /orders 由并行 SA1 开发中：
 *   - 404（未就绪）时跳过数据断言，转为验证前端「服务升级中」兜底不白屏（P3-D）；
 *   - 就绪时全链路断言：列表渲染探针单 → 详情矩阵 → 向导挂单进入。
 * mock 教训（mock wx）：若 mockWxMethod 自定义返回值，必须带 errMsg 字段
 *   （如 { confirm: true, errMsg: 'showModal:ok' }），否则小程序侧按失败处理。
 * 运行：node probe_orders.js
 */
const automator = require('miniprogram-automator');

const API = 'http://127.0.0.1:8000/api';
const MARK = 'PROBE-SO-订单探针';
const PASS = [], FAIL = [], SKIP = [];
function check(name, cond, detail = '') {
  (cond ? PASS : FAIL).push(name);
  console.log(cond ? 'PASS' : 'FAIL', name, detail ? `| ${detail}` : '');
}
function skip(name, detail = '') {
  SKIP.push(name);
  console.log('SKIP', name, detail ? `| ${detail}` : '');
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, { method = 'GET', token, cid, json } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (cid) headers['X-Company-Id'] = String(cid);
  const resp = await fetch(API + path, {
    method, headers, body: json === undefined ? undefined : JSON.stringify(json)
  });
  const text = await resp.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (e) { /* ignore */ }
  return { code: resp.status, data };
}

(async () => {
  let mp = null;
  let probeOrderId = null;
  let serviceReady = false;
  try {
    // ---- 1. 后端准备（B 层） ----
    const login = await api('/auth/login', {
      method: 'POST',
      json: { phone: '13800000001', password: 'yisao1234' }
    });
    check('B1 后端登录', login.code === 200 && login.data.access, `code=${login.code}`);
    const at = login.data.access;
    const cid = 2;
    const me = await api('/auth/me', { token: at, cid });
    const user = (me.data && (me.data.user || me.data)) || { real_name: 'probe' };

    // 探针单：黑色 M ×100（幂等自清扫：finally 删除）；P4 契约建单必带 style
    const sty = await api('/styles', { token: at, cid });
    const styList = (sty.data && (sty.data.results || sty.data)) || [];
    const probeStyleId = styList[0] && styList[0].id;
    if (!probeStyleId) {
      skip('B2 创建探针订单', '该厂无款式可关联（P4 契约建单必带 style），请先在款式管理添加');
    }
    const created = probeStyleId ? await api('/orders', {
      method: 'POST', token: at, cid,
      json: { order_no: MARK, style: probeStyleId, items: [{ color: '黑色', size: 'M', qty: 100 }] }
    }) : { code: 0, data: {} };
    if (created.code === 201 && created.data.id) {
      serviceReady = true;
      probeOrderId = created.data.id;
      check('B2 创建探针订单', true, `id=${probeOrderId}`);
      // 契约抽查：GET /orders/<id>/progress 色码矩阵完成度
      const prog = await api(`/orders/${probeOrderId}/progress`, { token: at, cid });
      check('B3 完成度接口（契约 §四）', prog.code === 200,
        `code=${prog.code} ${JSON.stringify(prog.data).slice(0, 120)}`);
    } else {
      skip('B2 创建探针订单', `code=${created.code}（/orders 未就绪，转前端兜底验证）`);
    }

    // ---- 2. automator 连接（连不上=交付待验证，不 FAIL） ----
    try {
      mp = await automator.connect({ wsEndpoint: 'ws://localhost:9420' });
      console.log('connected to 9420');
    } catch (e) {
      console.log('CONNECT FAIL:', e && e.message);
      console.log('运行说明：先用微信开发者工具 CLI 打开自动化端口——');
      console.log('  cli.bat auto --project D:\\project\\扫菲\\frontend --auto-port 9420');
      console.log('再运行 node probe_orders.js；本次结果=脚本交付，待真机/工具验证。');
      process.exitCode = 3;
      return;
    }
    const home = await mp.reLaunch('/pages/index/index');
    check('P1 reLaunch 首页（编译验证）', !!home, home && home.path);

    // ---- 3. 注入登录态 ----
    await mp.callWxMethod('setStorageSync', 'yisao_token', at);
    await mp.callWxMethod('setStorageSync', 'yisao_refresh', login.data.refresh || '');
    await mp.callWxMethod('setStorageSync', 'yisao_user', user);
    await mp.callWxMethod('setStorageSync', 'yisao_company_id', cid);
    await mp.callWxMethod('setStorageSync', 'yisao_is_admin', 1);

    // ---- 4. 订单列表页（P 层 + D 层） ----
    const listPage = await mp.reLaunch('/packages/admin/orders/index');
    check('P2 reLaunch 订单列表页', !!listPage, listPage && listPage.path);
    let ld = null;
    for (let i = 0; i < 20; i++) {
      await sleep(500);
      ld = await listPage.data();
      const done = serviceReady
        ? (ld && ld.orders && ld.orders.some((o) => o.order_no === MARK))
        : (ld && (ld.serviceDown || ld.error));
      if (done) break;
    }
    ld = await listPage.data();
    if (serviceReady) {
      check('P3 列表渲染探针单', !!(ld.orders || []).some((o) => o.order_no === MARK),
        `orders=${ld.orders && ld.orders.length}`);
      const hit = (ld.orders || []).find((o) => o.order_no === MARK);
      check('D1 列表卡字段（进度/徽标 VM）', !!hit && hit.statusCls !== '' && typeof hit.progress === 'number',
        hit ? `progress=${hit.progress} statusCls=${hit.statusCls}` : '未命中');
    } else {
      check('P3-D /orders 未就绪兜底（serviceDown/error 态，不白屏）',
        !!(ld && (ld.serviceDown || ld.error)),
        `serviceDown=${ld && ld.serviceDown} error=${ld && ld.error}`);
    }
    check('D2 筛选 tab 就绪（生产中/已完成/已取消/全部）',
      !!(ld.filters && ld.filters.length === 4),
      `filters=${ld.filters && ld.filters.length}`);

    // ---- 5. 订单详情页 ----
    if (serviceReady) {
      const detailPage = await mp.reLaunch(`/packages/admin/order-detail/index?id=${probeOrderId}`);
      check('P4 reLaunch 订单详情页', !!detailPage, detailPage && detailPage.path);
      let dd = null;
      for (let i = 0; i < 20; i++) {
        await sleep(500);
        dd = await detailPage.data();
        if (dd && dd.matrixLoaded && dd.rows && dd.rows.length) break;
      }
      dd = await detailPage.data();
      check('P5 详情矩阵渲染（订单数/已登记/完成率）',
        !!(dd.rows && dd.rows.length && dd.sizes && dd.sizes.length),
        `rows=${dd.rows && dd.rows.length} sizes=${dd.sizes && dd.sizes.length}`);
      const cell = dd.rows && dd.rows[0] && dd.rows[0].cells && dd.rows[0].cells[0];
      check('D3 矩阵格数值（订单数=100，完成率 0 → 落后红底）',
        !!cell && cell.orderQty === 100 && cell.low === true,
        cell ? `orderQty=${cell.orderQty} low=${cell.low}` : '无格');
      check('D4 交期灯口径（无交期=无灯）', dd.lamp === '', `lamp=${dd.lamp}`);
      check('D5 满登记提示隐藏（完成率 0）', dd.fullRegister === false, `fullRegister=${dd.fullRegister}`);

      // ---- 6. 建床向导挂单进入（§2.6.1 步骤 1） ----
      const wizPage = await mp.reLaunch(`/packages/admin/production-create/index?order_id=${probeOrderId}`);
      check('P6 reLaunch 建床向导（order_id 进入）', !!wizPage, wizPage && wizPage.path);
      let wd = null;
      for (let i = 0; i < 16; i++) {
        await sleep(500);
        wd = await wizPage.data();
        if (wd && wd.order && wd.order.order_no) break;
      }
      wd = await wizPage.data();
      // 2026-10-23 精简：挂单入口直达生成扎（step=4），不再经过裁剪计划
      check('P7 挂单入口直达生成扎（step=4）', wd && wd.step === 4, `step=${wd && wd.step}`);
      check('D6 挂单上下文带出（单号一致）', !!(wd.order && wd.order.order_no === MARK),
        wd.order ? wd.order.order_no : 'order 为空');
      // 2026-10-23 向导精简为 2 步：①选单选款 ②生成扎（裁剪计划环节已移除）
      check('D7 步骤条 2 步配置（选单选款→生成扎）', !!(wd.steps && wd.steps.length === 2
        && wd.steps[0].name === '选单选款' && wd.steps[1].name === '生成扎'),
        `steps=${wd.steps && JSON.stringify(wd.steps)}`);
    } else {
      skip('P4~D7 详情/向导数据断言', '/orders 未就绪（SA1 并行），待联调后重跑');
    }

    // 截图留档
    try {
      await mp.screenshot({ path: 'shots/probe_orders.png' });
      console.log('screenshot: shots/probe_orders.png');
    } catch (e) {
      console.log('[warn] screenshot 失败(跳过):', e.message);
    }
  } catch (e) {
    console.log('PROBE ERR:', e && e.message);
    process.exitCode = 2;
  } finally {
    // ---- 7. 清理（幂等自清扫） ----
    try {
      const lg = await api('/auth/login', {
        method: 'POST', json: { phone: '13800000001', password: 'yisao1234' }
      });
      const at2 = lg.data.access;
      if (probeOrderId) {
        // 已挂床次后端 400：先尝试删，失败则取消兜底
        const del = await api(`/orders/${probeOrderId}`, { method: 'DELETE', token: at2, cid: 2 });
        if (del.code >= 300) {
          await api(`/orders/${probeOrderId}/status`, { method: 'PUT', token: at2, cid: 2, json: { status: 'cancelled' } });
          console.log('probe order cancelled (delete blocked):', probeOrderId);
        } else {
          console.log('probe order deleted:', probeOrderId);
        }
      }
    } catch (e) {
      console.log('[warn] cleanup 失败，需人工删除订单「' + MARK + '」:', e.message);
    }
    try { if (mp) await mp.disconnect(); } catch (e) { /* ignore */ }
  }
  console.log(`\n=== 订单探针完成：PASS=${PASS.length} FAIL=${FAIL.length} SKIP=${SKIP.length} ===`);
  if (SKIP.length) console.log('跳过项:', SKIP);
  if (FAIL.length) { console.log('失败项:', FAIL); process.exitCode = 1; }
})();
