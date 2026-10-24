/**
 * 看板下钻+订单页签+工作台两块数字 探针（SF2，probe_kanban2.js）。
 *
 * 覆盖（重设计 §2.9 P0/P1）：
 *   1. kanban 页编译 + 计划表渲染（含新顶层页签 wxml）；
 *   2. 列改名显示端：不再出现「订单数量」（显示别名 → 「菲票数量」）；
 *   3. 行 tap 下钻：款号列（link=production 兜底）带参跳床次列表页（styleId/styleNo 预过滤）；
 *   4. navigateBack 返回后筛选状态保留（range/boardTab 不丢）；
 *   5. 订单页签渲染：后端 /orders 未就绪 → 「服务升级中」空态（不白屏）；就绪 → 卡片墙数据；
 *   6. 工作台常驻两块数字（todayQty/todayAmount）；
 *   7. 帮助页编译 + 三篇角色文章。
 *
 * 前置：外部已执行 cli.bat auto --project <frontend> --auto-port 9420，且后端 8000 已起。
 * 运行：node probe_kanban2.js（在 uitest/ 目录下）
 * 说明：extra.link 直测依赖 SA2 的 _columns_payload 下发 link，落地前用款号列兜底链路验证；
 *       SA2 合入后可在探针模板列 extra 里加 link 三值做白名单断言（非法值忽略用例同处补）。
 */
const automator = require('miniprogram-automator');

const API = 'http://127.0.0.1:8000/api';
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
  let resp;
  try {
    resp = await fetch(API + path, {
      method, headers, body: json === undefined ? undefined : JSON.stringify(json)
    });
  } catch (e) {
    return { code: 0, data: {}, netErr: String(e && e.message) };
  }
  const text = await resp.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (e) { /* ignore */ }
  return { code: resp.status, data };
}

/** 等待 page.data() 中 loader 返回 true（轮询，最多 20×500ms） */
async function waitFor(page, loader, label) {
  for (let i = 0; i < 20; i++) {
    await sleep(500);
    const d = await page.data();
    if (loader(d)) return d;
  }
  console.log(`[warn] waitFor 超时: ${label}`);
  return await page.data();
}

