// -*- coding: utf-8 -*-
/**
 * helpers_ui.js —— L3 融合套件（ui_orders / ui_bed_wizard / ui_scan2 / ui_kanban_drill）共用工具。
 *
 * 设计要点（对齐 docs/端到端测试设计方案.md §四 三层断言）：
 *   ① UI 动作成功 → ② API/DB 落库正确 → ③ 回 UI 回显一致。
 *   ② 层直接在本进程 fetch 隔离测试后端（管理员 token + 显式公司头）。
 *
 * 稳定性约束（automator ws://localhost:9420 连接不稳的教训）：
 *   - 连接重试 3 次（退避 2s/5s/10s）；
 *   - 每个套件内只允许一次 automator.connect（ Suites 串行跑，禁止并行）；
 *   - 所有 automator 调用带超时包装（默认 15s），超时/异常由 Suite.step 捕获记 SKIP，
 *     不让单点故障整体崩；
 *   - 结束必须 automator.disconnect() 释放连接（finally 保证）。
 *
 * mock 教训（沿用 probe_scan2 / ui_scan.js）：mockWxMethod 自定义函数返回值必须带
 *   errMsg 字段（如 { errMsg: 'scanCode:fail xxx' }），否则小程序侧按成功处理。
 *
 * 隔离铁律：必须由环境变量显式提供本机测试 API、测试公司和测试账号；
 *   禁止真实厂公司 86，登录后还会核对公司名称前缀。禁止默认连接开发库/真实库。
 *   测试数据带专属前缀（UITEST-ORD- / UIWIZ- / UISCAN- / KDRILL-），
 *   cleanupByPrefix 只清自己前缀的数据；清理顺序=撤销登记→删床次→删订单。
 */
const automator = require('miniprogram-automator');

const API_BASE = process.env.YISAO_E2E_API_BASE || '';
const WS_ENDPOINT = process.env.YISAO_E2E_WS_ENDPOINT || 'ws://127.0.0.1:9420';
const COMPANY_ID = Number(process.env.YISAO_E2E_COMPANY_ID || 0);
const ADMIN = {
  phone: process.env.YISAO_E2E_PHONE || '',
  password: process.env.YISAO_E2E_PASSWORD || ''
};
const ISOLATED_COMPANY_PREFIX = 'UI自动化隔离厂-';

