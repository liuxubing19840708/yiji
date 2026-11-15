// -*- coding: utf-8 -*-
/**
 * ui_parity_new.js —— 真实 automator + 一次性隔离后端 E2E。
 *
 * 必须由 backend/run_ui_gate.py 启动：公司、账号、API 地址均取 YISAO_E2E_*；
 * helpers_ui.js 会拒绝非本机 API、缺失公司上下文及真实厂 86。
 *
 * API 仅构造本运行专属 UIPARITY-<时间戳>- 前缀的订单/款式/工序/路线/菲票，
 * 接收人使用门禁新建隔离公司的管理员。UI 实际执行：
 *   1. 产前准备：填写工艺要求 ->「存为模板」-> API 与页面双向回查；
 *   2. 交接中心：指定交接单「全部接收」-> API 回查 -> 详情页回显已接收。
 * 附加：真实服务端以同一 Idempotency-Key 重发扫菲登记，断言不重复加数。
 *
 * 分包导航、automator 或关键控件不可达都直接进入 catch，记 FAIL/未验收；
 * 本套件不使用 Suite.step/skip，绝不把环境阻断写成 SKIP=0 的假通过。
 */
const {
  Suite, openSession, injectLogin, pageData, waitPageData,
  elementTap, elementText, withTimeout, sleep,
  ApiClient, API_BASE, COMPANY_ID, buildBedFixture
} = require('./helpers_ui');

const RUN_ID = Date.now().toString(36).toUpperCase();
const MARK = `UIPARITY-${RUN_ID}-`;
const TEMPLATE_NAME = `${MARK}工艺模板`;
const REQUIREMENTS = `${MARK}针距与领口工艺要求`;
const QTY = 12;

function must(res, expected, label) {
  if (res.code !== expected) {
    throw new Error(`${label}：HTTP ${res.code} ${JSON.stringify(res.data).slice(0, 240)}`);
  }
  return res.data;
}

async function waitApi(fn, label, timeout = 18000) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    last = await fn();
    if (last) return last;
    await sleep(500);
  }
  throw new Error(`API 回查超时：${label}；最后结果=${JSON.stringify(last).slice(0, 160)}`);
}

