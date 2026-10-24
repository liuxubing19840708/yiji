// -*- coding: utf-8 -*-
/**
 * UI + 后端 融合测试：管理端长尾操作 + 表单校验抽样
 *
 * 校验分支（行为断言：前端拦截 → 不发请求 → 落库计数不变）：
 *   V1 邀请非法手机号  V2 客户空名称  V3 编菲(未选款式/非法床次/非法总量)
 *   V4 工序单价非法值  V5 岗位空名称
 * 操作链（UI 动作 → API 落库断言）：
 *   O1 岗位建/删  O2 员工置顶  O3 拒绝入职  O4 审批/离职/复职  O5 设置岗位
 *   O6 客户分组建/删  O7 款式编辑  O8 款式复制  O9 图片上传(mock)  O10 款式删除
 *   O11 通知单条已读/全部已读  O12 设置开关  O13 协助登记选择+退出
 *   V6 公司切换(同公司)  V7 加入不存在公司
 *
 * 前置: 后端 8000；开发者工具 cli.bat auto --auto-port 9420。
 * 幂等: preclean/cleanup 清 OP_PHONE 用户与 UI操作/操作链 标记数据。
 */
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const automator = require('miniprogram-automator');

const API = 'http://127.0.0.1:8000/api';
const ADMIN = { phone: '13800000001', password: 'yisao1234' };
const OP_PHONE = '13877771001';
// 标记用 ASCII 前缀：execSync 命令行传中文会被 cmd 搅成乱码（preclean 过滤会失灵）
const MARK = 'UIOPS';
const SNO = 'UIOPS1';

const results = [];
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond });
  console.log((cond ? 'PASS' : 'FAIL'), name, cond ? '' : '| ' + String(detail).slice(0, 160));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitData(page, fn, timeout = 8000, label = 'cond') {
  const t0 = Date.now();
  let d = await page.data();
  while (!fn(d)) {
    if (Date.now() - t0 > timeout) throw new Error('waitData 超时: ' + label + ' | ' + JSON.stringify(d).slice(0, 150));
    await sleep(400);
    d = await page.data();
  }
  return d;
}
async function tapSel(page, sel, tries = 12) {
  for (let i = 0; i < tries; i++) {
    const el = await page.$(sel);
    if (el) { await el.tap(); return el; }
    await sleep(400);
  }
  throw new Error('tapSel 未找到元素: ' + sel);
}
const rows = (d) => (Array.isArray(d) ? d : (d && d.results) || []);