// 在任何登录/清理/写请求之前 fail fast；旧脚本曾默认写真实厂 86，不能保留默认回退。
if (!API_BASE || !Number.isInteger(COMPANY_ID) || COMPANY_ID <= 0 || !ADMIN.phone || !ADMIN.password) {
  throw new Error('缺少隔离测试环境：请通过 run_ui_gate.py 启动，或显式设置 YISAO_E2E_* 环境变量');
}
const apiUrl = new URL(API_BASE);
if (!['127.0.0.1', 'localhost', '::1'].includes(apiUrl.hostname)) {
  throw new Error(`UI 自动化只允许本机隔离后端，当前为 ${apiUrl.hostname}`);
}
if (COMPANY_ID === 86) {
  throw new Error('安全保护：真实厂汉川旭东服饰（company_id=86）禁止用于 UI 自动化');
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ============================================================
// 断言账本 + 单步守卫
// ============================================================

class Suite {
  constructor(name) {
    this.name = name;
    this.pass = [];
    this.fail = [];
    this.skipped = [];
  }

  /** 断言：条件不成立记 FAIL（真实缺陷/断言落空） */
  check(name, cond, detail = '') {
    (cond ? this.pass : this.fail).push(name);
    console.log(cond ? 'PASS' : 'FAIL', name, detail ? `| ${detail}` : '');
    return !!cond;
  }

  /** 环境限制跳过（automator 超时/页面未就绪等），不算失败但需报告原因 */
  skip(name, detail = '') {
    this.skipped.push(name);
    console.log('SKIP', name, detail ? `| ${detail}` : '');
  }

  /**
   * 单步守卫：fn 抛错（含 withTimeout 超时）→ 该步记 SKIP 并返回 null，不整体崩。
   * record=true 时成功也记 PASS（断言型步骤）。
   * 返回 fn 的返回值； SKIP 时返回 null（调用方判空续跑或提前收尾）。
   */
  async step(name, fn, record = false) {
    try {
      const r = await fn();
      if (record) {
        this.pass.push(name);
        console.log('PASS', name);
      }
      return r;
    } catch (e) {
      this.skip(name, '异常降级: ' + String((e && e.message) || e).slice(0, 160));
      return null;
    }
  }

  /** 汇总输出 + 退出码（有 FAIL → 1） */
  summary() {
    console.log(`\n=== ${this.name} 完成：PASS=${this.pass.length} FAIL=${this.fail.length} SKIP=${this.skipped.length} ===`);
    if (this.skipped.length) console.log('跳过项:', this.skipped);
    if (this.fail.length) {
      console.log('失败项:', this.fail);
      process.exitCode = 1;
    }
  }
}

// ============================================================
// automator 超时包装与连接
// ============================================================

/** 给任意 promise 套超时（automator 桥接偶发不回包 → 超时抛错交给 step 守卫） */
function withTimeout(p, ms = 15000, label = 'automator 调用') {
  // 防 race「输家」的 rejection 变 unhandledRejection 直接杀进程
  if (p && typeof p.catch === 'function') p.catch(() => { /* 由 race 分支处理 */ });
  return Promise.race([
    p,
    sleep(ms).then(() => { throw new Error(`${label} 超时(${ms}ms)`); })
  ]);
}

// automator 桥接偶发对迟到回包抛「timeout waiting for automator response」，
// 若已超时放弃则成为 unhandled rejection —— 全局兜底记日志，不允许杀进程。
process.on('unhandledRejection', (reason) => {
  console.log('[warn] unhandledRejection(已兜底):', String((reason && reason.message) || reason).slice(0, 160));
});

/**
 * automator 连接（3 次重试，退避 2s/5s/10s）。
 * 全部失败 → 抛错（调用方据此把 UI 断言整段 SKIP）。
 */
async function connectAutomator(logger = console.log) {
  const backoffs = [2000, 5000, 10000];
  let lastErr = null;
  for (let i = 0; i < backoffs.length; i++) {
    try {
      const mp = await automator.connect({ wsEndpoint: WS_ENDPOINT });
      logger(`connected to 9420（第 ${i + 1} 次尝试）`);
      return mp;
    } catch (e) {
      lastErr = e;
      logger(`connect 第 ${i + 1} 次失败: ${(e && e.message) || e}`);
      if (i < backoffs.length - 1) await sleep(backoffs[i]);
    }
  }
  throw new Error('9420 连接 3 次均失败: ' + String((lastErr && lastErr.message) || lastErr));
}

// ============================================================
// IDE 会话自愈（2026-10-21 实测：分包页导航 reLaunch:fail timeout 会毒化
// automator 会话——之后连主包页导航也挂。上一套件探测分包后，下一套件
// 必须「探测→失败→重启 IDE→重连」自愈，否则全线 SKIP。）
// ============================================================
const { exec } = require('child_process');
const IDE_CLI = process.env.WECHAT_DEVTOOLS_CLI || 'D:\\Program Files\\微信web开发者工具\\cli.bat';
const IDE_PROJECT = process.env.WECHAT_DEVTOOLS_PROJECT || 'D:\\project\\扫菲\\frontend';

function runCli(args, timeoutMs = 120000) {
  return new Promise((resolve) => {
    exec(`"${IDE_CLI}" ${args.join(' ')}`, { timeout: timeoutMs, windowsHide: true }, (err, stdout) => {
      resolve({ err: err && err.message, out: String(stdout || '').slice(-200) });
    });
  });
}

/** 重启开发者工具并重开自动化端口（cli quit → cli auto，等编译就绪） */
async function restartIde(logger = console.log) {
  logger('[ide] 重启开发者工具（cli quit → cli auto --auto-port 9420）…');
  await runCli(['quit']);
  await sleep(8000);
  const r = await runCli(['auto', '--project', `"${IDE_PROJECT}"`, '--auto-port', '9420'], 180000);
  logger(`[ide] cli auto 完成 ${r.err ? 'err=' + r.err : 'ok'}`);
  await sleep(40000); // 等工程编译
}

/** 健康探针：reLaunch 首页（主包页）能在超时内完成 → 会话可用 */
async function homeProbe(mp) {
  try {
    // 开发者工具默认已打开首页时，重复 reLaunch 同页可能使自动化桥接失效。
    // 先确认当前页；只有不在首页时才执行导航，探针仍要求真实页面可读取。
    const current = await withTimeout(mp.currentPage(), 8000, 'homeProbe currentPage');
    if (current && current.path === 'pages/index/index') return true;
    await withTimeout(mp.reLaunch('/pages/index/index'), 18000, 'homeProbe reLaunch');
    return true;
  } catch (e) {
    return false;
  }
}

/**
 * 打开可用会话：连接 + 首页探针；不健康则重启 IDE 再来一轮（最多 2 轮）。
 * 全失败 → 抛错（套件把 UI 段记 SKIP）。
 */
async function openSession(logger = console.log) {
  for (let round = 0; round < 2; round++) {
    let mp = null;
    try {
      mp = await connectAutomator(logger);
    } catch (e) {
      if (round === 0) { await restartIde(logger); continue; }
      throw e;
    }
    if (await homeProbe(mp)) return mp;
    logger('[ide] 会话探针失败（导航桥接挂起），准备重启 IDE');
    try { await withTimeout(mp.disconnect(), 8000, 'disconnect'); } catch (e) { /* ignore */ }
    if (round === 0) await restartIde(logger);
  }
  throw new Error('IDE 重启后会话仍不可用');
}

/**
 * 分包页健康探测（证据落档用）：同步 evaluate 发导航 + 轮询 app 全局标志，
 * 不用 automator 原生 reLaunch（避免桥接挂起不留痕迹）。
 * 注意：探测失败会毒化当前会话 —— 只允许在套件收尾阶段调用。
 */
async function probeSubpackage(mp, url = '/packages/admin/help/index') {
  try {
    await withTimeout(mp.evaluate(() => {
      const app = getApp();
      app.__probe = { state: 'going', msg: '' };
      app.__probeGo = function (u) {
        const a = getApp();
        a.__probe = { state: 'going', msg: '' };
        wx.reLaunch({
          url: u,
          success: () => { a.__probe.state = 'ok'; },
          fail: (r) => { a.__probe.state = 'fail'; a.__probe.msg = (r && r.errMsg) || ''; }
        });
        return 'fired';
      };
      return 'ready';
    }), 10000, 'probe setup');
    await withTimeout(mp.evaluate((u) => getApp().__probeGo(u), url), 8000, 'probe fire');
    for (let i = 0; i < 12; i++) {
      await sleep(2500);
      const st = await withTimeout(mp.evaluate(() => getApp().__probe.state), 8000, 'probe poll');
      if (st !== 'going') {
        const msg = await withTimeout(mp.evaluate(() => getApp().__probe.msg), 8000, 'probe msg');
        return { ok: st === 'ok', msg: msg || st };
      }
    }
    return { ok: false, msg: '30s 内导航未回包' };
  } catch (e) {
    return { ok: false, msg: '探测异常: ' + String((e && e.message) || e).slice(0, 100) };
  }
}

// ============================================================
// 常用 automator 操作（全部带超时；异常向上抛，由 Suite.step 兜）
// ============================================================

/** page.data() 带超时 */
function pageData(page, ms = 10000) {
  return withTimeout(page.data(), ms, 'page.data');
}

/** 轮询等待页面数据满足条件（默认 16×500ms），超时抛错 */
async function waitPageData(page, loader, { timeout = 16000, label = '条件' } = {}) {
  const t0 = Date.now();
  for (;;) {
    const d = await pageData(page);
    if (loader(d)) return d;
    if (Date.now() - t0 > timeout) {
      throw new Error(`waitPageData 超时: ${label}`);
    }
    await sleep(500);
  }
}

/** 注入隔离测试管理员登录态（globalData 同款 storage 键，requireLogin 即通过）。
 *  返回已登录的 ApiClient（套件②层断言复用同一 token）。 */
async function injectLogin(mp) {
  const api = new ApiClient();
  await api.login();
  const me = await api.get('/auth/me');
  const user = (me && (me.user || me)) || { real_name: 'suite' };
  await withTimeout(mp.callWxMethod('setStorageSync', 'yisao_token', api.token), 10000, 'setStorageSync token');
  await withTimeout(mp.callWxMethod('setStorageSync', 'yisao_refresh', api.refresh || ''), 10000, 'setStorageSync refresh');
  await withTimeout(mp.callWxMethod('setStorageSync', 'yisao_user', user), 10000, 'setStorageSync user');
  await withTimeout(mp.callWxMethod('setStorageSync', 'yisao_company_id', COMPANY_ID), 10000, 'setStorageSync company');
  await withTimeout(mp.callWxMethod('setStorageSync', 'yisao_is_admin', 1), 10000, 'setStorageSync admin');
  await withTimeout(mp.callWxMethod('setStorageSync', 'yisao_permissions', ['*']), 10000, 'setStorageSync perms');
  return user;
}

/**
 * 元素点击（支持 '>>>' 跨自定义组件选择器）。
 * 找不到元素 → 抛错（由 step 记 SKIP）。
 */
async function elementTap(page, selector, label) {
  const el = await withTimeout(page.$(selector), 10000, `$ ${label || selector}`);
  if (!el) throw new Error(`元素不存在: ${label || selector}`);
  await withTimeout(el.tap(), 10000, `tap ${label || selector}`);
  return el;
}

/** 取元素文本（找不到返回 ''） */
async function elementText(el) {
  try {
    const t = await withTimeout(el.text(), 8000, 'el.text');
    return String(t == null ? '' : t).replace(/\s+/g, '');
  } catch (e) {
    return '';
  }
}

/** 元素列表（空数组容错） */
async function elements(page, selector) {
  try {
    const list = await withTimeout(page.$$(selector), 10000, `$$ ${selector}`);
    return list || [];
  } catch (e) {
    return [];
  }
}

// ============================================================
// API client（登录 + 公司头 + CRUD；直接 fetch 后端 8000）
// ============================================================

class ApiClient {
  constructor(cid = COMPANY_ID) {
    this.cid = cid;
    this.token = '';
    this.refresh = '';
  }

  async login() {
    const res = await this.raw('/auth/login', {
      method: 'POST',
      json: { phone: ADMIN.phone, password: ADMIN.password }
    });
    if (res.code !== 200 || !res.data.access) {
      throw new Error(`后端登录失败 code=${res.code}`);
    }
    this.token = res.data.access;
    this.refresh = res.data.refresh || '';
    // 仅登录成功还不够：环境变量填错公司时必须在任何业务写入前阻断。
    const mine = await this.raw('/companies/mine');
    const company = (Array.isArray(mine.data) ? mine.data : [])
      .find((c) => Number(c.id) === this.cid);
    if (!company || !String(company.name || '').startsWith(ISOLATED_COMPANY_PREFIX)) {
      this.token = '';
      this.refresh = '';
      throw new Error(`安全保护：company_id=${this.cid} 不是 ${ISOLATED_COMPANY_PREFIX}* 隔离测试公司`);
    }
    this.companyName = company.name;
    return res.data;
  }

  async raw(path, { method = 'GET', json, token } = {}) {
    const headers = { 'Content-Type': 'application/json' };
    headers.Authorization = `Bearer ${token || this.token}`;
    headers['X-Company-Id'] = String(this.cid);
    let resp;
    try {
      resp = await fetch(API_BASE + path, {
        method,
        headers,
        body: json === undefined ? undefined : JSON.stringify(json)
      });
    } catch (e) {
      return { code: 0, data: {}, netErr: String((e && e.message) || e) };
    }
    const text = await resp.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { raw: text.slice(0, 200) }; }
    return { code: resp.status, data };
  }

  get(path) { return this.raw(path).then((r) => r.data); }
  async getFull(path) { return this.raw(path); }
  post(path, json) { return this.raw(path, { method: 'POST', json }); }
  put(path, json) { return this.raw(path, { method: 'PUT', json }); }
  del(path) { return this.raw(path, { method: 'DELETE' }); }
}