async function offlineReplay(api, ticketId, processId) {
  const key = `${MARK}offline-retry`;
  const payload = { ticket_id: ticketId, process_id: processId, qty: 2 };
  const send = async () => {
    const response = await fetch(`${API_BASE}/records`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${api.token}`,
        'X-Company-Id': String(COMPANY_ID),
        'Idempotency-Key': key
      },
      body: JSON.stringify(payload)
    });
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { raw: text.slice(0, 160) }; }
    return { code: response.status, data };
  };
  const first = await send();
  const replay = await send();
  if (first.code !== 201 || replay.code !== 201) {
    throw new Error(`扫菲幂等请求失败：first=${first.code} replay=${replay.code}`);
  }
  return { first, replay };
}

(async () => {
  const suite = new Suite('ui_parity_new 产前准备/交接中心真实 UI 验收');
  const api = new ApiClient();
  let mp = null;
  let phase = '隔离后端登录';

  try {
    await api.login();
    suite.check('B0 门禁隔离公司登录与名称安全校验', true, api.companyName);

    // ---- 专属 API 夹具：不读取或复用任何固定公司、款式、工序或菲票 ID。 ----
    phase = '创建专属订单/款式/工序/菲票夹具';
    const me = await api.get('/auth/me');
    const receiverId = Number(me.id || (me.user && me.user.id));
    if (!receiverId) throw new Error('无法确定隔离接收人用户 ID');

    const style = must(await api.post('/styles', {
      style_no: `${MARK}STYLE`, name: `${MARK}隔离测试款`
    }), 201, '创建款式');
    const process = must(await api.post('/processes', {
      name: `${MARK}整件`, default_price: '0.050', is_fixed: true, sort: 90
    }), 201, '创建工序');
    const bom = must(await api.post('/tech/boms', {
      style: style.id, remark: `${MARK}隔离开床BOM`, items: []
    }), 201, '创建开床 BOM');
    must(await api.post(`/tech/boms/${bom.id}/activate`, {}), 200, '激活开床 BOM');
    const fixture = await buildBedFixture(api, {
      orderNo: `${MARK}ORDER`, bedNo: `9${RUN_ID}`.slice(0, 28), totalQty: QTY,
      registerQty: 0, styleId: style.id, processId: process.id
    });
    const priced = must(await api.getFull(
      `/productions/${fixture.prodId}/order-processes`), 200, '回查床次工价');
    const priceRows = Array.isArray(priced) ? priced : (priced.results || []);
    if (!priceRows.some((row) => row.process === process.id && Number(row.price) === 0.05)) {
      throw new Error('床次专属工序工价未落库');
    }
    suite.check('B1 专属订单/款式/工序/床次/菲票/接收人夹具就绪', true,
      `order=${fixture.orderId} ticket=${fixture.ticketId} receiver=${receiverId}`);

    phase = '创建专属生产路线并准备待接收交接';
    const cutting = must(await api.post('/manufacturing/stages', {
      code: `${MARK}CUT`.slice(0, 32), name: `${MARK}裁剪`, stage_type: 'cutting'
    }), 201, '创建裁剪阶段');
    const sewing = must(await api.post('/manufacturing/stages', {
      code: `${MARK}SEW`.slice(0, 32), name: `${MARK}车缝`, stage_type: 'sewing'
    }), 201, '创建车缝阶段');
    const template = must(await api.post('/manufacturing/routes', {
      code: `${MARK}ROUTE-T`, name: `${MARK}路线模板`,
      nodes: [
        { stage_id: cutting.id, sequence: 1, required: true },
        { stage_id: sewing.id, sequence: 2, required: true }
      ]
    }), 201, '创建路线模板');
    const route = must(await api.post(`/manufacturing/routes/${template.id}/instantiate`, {
      production_id: fixture.prodId, code: `${MARK}ROUTE`, name: `${MARK}订单路线`
    }), 201, '实例化路线');
    const sourceNode = route.nodes.find((node) => node.stage === cutting.id);
    const targetNode = route.nodes.find((node) => node.stage === sewing.id);
    if (!sourceNode || !targetNode) throw new Error('路线实例缺少裁剪/车缝节点');
    for (const action of ['receive', 'complete']) {
      must(await api.post(`/manufacturing/actions/${action}`, {
        ticket_id: fixture.ticketId, node_id: sourceNode.id, quantity: QTY,
        request_id: `${MARK}${action}`.slice(0, 60)
      }), 201, `裁剪节点 ${action}`);
    }
    const created = must(await api.post('/handovers/', {
      kind: 'cutting', request_id: `${MARK}handover`.slice(0, 64),
      source_node: sourceNode.id, target_node: targetNode.id,
      remark: `${MARK}真实 UI 全收夹具`,
      targets: [{
        receiver: receiverId, target_group_no: 1,
        items: [{ ticket: fixture.ticketId, quantity: QTY }]
      }]
    }), 201, '创建待接收交接');
    const handover = created.items && created.items[0];
    if (!handover || handover.status !== 'pending' || handover.receiver !== receiverId) {
      throw new Error('待接收交接或指定接收人不正确');
    }
    suite.check('B2 专属路线与指定接收人的待接收交接就绪', true,
      `route=${route.id} handover=${handover.id}`);

    // ---- automator：任何失败都是未验收，不做 SKIP 降级。 ----
    phase = 'automator 连接与登录态注入';
    mp = await openSession(console.log);
    await injectLogin(mp);

    phase = '打开产前准备分包页';
    let page = await withTimeout(mp.reLaunch(
      `/packages/admin/preproduction/index?id=${fixture.orderId}`),
    25000, 'reLaunch 产前准备');
    await waitPageData(page, (d) => d.loading === false && !d.error && d.orderNo,
      { timeout: 22000, label: '产前准备加载' });
    let data = await pageData(page);
    if (data.orderNo !== `${MARK}ORDER` || data.canManage !== true) {
      throw new Error(`产前页订单/权限不符：orderNo=${data.orderNo} canManage=${data.canManage}`);
    }
    const heading = await withTimeout(page.$('.summary .h1'), 10000, '产前标题');
    if (!heading || (await elementText(heading)) !== '产前准备') {
      throw new Error('产前准备页面标题未渲染');
    }
    suite.check('U1 实际打开产前准备并显示本运行订单', true, data.orderNo);

    phase = '产前准备 UI 保存工艺模板';
    const requirements = await withTimeout(
      page.$('textarea[data-field="requirements"]'), 10000, '工艺要求输入框');
    if (!requirements) throw new Error('工艺要求输入框不存在');
    await withTimeout(requirements.input(REQUIREMENTS), 10000, '输入工艺要求');
    await waitPageData(page, (d) => d.requirements === REQUIREMENTS,
      { label: '工艺要求输入回显' });
    await withTimeout(mp.mockWxMethod('showModal', {
      confirm: true, content: TEMPLATE_NAME, errMsg: 'showModal:ok'
    }), 10000, 'mock 模板名称确认框');
    try {
      await elementTap(page, 'button[data-kind="requirements"]', '存为工艺模板');
    } finally {
      await withTimeout(mp.restoreWxMethod('showModal'), 10000, 'restore showModal');
    }
    await waitPageData(page, (d) => (d.requirementsTemplates || []).some(
      (item) => item.name === TEMPLATE_NAME && item.content === REQUIREMENTS),
    { timeout: 22000, label: '模板保存后的页面列表' });
    const templates = await waitApi(async () => {
      const rows = await api.get('/preproduction/templates?kind=requirements');
      return Array.isArray(rows) && rows.some((item) => item.name === TEMPLATE_NAME &&
        item.content === REQUIREMENTS && item.is_system === false) ? rows : null;
    }, '工艺模板落库');
    const savedTemplate = templates.find((item) => item.name === TEMPLATE_NAME);
    const picker = await withTimeout(
      page.$('picker[data-kind="requirements"] .template-picker'), 10000, '工艺模板选择器');
    if (!picker || (await elementText(picker)).indexOf('选择标准或公司模板') < 0) {
      throw new Error('保存后的工艺模板选择器未回显');
    }
    suite.check('U2 UI 存为模板且 API 与页面列表一致', true, `template=${savedTemplate.id}`);

    phase = '打开交接中心分包页';
    page = await withTimeout(mp.reLaunch('/packages/admin/handovers/index/index'),
      25000, 'reLaunch 交接中心');
    await waitPageData(page, (d) => d.loading === false && !d.error && !d.denied &&
      (d.rows || []).some((row) => row.id === handover.id),
    { timeout: 22000, label: '交接中心待我接收列表' });
    data = await pageData(page);
    const incoming = data.rows.find((row) => row.id === handover.id);
    if (!incoming.canAccept || incoming.total_quantity !== QTY || !incoming.receiver_name) {
      throw new Error(`交接行不可接收或数量/接收人不符：${JSON.stringify(incoming).slice(0, 200)}`);
    }
    const handoverNo = await withTimeout(page.$(
      `.handover-card[data-id="${handover.id}"] .handover-no`), 10000, '目标交接单号');
    if (!handoverNo || (await elementText(handoverNo)) !== handover.handover_no) {
      throw new Error('交接中心未渲染目标交接单号');
    }
    suite.check('U3 实际打开交接中心并显示指定接收人的待接收单', true, handover.handover_no);

    phase = '交接中心 UI 全部接收';
    await withTimeout(mp.mockWxMethod('showModal', {
      confirm: true, content: '', errMsg: 'showModal:ok'
    }), 10000, 'mock 全部接收确认框');
    try {
      await elementTap(page,
        `.handover-card[data-id="${handover.id}"] button[data-id="${handover.id}"]`,
        '目标交接单全部接收');
    } finally {
      await withTimeout(mp.restoreWxMethod('showModal'), 10000, 'restore showModal');
    }
    const received = await waitApi(async () => {
      const row = await api.get(`/handovers/${handover.id}/`);
      return row.status === 'received' && row.received_quantity === QTY ? row : null;
    }, 'UI 全部接收落库');
    if (received.received_by !== receiverId || received.has_discrepancy ||
      received.items[0].received_quantity !== QTY) {
      throw new Error(`交接 API 回查字段不符：${JSON.stringify(received).slice(0, 240)}`);
    }
    suite.check('B3 UI 全部接收后 API 状态/数量/实际接收人正确', true,
      `status=${received.status} qty=${received.received_quantity}`);

    phase = '交接详情 UI 回查已接收';
    page = await withTimeout(mp.reLaunch(
      `/packages/admin/handovers/detail/index?id=${handover.id}`),
    25000, 'reLaunch 交接详情');
    await waitPageData(page, (d) => d.loading === false && !d.error && d.detail &&
      d.detail.status === 'received' && d.detail.received_quantity === QTY,
    { timeout: 22000, label: '交接详情已接收回显' });
    data = await pageData(page);
    if (data.detail.statusText !== '已接收' || data.detail.has_discrepancy) {
      throw new Error('交接详情状态文本或差异标记不正确');
    }
    const detailNo = await withTimeout(page.$('.handover-no'), 10000, '详情交接单号');
    if (!detailNo || (await elementText(detailNo)) !== handover.handover_no) {
      throw new Error('交接详情单号回显不符');
    }
    suite.check('U4 交接详情 UI 回显已接收及全收数量', true,
      `${data.detail.statusText} ${data.detail.received_quantity}/${data.detail.total_quantity}`);

    phase = '真实服务端扫菲离线幂等重发';
    const replayed = await offlineReplay(api, fixture.ticketId, process.id);
    const records = await api.getFull(`/records?ticket_id=${fixture.ticketId}&page_size=20`);
    const rows = (records.data && records.data.results) || [];
    const record = rows.find((row) => row.process === process.id);
    suite.check('B4 扫菲同键重发返回首次快照且服务端只登记 2 件',
      replayed.first.code === 201 && replayed.replay.code === 201 &&
      JSON.stringify(replayed.first.data) === JSON.stringify(replayed.replay.data) &&
      rows.length === 1 && record && record.qty === 2,
      `first=${replayed.first.code} replay=${replayed.replay.code} rows=${rows.length} qty=${record && record.qty}`);
  } catch (error) {
    const message = String((error && error.message) || error).slice(0, 360);
    suite.check(`未验收：${phase}`, false, message);
  } finally {
    if (mp) {
      try { await withTimeout(mp.disconnect(), 8000, 'disconnect'); } catch (e) { /* 保留原失败 */ }
    }
    // run_ui_gate.py 最终删除整个一次性 SQLite；不做跨前缀或共享数据清理。
    suite.summary();
  }
})().catch((error) => {
  console.log('FAIL 未验收：脚本顶层异常 |', String((error && error.message) || error));
  console.log('=== ui_parity_new 完成：PASS=0 FAIL=1 SKIP=0 ===');
  process.exitCode = 1;
});