let adminTok = '', cid = 0;
async function api(method, urlPath, body) {
  const res = await fetch(API + urlPath, {
    method: method.toUpperCase(),
    headers: {
      'Content-Type': 'application/json',
      ...(adminTok ? { Authorization: 'Bearer ' + adminTok } : {}),
      ...(cid ? { 'X-Company-Id': String(cid) } : {})
    },
    body: body ? JSON.stringify(body) : undefined
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
  return { status: res.status, data };
}

async function goto(mp, url, waitMs = 1500) {
  await mp.navigateTo(url);
  await sleep(waitMs);
  return mp.currentPage();
}
async function freshStylesPage(mp) {
  await mp.reLaunch('/pages/index/index');
  await sleep(600);
  return goto(mp, '/packages/admin/styles/index');
}

(async () => {
  const mp = await automator.connect({ wsEndpoint: 'ws://localhost:9420' });
  console.log('connected');
  try {
    // ===== V0 前置 =====
    const lg = await api('POST', '/auth/login', ADMIN);
    check('V0a API 管理员登录', lg.status === 200 && lg.data && lg.data.access, `status=${lg.status}`);
    adminTok = lg.data.access;
    const mine = await api('GET', '/companies/mine');
    const co = rows(mine.data).find((c) => String(c.company_no) === '100001');
    cid = co.id;
    check('V0b API 取公司上下文', !!co, JSON.stringify(mine.data).slice(0, 80));

    // preclean：临时用户/标记岗位/款式/客户/分组 全部 ORM 硬删
    // （API DELETE 是软删；Post company+name 唯一约束未排除软删行，残留会让重建同名 500）
    {
      const py =
        "from apps.accounts.models import User, Post; " +
        "from apps.catalog.models import Style, Customer, CustomerGroup; " +
        "User.objects.filter(phone='" + OP_PHONE + "').delete(); " +
        "Post.all_objects.filter(name__startswith='" + MARK + "').delete(); " +
        "Style.all_objects.filter(style_no__startswith='" + SNO + "').delete(); " +
        "Customer.all_objects.filter(name__startswith='" + MARK + "').delete(); " +
        "CustomerGroup.all_objects.filter(name__startswith='" + MARK + "').delete(); " +
        "print('ok')";
      try {
        execSync('.venv\\Scripts\\python.exe manage.py shell -c "' + py + '"', {
          cwd: 'D:\\project\\扫菲\\backend', env: { ...process.env, PYTHONIOENCODING: 'utf-8' }, encoding: 'utf-8'
        });
      } catch (e) { /* 首跑无残留 */ }
    }
    const mk = await api('POST', '/styles', { style_no: SNO, name: MARK + '原款' });
    check('V0c preclean+标记款式就绪', mk.status === 201, `status=${mk.status} ${JSON.stringify(mk.data).slice(0, 80)}`);

    // UI 管理员登录（mode 确认 + 重试，防 tab 点击未生效）
    await mp.evaluate(() => { wx.clearStorage(); });
    let page = await mp.reLaunch('/pages/login/index');
    await page.waitFor(1500);
    page = await mp.currentPage();
    for (let i = 0; i < 3 && (await page.data()).mode !== 'password'; i++) {
      await page.callMethod('onMode', { currentTarget: { dataset: { mode: 'password' } } });
      await sleep(500);
    }
    check('V0d-0 登录页切到密码模式', (await page.data()).mode === 'password', `mode=${(await page.data()).mode}`);
    const aInputs = await page.$$('input');
    await aInputs[0].input(ADMIN.phone);
    await aInputs[1].input(ADMIN.password);
    await tapSel(page, '.agree .checkbox');
    await tapSel(page, '.btn.btn-primary');
    for (let i = 0; i < 10; i++) {
      await sleep(1200);
      page = await mp.currentPage();
      if (page.path === 'pages/index/index') break;
    }
    if (page.path !== 'pages/index/index' && (await page.data()).stage === 'pick') {
      const items = await page.$$('.co-item');
      for (const it of items) if ((await it.text()).indexOf('100001') >= 0) { await it.tap(); break; }
      await sleep(3000);
      page = await mp.currentPage();
    }
    check('V0d UI 管理员登录', page.path === 'pages/index/index', page.path);

    // ===== V1 邀请非法手机号（前端拦截，不发请求） =====
    page = await goto(mp, '/packages/admin/employees/index');
    await waitData(page, (d) => (d.list || []).length > 0, 8000, '员工列表');
    const pend0 = rows((await api('GET', '/employees?tab=pending')).data).length;
    await page.callMethod('openInvite');
    await sleep(600);
    await page.callMethod('onInvitePhone', { detail: { value: '123' } });
    await page.callMethod('submitInvite');
    await sleep(800);
    const pend1 = rows((await api('GET', '/employees?tab=pending')).data).length;
    check('V1 邀请非法手机号被拦截', pend1 === pend0, `before=${pend0} after=${pend1}`);
    await page.callMethod('closeInvite');

    // ===== O3 前置：API 邀请临时员工（pending） =====
    const inv = await api('POST', '/employees/invite', { phone: OP_PHONE, real_name: '操作链员工' });
    check('O3-0 API 邀请临时员工', inv.status === 201 || inv.status === 200, `status=${inv.status}`);
    const pendList = rows((await api('GET', '/employees?tab=pending')).data);
    const tmpEmp = pendList.find((e) => e.phone === OP_PHONE);
    check('O3-1 待审核列表可见临时员工', !!tmpEmp, JSON.stringify(pendList).slice(0, 80));

    // ===== O3 UI 拒绝入职 =====
    await page.callMethod('onTab', { currentTarget: { dataset: { key: 'pending' } } });
    await waitData(page, (d) => (d.list || []).some((e) => e.phone === OP_PHONE), 6000, 'pending tab');
    await mp.mockWxMethod('showModal', { confirm: true });
    await page.callMethod('onReject', { currentTarget: { dataset: { id: tmpEmp.id } } });
    await mp.restoreWxMethod('showModal');
    await sleep(1000);
    const rej = rows((await api('GET', '/employees?tab=resigned')).data).find((e) => e.phone === OP_PHONE);
    check('O3 UI 拒绝入职→离职态', !!rej && rej.status === 2, JSON.stringify(rej || {}).slice(0, 80));

    // ===== O4a 再邀请复活为待审核 → UI 审批入职 =====
    await api('POST', '/employees/invite', { phone: OP_PHONE, real_name: '操作链员工' });
    // onTab 有同值 guard（tab 未变不重拉），页面仍在 pending tab，直接触发 load
    await page.callMethod('load');
    await waitData(page, (d) => (d.list || []).some((e) => e.phone === OP_PHONE), 6000, 'pending tab2');
    const pend2 = (await page.data()).list.find((e) => e.phone === OP_PHONE);
    await page.callMethod('onApprove', { currentTarget: { dataset: { id: pend2.id } } });
    await sleep(1000);
    const act1 = rows((await api('GET', '/employees?tab=all')).data).find((e) => e.phone === OP_PHONE);
    check('O4a UI 审批入职→在职', !!act1 && act1.status === 1, JSON.stringify(act1 || {}).slice(0, 80));

    // ===== O2 UI 置顶/取消（王小丽，状态翻转断言，兼容初始已置顶） =====
    await page.callMethod('onTab', { currentTarget: { dataset: { key: 'active' } } });
    await waitData(page, (d) => (d.list || []).some((e) => e.phone === '13800000002'), 6000, 'active tab');
    const wlEmp = (await page.data()).list.find((e) => e.phone === '13800000002');
    const pin0 = !!rows((await api('GET', '/employees?tab=all')).data).find((e) => e.phone === '13800000002').is_pinned;
    await page.callMethod('onTogglePin', { currentTarget: { dataset: { id: wlEmp.id } } });
    await sleep(800);
    const wlAfter = rows((await api('GET', '/employees?tab=all')).data).find((e) => e.phone === '13800000002');
    await page.callMethod('onTogglePin', { currentTarget: { dataset: { id: wlEmp.id } } });
    await sleep(800);
    const wlAfter2 = rows((await api('GET', '/employees?tab=all')).data).find((e) => e.phone === '13800000002');
    check('O2 UI 置顶↔取消联动', (!!wlAfter.is_pinned) === !pin0 && (!!wlAfter2.is_pinned) === pin0,
      `init=${pin0} after1=${wlAfter.is_pinned} after2=${wlAfter2.is_pinned}`);

    // ===== O4b/O4c UI 离职 → 复职（临时员工） =====
    await page.callMethod('onTab', { currentTarget: { dataset: { key: 'active' } } });
    await waitData(page, (d) => (d.list || []).some((e) => e.phone === OP_PHONE), 6000, 'active tmp');
    await mp.mockWxMethod('showActionSheet', { tapIndex: 1 }); // 在职菜单: 1=办理离职
    await page.callMethod('onMenu', { currentTarget: { dataset: { id: act1.id } } });
    await mp.restoreWxMethod('showActionSheet');
    await sleep(1200);
    const res2 = rows((await api('GET', '/employees?tab=resigned')).data).find((e) => e.phone === OP_PHONE);
    check('O4b UI 办理离职→离职态', !!res2 && res2.status === 2, JSON.stringify(res2 || {}).slice(0, 80));
    await page.callMethod('onTab', { currentTarget: { dataset: { key: 'resigned' } } });
    await waitData(page, (d) => (d.list || []).some((e) => e.phone === OP_PHONE), 6000, 'resigned tmp');
    await mp.mockWxMethod('showActionSheet', { tapIndex: 0 }); // 离职菜单: 0=复职
    await page.callMethod('onMenu', { currentTarget: { dataset: { id: res2.id } } });
    await mp.restoreWxMethod('showActionSheet');
    await sleep(1200);
    const act2 = rows((await api('GET', '/employees?tab=all')).data).find((e) => e.phone === OP_PHONE);
    check('O4c UI 复职→在职', !!act2 && act2.status === 1, JSON.stringify(act2 || {}).slice(0, 80));

    // ===== O5 UI 设置岗位（两级 ActionSheet：一级选0=设置岗位，二级选0=岗位列表第一个） =====
    const posts0 = rows((await api('GET', '/posts?page_size=50')).data);
    const beforePost = act2.post_id;
    await page.callMethod('onTab', { currentTarget: { dataset: { key: 'active' } } });
    await waitData(page, (d) => (d.list || []).some((e) => e.phone === OP_PHONE), 6000, 'active tmp2');
    await mp.mockWxMethod('showActionSheet', { tapIndex: 0 }); // 两级共用：一级0=设置岗位，二级0=posts[0]
    await page.callMethod('onMenu', { currentTarget: { dataset: { id: act2.id } } });
    await sleep(1800); // 一级回调 → fetchPosts(异步) → 二级弹层
    await mp.restoreWxMethod('showActionSheet');
    await sleep(1000);
    const act3 = rows((await api('GET', '/employees?tab=all')).data).find((e) => e.phone === OP_PHONE);
    check('O5 UI 设置岗位→岗位变化', act3 && act3.post_id === posts0[0].id && act3.post_id !== beforePost,
      `before=${beforePost} after=${act3 && act3.post_id} target=${posts0[0].id}`);

    // ===== V5+O1 岗位：空名称拦截 → 创建 → UI 删除 =====
    page = await goto(mp, '/packages/admin/posts/index');
    await waitData(page, (d) => (d.posts || []).length > 0 || d.loaded === true, 6000, '岗位列表').catch(() => {});
    const pd0 = await page.data();
    const postsCount0 = (pd0.posts || pd0.list || []).length;
    await page.callMethod('openCreate');
    await page.callMethod('onSubmit');
    await sleep(600);
    const pd1 = await page.data();
    check('V5 岗位空名称被拦截', ((pd1.posts || pd1.list || []).length) === postsCount0, 'count unchanged');
    await page.callMethod('onNameInput', { detail: { value: MARK + '岗' } });
    await page.callMethod('onTogglePerm', { currentTarget: { dataset: { key: 'styles' } } });
    await page.callMethod('onSubmit');
    let newPost = null;
    for (let i = 0; i < 8 && !newPost; i++) {
      await sleep(700);
      newPost = rows((await api('GET', '/posts?page_size=50')).data).find((p) => p.name === MARK + '岗');
    }
    check('O1 UI 建岗位→API可见', !!newPost, JSON.stringify(newPost || {}).slice(0, 80));
    // 页面自身列表刷出该岗位后才可删（onDelete 在 data.list 找不到会静默 return）
    await waitData(page, (d) => (d.list || []).some((p) => p.id === newPost.id), 6000, '岗位列表刷新');
    await mp.mockWxMethod('showModal', { confirm: true });
    await page.callMethod('onDelete', { currentTarget: { dataset: { id: newPost.id } } });
    await mp.restoreWxMethod('showModal');
    await sleep(1000);
    const postGone = !rows((await api('GET', '/posts?page_size=50')).data).some((p) => p.id === newPost.id);
    check('O1b UI 删岗位→API消失', postGone, '');

    // ===== V2 客户空名称拦截 + O6 客户分组 =====
    page = await goto(mp, '/packages/admin/customers/index');
    await waitData(page, (d) => (d.customers || []).length > 0 || d.loading === false, 8000, '客户列表');
    const cusCnt0 = rows((await api('GET', '/customers?page_size=1')).data).count;
    await page.callMethod('onAddCustomer');
    await page.callMethod('onSaveCustomer');
    await sleep(600);
    const cusCnt1 = rows((await api('GET', '/customers?page_size=1')).data).count;
    check('V2 客户空名称被拦截', cusCnt1 === cusCnt0, `before=${cusCnt0} after=${cusCnt1}`);

    await page.callMethod('onTab', { currentTarget: { dataset: { tab: 'groups' } } });
    await waitData(page, (d) => (d.groups || []).length > 0, 6000, '分组列表');
    await mp.mockWxMethod('showModal', { confirm: true, content: MARK + '组' });
    await page.callMethod('onAddGroup');
    await mp.restoreWxMethod('showModal');
    await sleep(1200); // 创建请求异步落库
    const grp = rows((await api('GET', '/customer-groups?page_size=100')).data).find((g) => g.name === MARK + '组');
    check('O6a UI 建分组→API可见', !!grp, '');
    if (grp) {
      const gd = await page.data();
      const gIdx = (gd.groups || []).findIndex((g) => g.id === grp.id);
      if (gIdx >= 0) {
        await mp.mockWxMethod('showActionSheet', { tapIndex: 1 }); // 1=删除分组
        await mp.mockWxMethod('showModal', { confirm: true });
        await page.callMethod('onEditGroup', { currentTarget: { dataset: { index: gIdx } } });
        await mp.restoreWxMethod('showModal');
        await mp.restoreWxMethod('showActionSheet');
        await sleep(1200);
      }
    }
    const grpGone = !rows((await api('GET', '/customer-groups?page_size=100')).data)
      .some((g) => (g.name || '') === MARK + '组');
    check('O6b UI 删分组→API消失', grpGone, '');

    // ===== V3 编菲表单校验（未选款式/非法床次/非法总量） =====
    page = await goto(mp, '/packages/admin/production-create/index');
    await waitData(page, (d) => d.stage === 'form' || d.stage === undefined, 6000, '编菲表单');
    await page.callMethod('onSaveForm'); // 未选款式
    let fd = await page.data();
    check('V3a 未选款式被拦截', (fd.stage || 'form') === 'form', `stage=${fd.stage}`);
    await page.callMethod('onOpenStylePanel');
    await waitData(page, (d) => (d.styleOptions || []).length > 0, 8000, '款式面板');
    const sOpts = (await page.data()).styleOptions;
    const mkIdx = sOpts.findIndex((s) => s.style_no === SNO);
    await page.callMethod('onPickStyle', { currentTarget: { dataset: { index: mkIdx } } });
    await waitData(page, (d) => d.style && d.style.style_no === SNO, 4000, '选中款式');
    await page.callMethod('onBedNo', { detail: { value: '12a' } });
    await page.callMethod('onTotalQty', { detail: { value: '100' } });
    await page.callMethod('onSaveForm');
    fd = await page.data();
    check('V3b 非法床次被拦截', (fd.stage || 'form') === 'form', `stage=${fd.stage}`);
    await page.callMethod('onBedNo', { detail: { value: '88' } });
    await page.callMethod('onTotalQty', { detail: { value: '3.5' } });
    await page.callMethod('onSaveForm');
    fd = await page.data();
    check('V3c 非法总量被拦截', (fd.stage || 'form') === 'form', `stage=${fd.stage}`);

    // ===== V4 工序单价非法值拦截 =====
    page = await goto(mp, `/packages/admin/style-process/index?styleId=${mk.data.id}&styleNo=${SNO}`);
    await waitData(page, (d) => (d.rows || []).length >= 0 && d.loading !== true, 6000, '工序单价页').catch(() => {});
    await page.callMethod('onPickProcess', { detail: { value: '0' } });
    await sleep(500);
    await page.callMethod('onPrice', { currentTarget: { dataset: { index: 0 } }, detail: { value: '-1' } });
    await page.callMethod('onSave');
    await sleep(800);
    const stDetail = (await api('GET', `/styles/${mk.data.id}`)).data;
    check('V4 工序单价非法被拦截', (stDetail.processes || []).length === 0,
      `processes=${JSON.stringify(stDetail.processes || []).slice(0, 80)}`);

    // ===== O7 款式编辑改名 =====
    page = await freshStylesPage(mp);
    await waitData(page, (d) => (d.styles || []).some((s) => s.style_no === SNO), 8000, '款式列表');
    let sd = await page.data();
    let mkIdx2 = sd.styles.findIndex((s) => s.style_no === SNO);
    await mp.mockWxMethod('showActionSheet', { tapIndex: 0 }); // 0=编辑款式
    await page.callMethod('onCardMenu', { currentTarget: { dataset: { index: mkIdx2 } } });
    await mp.restoreWxMethod('showActionSheet');
    await waitData(page, (d) => d.formVisible === true, 4000, '款式表单');
    await page.callMethod('onFormName', { detail: { value: MARK + '改名款' } });
    await page.callMethod('onSave');
    await sleep(1000);
    const renamed = (await api('GET', `/styles/${mk.data.id}`)).data;
    check('O7 UI 款式改名→API', renamed.name === MARK + '改名款', `name=${renamed.name}`);

    // ===== O8 款式复制 =====
    page = await freshStylesPage(mp);
    await waitData(page, (d) => (d.styles || []).some((s) => s.style_no === SNO), 8000, '款式列表2');
    sd = await page.data();
    mkIdx2 = sd.styles.findIndex((s) => s.style_no === SNO);
    await mp.mockWxMethod('showActionSheet', { tapIndex: 1 }); // 1=复制款式
    await page.callMethod('onCardMenu', { currentTarget: { dataset: { index: mkIdx2 } } });
    await mp.restoreWxMethod('showActionSheet');
    await sleep(1500);
    const copy = rows((await api('GET', `/styles?keyword=${SNO}&page_size=50`)).data)
      .find((s) => s.id !== mk.data.id);
    check('O8 UI 复制款式→API出现副本', !!copy, JSON.stringify(rows((await api('GET', `/styles?keyword=${SNO}&page_size=50`)).data).map((s) => s.style_no)));

    // ===== O9 图片上传（mock chooseMedia + uploadFile） =====
    await mp.mockWxMethod('chooseMedia', {
      tempFiles: [{ tempFilePath: 'http://tmp/ui_test.jpg' }], errMsg: 'chooseMedia:ok'
    });
    await mp.mockWxMethod('uploadFile', {
      statusCode: 200, data: JSON.stringify({ url: 'https://example.com/ui_test.jpg' }), errMsg: 'uploadFile:ok'
    });
    sd = await page.data();
    mkIdx2 = sd.styles.findIndex((s) => s.style_no === SNO);
    await mp.mockWxMethod('showActionSheet', { tapIndex: 0 });
    await page.callMethod('onCardMenu', { currentTarget: { dataset: { index: mkIdx2 } } });
    await mp.restoreWxMethod('showActionSheet');
    await waitData(page, (d) => d.formVisible === true, 4000, '款式表单2');
    await page.callMethod('onFormImage');
    await waitData(page, (d) => (d.form && d.form.image) || (d.form && d.form.imageUrl), 5000, '图片上传回显');
    await page.callMethod('onSave');
    await sleep(1000);
    const withImg = (await api('GET', `/styles/${mk.data.id}`)).data;
    check('O9 UI 图片上传→API image 落库', !!withImg.image && withImg.image.indexOf('ui_test.jpg') >= 0,
      `image=${withImg.image}`);

    // ===== O10 款式删除（无票靶款式） =====
    const tgt = await api('POST', '/styles', { style_no: 'UIMARKDEL', name: MARK + '删除靶' });
    page = await freshStylesPage(mp);
    await waitData(page, (d) => (d.styles || []).some((s) => s.style_no === 'UIMARKDEL'), 8000, '删除靶出现');
    sd = await page.data();
    const delIdx = sd.styles.findIndex((s) => s.style_no === 'UIMARKDEL');
    await mp.mockWxMethod('showActionSheet', { tapIndex: 3 }); // 3=款式删除
    await mp.mockWxMethod('showModal', { confirm: true });
    await page.callMethod('onCardMenu', { currentTarget: { dataset: { index: delIdx } } });
    await mp.restoreWxMethod('showModal');
    await mp.restoreWxMethod('showActionSheet');
    await sleep(1200);
    const delSt = await api('GET', '/styles/' + tgt.data.id);
    check('O10 UI 删款式→API 404', delSt.status === 404, `status=${delSt.status}`);

    // ===== O11 通知已读/全部已读 =====
    page = await mp.switchTab('/pages/notifications/index');
    await sleep(1500);
    await waitData(page, (d) => (d.items || []).length > 0, 8000, '通知列表');
    const unread0 = rows((await api('GET', '/notifications?is_read=0&page_size=50')).data);
    if (unread0.length > 0) {
      const nd = await page.data();
      const idx = nd.items.findIndex((it) => it.id === unread0[0].id && !it.is_read);
      if (idx >= 0) {
        await page.callMethod('onItemTap', { currentTarget: { dataset: { index: idx } } });
        await sleep(800);
        const oneRead = rows((await api('GET', '/notifications?page_size=50')).data)
          .find((n) => n.id === unread0[0].id);
        check('O11a UI 单条已读→API', !!oneRead && !!oneRead.is_read, `is_read=${oneRead && oneRead.is_read}`);
      } else {
        check('O11a UI 单条已读→API', true, 'skip: 首页无该未读(分页)');
      }
    } else {
      check('O11a UI 单条已读→API', true, 'skip: 无未读通知');
    }
    await page.callMethod('onMarkAll');
    await sleep(1000);
    const unread1 = rows((await api('GET', '/notifications?is_read=0&page_size=50')).data);
    check('O11b UI 全部已读→未读清零', unread1.length === 0, `unread=${unread1.length}`);

    // ===== O12 设置开关 =====
    page = await goto(mp, '/packages/admin/settings/index');
    await waitData(page, (d) => (d.rows || []).length > 0, 8000, '设置行');
    const key = 'filter_zero_tickets';
    const sv0 = (await api('GET', '/settings')).data;
    const to = sv0[key] === '1' ? '0' : '1';
    await page.callMethod('onToggle',
      { currentTarget: { dataset: { key } }, detail: { value: to === '1' } });
    await sleep(1000);
    const sv1 = (await api('GET', '/settings')).data;
    const flipped = sv1[key] === to;
    await page.callMethod('onToggle',
      { currentTarget: { dataset: { key } }, detail: { value: sv0[key] === '1' } });
    await sleep(1000);
    const sv2 = (await api('GET', '/settings')).data;
    check('O12 UI 设置开关→PUT /settings 生效并复原', flipped && sv2[key] === sv0[key],
      `before=${sv0[key]} flipped=${sv1[key]} restored=${sv2[key]}`);

    // ===== O13 协助登记：选员工→storage→扫码页协助模式→退出 =====
    page = await goto(mp, '/packages/admin/assist/index');
    await waitData(page, (d) => (d.emps || d.users || []).length > 0 || d.loading === false, 8000, '协助页').catch(() => {});
    await page.callMethod('onOpenPicker');
    await sleep(800);
    const pickerEl = await page.$('employee-picker'); // Element.$$ 可穿透自定义组件子树
    const epCells = (await pickerEl.$$('.ep-cell')) || [];
    let picked = false;
    for (const c of epCells) {
      const t = await c.text();
      if (t.indexOf('李翠花') >= 0) { await c.tap(); picked = true; break; }
    }
    check('O13-0 员工面板可见并选中李翠花', picked, `cells=${epCells.length}`);
    const confirmBtn = await pickerEl.$('.ep-confirm');
    if (confirmBtn) await confirmBtn.tap();
    await sleep(1500);
    const stash = await mp.evaluate(() => wx.getStorageSync('yisao_assist_target'));
    check('O13a 协助选员工→storage写入', stash && stash.userId === 8, JSON.stringify(stash));
    page = await mp.switchTab('/pages/scan/index');
    await sleep(1200);
    const scd = await page.data();
    check('O13b 扫菲页进入协助模式', scd.assistUserId === 8, `assistUserId=${scd.assistUserId}`);
    await page.callMethod('onExitAssist');
    await sleep(600);
    const stash2 = await mp.evaluate(() => wx.getStorageSync('yisao_assist_target'));
    const scd2 = await page.data();
    check('O13c 退出协助→清除', !stash2 && scd2.assistUserId === 0,
      `stash=${JSON.stringify(stash2)} uid=${scd2.assistUserId}`);

    // ===== V6 公司切换(同公司) / V7 加入不存在公司 =====
    page = await mp.switchTab('/pages/profile/index');
    await sleep(1200);
    const cid0 = await mp.evaluate(() => wx.getStorageSync('yisao_company_id'));
    await mp.mockWxMethod('showActionSheet', { tapIndex: 0 });
    await page.callMethod('onAction', { currentTarget: { dataset: { key: 'switch' } } });
    await mp.restoreWxMethod('showActionSheet');
    await sleep(1500);
    page = await mp.currentPage();
    const cid1 = await mp.evaluate(() => wx.getStorageSync('yisao_company_id'));
    check('V6 公司切换(仅一家)→公司不变', cid1 === cid0, `cid ${cid0}→${cid1}`);

    page = await mp.switchTab('/pages/profile/index');
    await sleep(800);
    const mineCnt0 = rows((await api('GET', '/companies/mine')).data).length;
    await mp.mockWxMethod('showModal', { confirm: true, content: '999999' });
    await page.callMethod('onAction', { currentTarget: { dataset: { key: 'join' } } });
    await mp.restoreWxMethod('showModal');
    await sleep(1500);
    const mineCnt1 = rows((await api('GET', '/companies/mine')).data).length;
    check('V7 加入不存在公司→mine不变', mineCnt1 === mineCnt0, `before=${mineCnt0} after=${mineCnt1}`);

    // ===== cleanup =====
    {
      // 临时用户/标记岗位/款式/客户/分组 ORM 硬删（软删会残留占名，见 preclean 注释）
      const py =
        "from apps.accounts.models import User, Post; " +
        "from apps.catalog.models import Style, Customer, CustomerGroup; " +
        "User.objects.filter(phone='" + OP_PHONE + "').delete(); " +
        "Post.all_objects.filter(name__startswith='" + MARK + "').delete(); " +
        "Style.all_objects.filter(style_no__startswith='" + SNO + "').delete(); " +
        "Customer.all_objects.filter(name__startswith='" + MARK + "').delete(); " +
        "CustomerGroup.all_objects.filter(name__startswith='" + MARK + "').delete(); " +
        "print('cleaned')";
      const out = execSync('.venv\\Scripts\\python.exe manage.py shell -c "' + py + '"', {
        cwd: 'D:\\project\\扫菲\\backend', env: { ...process.env, PYTHONIOENCODING: 'utf-8' }, encoding: 'utf-8'
      });
      check('cleanup 标记数据已清', out.includes('cleaned'), out.slice(-60));
    }
  } catch (e) {
    console.log('RUN ERROR:', e && e.stack || e);
    results.push({ name: 'RUN-COMPLETED', ok: false });
  } finally {
    const np = results.filter((r) => r.ok).length;
    console.log(`\n===== UI_OPS RESULT: ${np} passed, ${results.length - np} failed =====`);
    if (results.some((r) => !r.ok)) console.log('FAILED:', ...results.filter((r) => !r.ok).map((r) => r.name), '\n');
    process.exit(0);
  }
})();