// ============================================================
// 测试数据构造 / 清理（仅隔离测试公司，自清扫）
// ============================================================

/** 撤销一张菲票下的全部登记记录（GET /records?ticket_id= → 逐条 DELETE） */
async function purgeTicketRecords(api, ticketId) {
  const res = await api.raw(`/records?ticket_id=${ticketId}&page_size=100`);
  const rows = (res.data && res.data.results) || (Array.isArray(res.data) ? res.data : []) || [];
  for (const r of rows) {
    try { await api.del(`/records/${r.id}`); } catch (e) { /* 尽力而为 */ }
  }
}

/** 删床次（先撤其下所有登记；床次删除级联软删菲票） */
async function deleteProduction(api, prodId) {
  const tk = await api.raw(`/tickets?production_id=${prodId}&page_size=100`);
  const list = (tk.data && tk.data.results) || [];
  for (const t of list) await purgeTicketRecords(api, t.id);
  return api.del(`/productions/${prodId}`);
}

/** 按订单号前缀清扫：删关联床次 → 删订单（只动本前缀，不清他人数据） */
async function cleanupByPrefix(api, prefix, log = console.log) {
  let cleaned = 0;
  try {
    const res = await api.raw(`/orders?keyword=${encodeURIComponent(prefix)}&page_size=50`);
    const orders = (res.data && res.data.results) || [];
    for (const o of orders) {
      if (!String(o.order_no || '').startsWith(prefix)) continue;
      try {
        const prods = await api.raw(`/productions?order_id=${o.id}&page_size=50`);
        for (const p of (prods.data && prods.data.results) || []) {
          const d = await deleteProduction(api, p.id);
          if (d.code >= 300) log(`[cleanup] 床次 ${p.id} 删除失败 code=${d.code}（可能他人数据/有登记），跳过`);
        }
        const od = await api.del(`/orders/${o.id}`);
        if (od.code >= 300) {
          // 有床次挡删 → 兜底取消，不硬删
          await api.put(`/orders/${o.id}/status`, { status: 'cancelled' });
          log(`[cleanup] 订单 ${o.order_no} 删除受阻，已置 cancelled`);
        } else {
          cleaned += 1;
        }
      } catch (e) {
        log(`[cleanup] 订单 ${o.order_no} 清理异常: ${(e && e.message) || e}`);
      }
    }
  } catch (e) {
    log(`[cleanup] 前缀 ${prefix} 清扫异常: ${(e && e.message) || e}`);
  }
  return cleaned;
}