(async () => {
  let mp = null;
  try {
    // ---- 0. 连 automator（9420 不可用 → 交付待验证） ----
    try {
      mp = await automator.connect({ wsEndpoint: 'ws://localhost:9420' });
      console.log('connected to 9420');
    } catch (e) {
      console.log('CONNECT ERR:', e.message);
      console.log('\n=== 端口 9420 不可用：探针无法运行（脚本已交付，待真机/开发者工具 automator 端口后复跑）===');
      console.log('复跑步骤：cli.bat auto --project frontend --auto-port 9420 && node probe_kanban2.js');
      process.exitCode = 3;
      return;
    }

    // ---- 1. 后端登录（后端不可用 → 后续页面断言仍可做编译级验证） ----
    const login = await api('/auth/login', {
      method: 'POST',
      json: { phone: '13800000001', password: 'yisao1234' }
    });
    const backendUp = login.code === 200 && login.data.access;
    check('B1 后端登录', backendUp, `code=${login.code}${login.netErr ? ' ' + login.netErr : ''}`);
    if (!backendUp) skip('B1 之后的接口级断言', '后端 8000 未就绪，仅验证页面编译/数据兜底');
    const at = login.data.access || '';
    const cid = 2;
    let user = { real_name: 'probe' };
    if (backendUp) {
      const me = await api('/auth/me', { token: at, cid });
      user = (me.data && (me.data.user || me.data)) || user;
    }

    // ---- 2. 注入登录态 → reLaunch 看板页（wxml/ts 编译验证） ----
    await mp.callWxMethod('setStorageSync', 'yisao_token', at);
    await mp.callWxMethod('setStorageSync', 'yisao_refresh', login.data.refresh || '');
    await mp.callWxMethod('setStorageSync', 'yisao_user', user);
    await mp.callWxMethod('setStorageSync', 'yisao_company_id', cid);
    await mp.callWxMethod('setStorageSync', 'yisao_is_admin', 1);

    const page = await mp.reLaunch('/packages/admin/kanban/index');
    check('P1 reLaunch 看板页（编译验证：顶层页签/订单卡片墙 wxml）', !!page, page && page.path);
    const d0 = await waitFor(page, (d) => d && d.summary && !d.loading, '看板渲染数据');

    check('P2 默认页签=计划表', d0.boardTab === 'plan', `boardTab=${d0.boardTab}`);
    check('P3 计划表渲染出数据',
      d0.groups && d0.groups.length > 0 && !!d0.summary,
      `groups=${d0.groups && d0.groups.length} tpl=${d0.tplName}`);

    // ---- 3. 列改名显示端：「订单数量」不再出现（显示别名 → 菲票数量） ----
    const titles = (d0.columns || []).map((c) => c.title);
    check('P4 列改名：无「订单数量」（显示端统一「菲票数量」）',
      titles.length === 0 || titles.indexOf('订单数量') < 0,
      `titles=${JSON.stringify(titles)}`);

    // ---- 4. 行 tap 下钻：筛选 → 找 nav → 跳床次列表（带参预过滤） ----
    await page.callMethod('onRangeTap', { currentTarget: { dataset: { key: 'week' } } });
    await sleep(800);
    const d1 = await page.data();
    check('P5 切时间范围生效', d1.range === 'week', `range=${d1.range}`);

    // 找第一个可下钻 nav（款号列兜底 link=production → /packages/admin/productions/index?styleId=…）
    let nav = '';
    for (const g of d1.groups || []) {
      for (const b of g.blocks || []) {
        for (const s of b.segs || []) {
          for (const c of s.cells || []) {
            if (c.nav && c.nav.indexOf('/packages/admin/productions/index') === 0) { nav = c.nav; break; }
          }
          if (nav) break;
        }
        if (nav) break;
      }
      if (nav) break;
    }
    check('P6 存在下钻 nav（款号列兜底/模板 link）', !!nav, `nav=${nav}`);
    const styleIdMatch = /styleId=(\d+)/.exec(nav || '');
    check('P7 下钻参数带 styleId 预过滤', !!styleIdMatch, `styleId=${styleIdMatch && styleIdMatch[1]}`);

    if (nav) {
      await page.callMethod('onCellTap', { currentTarget: { dataset: { nav } } });
      await sleep(1500);
      const bedPage = await mp.currentPage();
      check('P8 已跳到床次列表页', bedPage && bedPage.path === 'packages/admin/productions/index',
        `path=${bedPage && bedPage.path}`);
      const bd = bedPage ? await bedPage.data() : {};
      check('P9 床次列表带款预过滤',
        (!!styleIdMatch && bd.drillStyleId === Number(styleIdMatch[1])) || !!bd.drillStyleNo,
        `drillStyleId=${bd.drillStyleId} drillStyleNo=${bd.drillStyleNo} keyword=${bd.keyword}`);

      // ---- 5. navigateBack → 看板筛选保留 ----
      await mp.navigateBack();
      await sleep(1500);
      const backPage = await mp.currentPage();
      check('P10 返回到看板页', backPage && backPage.path === 'packages/admin/kanban/index',
        `path=${backPage && backPage.path}`);
      const d2 = backPage ? await backPage.data() : {};
      check('P11 返回后筛选保留（range=week + 计划表页签）',
        d2.range === 'week' && d2.boardTab === 'plan',
        `range=${d2.range} boardTab=${d2.boardTab}`);
    } else {
      skip('P8-P11 下钻跳转与返回保筛选', '无可下钻列（后端渲染列无款号兜底）');
    }

    // ---- 6. 订单页签：后端未就绪 → 服务升级中空态；就绪 → 卡片墙 ----
    await page.callMethod('onBoardTab', { currentTarget: { dataset: { key: 'orders' } } });
    const d3 = await waitFor(page, (d) => d && !d.ordersLoading, '订单页签加载');
    check('P12 订单页签切换成功', d3.boardTab === 'orders', `boardTab=${d3.boardTab}`);
    if (d3.ordersError) {
      // SA1 /orders 未就绪的预期路径：空态而非白屏
      const emptyEl = await page.$('.ord-upgrade');
      check('P13 订单接口未就绪 → 「服务升级中」空态（不白屏）', !!emptyEl);
    } else {
      const wall = await page.$('.ord-wall');
      check('P13 订单卡片墙渲染（/orders 已就绪）', !!wall, `orders=${(d3.orders || []).length}`);
      const o0 = (d3.orders || [])[0];
      if (o0) {
        check('P14 订单卡片字段齐（环形/交期灯/落后chip）',
          typeof o0.progress === 'number' && !!o0.lightCls && typeof o0.behind === 'boolean',
          `progress=${o0.progress} light=${o0.lightCls} behind=${o0.behind}`);
      } else {
        skip('P14 订单卡片字段', '当前公司无订单数据');
      }
    }

    // ---- 7. 工作台常驻两块数字 ----
    const home = await mp.reLaunch('/pages/index/index');
    await sleep(1500);
    const hd = home ? await home.data() : {};
    check('P15 工作台今日件数（number 常驻）', typeof hd.todayQty === 'number', `todayQty=${hd.todayQty}`);
    check('P16 工作台今日预估工资（字符串金额，SA3 未上线=0.00）',
      typeof hd.todayAmount === 'string' && hd.todayAmount.length > 0, `todayAmount=${hd.todayAmount}`);

    // ---- 8. 帮助页编译 + 三篇角色文章 ----
    const help = await mp.reLaunch('/packages/admin/help/index');
    await sleep(800);
    const hlp = help ? await help.data() : {};
    check('P17 帮助页编译 + 三角色注册', !!help && (hlp.roles || []).length === 3,
      `roles=${hlp.roles && hlp.roles.length} current=${hlp.current}`);
    check('P18 帮助文章含动线+FAQ 两段',
      hlp.article && (hlp.article.sections || []).length === 2 &&
      hlp.article.sections.every((s) => s.lines && s.lines.length > 0),
      `sections=${hlp.article && hlp.article.sections && hlp.article.sections.length}`);
    // 角色切换
    await help.callMethod('onRoleTap', { currentTarget: { dataset: { key: 'cutter' } } });
    await sleep(300);
    const hlp2 = await help.data();
    check('P19 帮助角色切换', hlp2.current === 'cutter', `current=${hlp2.current}`);

    try {
      await mp.screenshot({ path: 'shots/probe_kanban2.png' });
      console.log('screenshot: shots/probe_kanban2.png');
    } catch (e) {
      console.log('[warn] screenshot 失败(跳过):', e.message);
    }
  } catch (e) {
    console.log('PROBE ERR:', e && (e.stack || e.message));
    process.exitCode = 2;
  } finally {
    try { if (mp) await mp.disconnect(); } catch (e) { /* ignore */ }
  }
  console.log(`\n=== 看板下钻探针完成：PASS=${PASS.length} FAIL=${FAIL.length} SKIP=${SKIP.length} ===`);
  if (FAIL.length) { console.log('失败项:', FAIL); process.exitCode = 1; }
})();
