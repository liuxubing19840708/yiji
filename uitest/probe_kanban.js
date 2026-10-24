/**
 * 看板页 automator 探针（P3：wxml 改动编译验证 + 合并列无内部横线 + 自适应列宽生效）。
 *
 * 前置：外部已执行 cli.bat auto --project <frontend> --auto-port 9420。
 * 流程：
 *   1. 后端登录公司2管理员 → 记住当前默认模板；
 *   2. 建探针模板（3 列全 width=0 自适应 + 款号列 merge=block）并设为默认；
 *   3. reLaunch 首页（编译验证）→ 注入登录态 storage → reLaunch 看板页；
 *   4. 断言：列宽全部 > 0（自适应已填充）；存在 blockMerge 段且 cells.length===1
 *      （合并段=单格，内部无横线）；截图；
 *   5. finally 清理：删探针模板并恢复原默认模板。
 * 运行：node probe_kanban.js
 */
const automator = require('miniprogram-automator');

const API = 'http://127.0.0.1:8000/api';
const MARK = '看板E2E探针';
const PASS = [], FAIL = [];
function check(name, cond, detail = '') {
  (cond ? PASS : FAIL).push(name);
  console.log(cond ? 'PASS' : 'FAIL', name, detail ? `| ${detail}` : '');
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
  let oldDefaultId = null;
  let probeId = null;
  let mp = null;
  try {
    // ---- 1. 后端准备 ----
    const login = await api('/auth/login', {
      method: 'POST',
      json: { phone: '13800000001', password: 'yisao1234' }
    });
    check('B1 后端登录', login.code === 200 && login.data.access, `code=${login.code}`);
    const at = login.data.access;
    const cid = 2;
    const me = await api('/auth/me', { token: at, cid });
    const user = (me.data && (me.data.user || me.data)) || { real_name: 'probe' };

    const tpls = await api('/kanban/templates', { token: at, cid });
    const oldDefault = (tpls.data.results || []).find((t) => t.is_default);
    oldDefaultId = oldDefault ? oldDefault.id : null;
    console.log('old default template:', oldDefaultId, oldDefault && oldDefault.name);

    const probeCols = {
      name: MARK, is_default: true, columns: [
        { seq: 0, title: '款号', bind_type: 'field', field_key: 'style_no',
          extra: { merge: 'block', width: 0, align: 'left' } },
        { seq: 1, title: '颜色', bind_type: 'field', field_key: 'color',
          extra: { width: 0, align: 'left' } },
        { seq: 2, title: '订单数量', bind_type: 'field', field_key: 'order_qty',
          extra: { width: 0, align: 'right' } }
      ]
    };
    const created = await api('/kanban/templates', { method: 'POST', token: at, cid, json: probeCols });
    check('B2 探针模板创建', created.code === 201 && created.data.id,
      `code=${created.code} ${JSON.stringify(created.data).slice(0, 100)}`);
    probeId = created.data.id;

    // ---- 2. automator 连接 + reLaunch 首页（wxml/编译验证） ----
    mp = await automator.connect({ wsEndpoint: 'ws://localhost:9420' });
    console.log('connected to 9420');
    const home = await mp.reLaunch('/pages/index/index');
    check('P1 reLaunch 首页（编译验证）', !!home, home && home.path);

    // ---- 3. 注入登录态 → 进看板页 ----
    await mp.callWxMethod('setStorageSync', 'yisao_token', at);
    await mp.callWxMethod('setStorageSync', 'yisao_refresh', login.data.refresh || '');
    await mp.callWxMethod('setStorageSync', 'yisao_user', user);
    await mp.callWxMethod('setStorageSync', 'yisao_company_id', 2);
    await mp.callWxMethod('setStorageSync', 'yisao_is_admin', 1);

    const page = await mp.reLaunch('/packages/admin/kanban/index');
    check('P2 reLaunch 看板页', !!page, page && page.path);

    // 等渲染数据（render 接口返回）
    let d = null;
    for (let i = 0; i < 20; i++) {
      await sleep(500);
      d = await page.data();
      if (d && d.groups && d.groups.length) break;
    }
    d = await page.data();
    check('P3 看板页渲染出数据', d && d.groups && d.groups.length > 0,
      `groups=${d && d.groups && d.groups.length} tpl=${d && d.tplName}`);
    check('P4 使用探针模板', d && d.tplName === MARK, `tplName=${d && d.tplName}`);

    // 自适应列宽：width=0 的列渲染前已估宽填充 → 全部 > 0
    const widths = (d.columns || []).map((c) => c.width);
    check('P5 自适应列宽生效（全部>0）', widths.length === 3 && widths.every((w) => w > 0),
      `widths=${JSON.stringify(widths)}`);

    // 合并列无内部横线：merge=block 段 = 单格（cells.length===1，单格自身无边框缝）
    const segs = [];
    for (const g of d.groups || []) {
      for (const b of g.blocks || []) {
        for (const s of b.segs || []) segs.push(s);
      }
    }
    const mergedSeg = segs.find((s) => s.blockMerge);
    check('P6 block 合并段=单格（无内部横线）',
      !!mergedSeg && mergedSeg.cells.length === 1,
      mergedSeg ? `cells=${mergedSeg.cells.length}` : '无 blockMerge 段');
    const multiRow = segs.some((s) => s.cells.length > 1);
    check('P7 多行块存在（对照：非合并列正常分行）', multiRow, `segs=${segs.length}`);

    // 截图留档
    try {
      await mp.screenshot({ path: 'shots/probe_kanban.png' });
      console.log('screenshot: shots/probe_kanban.png');
    } catch (e) {
      console.log('[warn] screenshot 失败(跳过):', e.message);
    }
  } catch (e) {
    console.log('PROBE ERR:', e && e.message);
    process.exitCode = 2;
  } finally {
    // ---- 4. 清理 ----
    try {
      const lg = await api('/auth/login', {
        method: 'POST', json: { phone: '13800000001', password: 'yisao1234' }
      });
      const at2 = lg.data.access;
      if (probeId) {
        await api(`/kanban/templates/${probeId}`, { method: 'DELETE', token: at2, cid: 2 });
        console.log('probe template deleted:', probeId);
      }
      if (oldDefaultId) {
        await api(`/kanban/templates/${oldDefaultId}/default`, { method: 'PUT', token: at2, cid: 2 });
        console.log('old default restored:', oldDefaultId);
      }
    } catch (e) {
      console.log('[warn] cleanup 失败，需人工删除模板「' + MARK + '」:', e.message);
    }
    try { if (mp) await mp.disconnect(); } catch (e) { /* ignore */ }
  }
  console.log(`\n=== 看板探针完成：PASS=${PASS.length} FAIL=${FAIL.length} ===`);
  if (FAIL.length) { console.log('失败项:', FAIL); process.exitCode = 1; }
})();