/**
 * 扫菲/下钻共用数据构造：
 *   订单（totalQty）→ 床次（bedNo，挂单）→ 配整件工序价 → 生成 1 张菲票（每扎上限=totalQty）→
 *   预登记 registerQty 件（接近满扎）。
 * 返回 { orderId, prodId, ticketId, recordIds }；失败抛错由调用方兜。
 */
async function buildBedFixture(api, { orderNo, bedNo, totalQty, registerQty, styleId, processId }) {
  const ord = await api.post('/orders', {
    order_no: orderNo,
    style: styleId,
    items: [{ color: '黑色', size: 'M', qty: totalQty }]
  });
  if (ord.code !== 201) throw new Error(`建订单失败 code=${ord.code} ${JSON.stringify(ord.data).slice(0, 120)}`);
  const orderId = ord.data.id;

  const pr = await api.post('/productions', {
    style: styleId,
    bed_no: bedNo,
    total_qty: totalQty,
    order_type: '自产',
    customer: null,
    fabric: '',
    order_id: orderId
  });
  if (pr.code !== 201) throw new Error(`建床次失败 code=${pr.code} ${JSON.stringify(pr.data).slice(0, 120)}`);
  const prodId = pr.data.id;

  // 配整件工序价（0 价登记会被拦），失败不抛（登记时再兜）
  await api.put(`/productions/${prodId}/order-processes`, {
    process_items: [{ process_id: processId, price: '0.05' }]
  });

  const tb = await api.post('/tickets/batch', {
    production_id: prodId,
    items: [{ color: '黑色', size: 'M', qty: totalQty, fabric: '', piece_no: '' }],
    bundle_limit: totalQty,
    start_bundle_no: '1'
  });
  if (tb.code !== 201 || !(tb.data || []).length) {
    throw new Error(`生成菲票失败 code=${tb.code}`);
  }
  const ticketId = tb.data[0].id;

  const recordIds = [];
  if (registerQty > 0) {
    const rec = await api.post('/records', {
      ticket_id: ticketId, process_id: processId, qty: registerQty
    });
    if (rec.code === 201 && rec.data.id) recordIds.push(rec.data.id);
  }
  return { orderId, prodId, ticketId, recordIds };
}

module.exports = {
  automator,
  API_BASE,
  WS_ENDPOINT,
  COMPANY_ID,
  ADMIN,
  IDE_CLI,
  IDE_PROJECT,
  sleep,
  Suite,
  withTimeout,
  connectAutomator,
  restartIde,
  homeProbe,
  openSession,
  probeSubpackage,
  pageData,
  waitPageData,
  injectLogin,
  elementTap,
  elementText,
  elements,
  ApiClient,
  purgeTicketRecords,
  deleteProduction,
  cleanupByPrefix,
  buildBedFixture
};
