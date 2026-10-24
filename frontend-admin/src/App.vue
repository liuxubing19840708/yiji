<script setup>
/**
 * 看板模板配置端（P1 核心闭环）：
 *   导入客户 Excel 表头（两级/合并表头）→ 拖拽排序 + 逐列绑定
 *   （系统字段 / 公式 / 静态文本）→ 试跑预览 → 保存模板。
 * 认证：管理端 JWT（与小程序同套登录）+ X-Company-Id。
 */
import { ref, computed, onMounted } from 'vue';

const token = ref(localStorage.getItem('kt_token') || '');
const companyId = ref(localStorage.getItem('kt_cid') || '2');
const phone = ref('');           // 内置登录：手机号+密码直接换 token，免命令行
const password = ref('');
const loggingIn = ref(false);

async function doLogin() {
  err.value = ''; msg.value = '';
  if (!phone.value.trim() || !password.value) { err.value = '请输入手机号和密码'; return; }
  loggingIn.value = true;
  try {
    const resp = await fetch('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: phone.value.trim(), password: password.value })
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.access) throw new Error(data.message || `登录失败 ${resp.status}`);
    token.value = data.access;
    saveLocal();
    msg.value = '登录成功，已连接';
    await Promise.all([loadFields(), loadTemplates()]);
  } catch (e) { err.value = String(e.message || e); }
  finally { loggingIn.value = false; }
}

function logout() {
  token.value = '';
  localStorage.removeItem('kt_token');
}

const templates = ref([]);
const fields = ref([]);            // 可绑定字段字典
const currentId = ref(null);       // 正在编辑的模板 id（null=新建）
const tplName = ref('');
const isDefault = ref(false);
const excelInfo = ref(null);       // 导入结果 {header_row, two_level, columns}
const mappings = ref([]);          // [{title,parent,bind,field_key,expr,static_text,width,align,merge,group}]
const previewRows = ref(null);     // 试跑结果 {columns, groups}
const msg = ref('');
const err = ref('');
const busy = ref(false);

function saveLocal() {
  localStorage.setItem('kt_token', token.value);
  localStorage.setItem('kt_cid', companyId.value);
}

async function api(path, opts = {}) {
  const headers = { 'X-Company-Id': companyId.value, ...(opts.headers || {}) };
  if (token.value) headers.Authorization = `Bearer ${token.value}`;
  if (opts.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.json);
  }
  const resp = await fetch(`/api${path}`, { method: opts.method || 'GET', headers, body: opts.body });
  const text = await resp.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = {}; }
  if (!resp.ok) {
    const detail = typeof data === 'object' ? JSON.stringify(data) : String(data);
    throw new Error(`${resp.status} ${detail}`);
  }
  return data;
}

async function loadFields() {
  const d = await api('/kanban/fields');
  fields.value = d.results || [];
}

async function loadTemplates() {
  err.value = ''; msg.value = '';
  try {
    const d = await api('/kanban/templates');
    templates.value = d.results || [];
    msg.value = `已加载 ${templates.value.length} 套模板`;
  } catch (e) { err.value = String(e); }
}

function newTemplate() {
  currentId.value = null;
  tplName.value = '新模板';
  isDefault.value = false;
  excelInfo.value = null;
  mappings.value = defaultMappings();
  previewRows.value = null;
}

/** 字段下拉按类目分组（注册表 category 顺序即展示顺序） */
const fieldGroups = computed(() => {
  const order = [];
  const map = {};
  for (const f of fields.value) {
    const cat = f.category || '其它';
    if (!map[cat]) { map[cat] = []; order.push(cat); }
    map[cat].push(f);
  }
  return order.map((cat) => ({ category: cat, fields: map[cat] }));
});

/** 宽度输入显示：0 = 自适应显示为空串 */
const widthShow = (w) => (w === 0 || w === 'auto' ? '' : w);

/** 默认列 = 经典十列（不导入 Excel 也能配） */
function defaultMappings() {
  return [
    { title: '款号', parent: '', bind: 'field', field_key: 'style_no', expr: '', static_text: '', width: 160, align: 'left', merge: 'block' },
    { title: '颜色', parent: '', bind: 'field', field_key: 'color', expr: '', static_text: '', width: 110, align: 'left', merge: 'none' },
    { title: '订单数量', parent: '', bind: 'field', field_key: 'order_qty', expr: '', static_text: '', width: 120, align: 'right', merge: 'none' },
    { title: '床次', parent: '', bind: 'field', field_key: 'beds_text', expr: '', static_text: '', width: 160, align: 'left', merge: 'none' },
    { title: '已裁数量', parent: '', bind: 'field', field_key: 'cut_qty', expr: '', static_text: '', width: 120, align: 'right', merge: 'none' },
    { title: '已裁合计', parent: '', bind: 'field', field_key: 'cut_total', expr: '', static_text: '', width: 120, align: 'right', merge: 'block' },
    { title: '排单情况', parent: '', bind: 'field', field_key: 'schedule', expr: '', static_text: '', width: 110, align: 'center', merge: 'block' },
    { title: '下线数量', parent: '', bind: 'field', field_key: 'offline_qty', expr: '', static_text: '', width: 110, align: 'center', merge: 'block' },
    { title: '出货', parent: '', bind: 'field', field_key: 'ship_qty', expr: '', static_text: '', width: 110, align: 'center', merge: 'block' },
    { title: '尾部成品', parent: '', bind: 'field', field_key: 'tail_qty', expr: '', static_text: '', width: 110, align: 'center', merge: 'block' }
  ];
}

function editTemplate(t) {
  currentId.value = t.id;
  tplName.value = t.name;
  isDefault.value = !!t.is_default;
  excelInfo.value = null;
  previewRows.value = null;
  mappings.value = (t.columns || []).map((c) => {
    const w = c.extra && c.extra.width;
    return {
      title: c.title, parent: (c.extra && c.extra.group) || '',
      bind: c.bind_type, field_key: c.field_key || '',
      expr: c.expr || '', static_text: c.static_text || '',
      width: w === undefined ? 150 : widthShow(w),   // 0=自适应 → 显示空
      align: (c.extra && c.extra.align) || 'left',
      merge: (c.extra && c.extra.merge) || 'none'
    };
  });
  msg.value = `已载入模板「${t.name}」`;
}

async function onUpload(ev) {
  err.value = ''; msg.value = '';
  const file = ev.target.files && ev.target.files[0];
  if (!file) return;
  busy.value = true;
  try {
    const fd = new FormData();
    fd.append('file', file);
    const d = await api('/kanban/import-header', { method: 'POST', body: fd });
    excelInfo.value = d;
    // 导入后：默认把 Excel 列全部绑为静态文本（待人工映射），可逐列改绑字段/公式
    mappings.value = (d.columns || []).map((c) => ({
      title: c.parent ? `${c.parent}-${c.title}` : c.title,
      parent: c.parent || '',
      bind: 'static', field_key: '', expr: '',
      static_text: c.title, width: 130, align: 'left', merge: 'none'
    }));
    msg.value = `已解析表头（第 ${d.header_row} 行${d.two_level ? '，两级表头' : ''}，${d.columns.length} 列）。请为每列选择绑定方式。`;
  } catch (e) { err.value = String(e); } finally { busy.value = false; }
}

function addColumn() {
  mappings.value.push({ title: '新列', parent: '', bind: 'static', field_key: '', expr: '', static_text: '', width: 130, align: 'left', merge: 'none' });
}

function delColumn(i) { mappings.value.splice(i, 1); }

/** 上移/下移兜底排序（拖拽之外必定可用） */
function moveColumn(i, dir) {
  const j = i + dir;
  if (j < 0 || j >= mappings.value.length) return;
  const arr = mappings.value;
  [arr[i], arr[j]] = [arr[j], arr[i]];
}

/** 自实现拖拽（指针事件，不依赖 HTML5 DnD，任何浏览器可用）：
 *  按住把手 → 移动时按指针 Y 落点把行插入目标位置 → 松开结束 */
const dragIndex = ref(-1);
function onGripDown(e, i) {
  if (e.pointerType === 'mouse' && e.button !== 0) return;
  e.preventDefault();
  dragIndex.value = i;
  const onMove = (ev) => {
    const els = Array.from(document.querySelectorAll('.map-row'));
    if (!els.length) return;
    let insert = 0; // 插入位 = 指针越过中线的行数
    for (const el of els) {
      const r = el.getBoundingClientRect();
      if (ev.clientY > r.top + r.height / 2) insert += 1;
    }
    const cur = dragIndex.value;
    let ins = insert > cur ? insert - 1 : insert;
    if (ins === cur) return;
    const arr = mappings.value;
    const [it] = arr.splice(cur, 1);
    arr.splice(ins, 0, it);
    dragIndex.value = ins;
  };
  const onUp = () => {
    dragIndex.value = -1;
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    window.removeEventListener('pointercancel', onUp);
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
  window.addEventListener('pointercancel', onUp);
}

/** 宽度入库：空/0/auto → 0（自适应，渲染端按内容估宽）；数字 → 固定 rpx */
function widthSave(w) {
  if (w === '' || w === null || w === undefined) return 0;
  if (typeof w === 'string' && w.trim().toLowerCase() === 'auto') return 0;
  const n = Number(w);
  return Number.isFinite(n) && n > 0 ? Math.round(n) : 0;
}

function columnsPayload() {
  return mappings.value.map((m, i) => ({
    seq: i, title: m.title, bind_type: m.bind,
    field_key: m.bind === 'field' ? m.field_key : '',
    expr: m.bind === 'formula' ? m.expr : '',
    static_text: m.bind === 'static' ? m.static_text : '',
    extra: { merge: m.merge, width: widthSave(m.width), align: m.align,
             ...(m.parent ? { group: m.parent } : {}) }
  }));
}

async function doPreview() {
  err.value = ''; msg.value = ''; previewRows.value = null;
  busy.value = true;
  try {
    const d = await api('/kanban/preview', {
      method: 'POST',
      json: { columns: columnsPayload(), range: 'month' }
    });
    previewRows.value = d;
    msg.value = '试跑成功（本月数据）';
  } catch (e) { err.value = String(e); } finally { busy.value = false; }
}

async function doSave() {
  err.value = ''; msg.value = '';
  busy.value = true;
  try {
    const body = { name: tplName.value, is_default: isDefault.value, columns: columnsPayload() };
    const d = currentId.value
      ? await api(`/kanban/templates/${currentId.value}`, { method: 'PUT', json: body })
      : await api('/kanban/templates', { method: 'POST', json: body });
    currentId.value = d.id;
    msg.value = `已保存「${d.name}」(v${d.version})`;
    await loadTemplates();
  } catch (e) { err.value = String(e); } finally { busy.value = false; }
}

// 本机已存 token 时自动连接，免再点一次
onMounted(() => { if (token.value) { loadFields(); loadTemplates(); } });

const fieldLabel = (k) => {
  const f = fields.value.find((x) => x.key === k);
  return f ? f.label : k;
};

/** 试跑结果 → 真实 rowspan 行模型（预览与设备端合并观感一致：
 *  block=整块一格，value=连续同值一格，其余逐行） */
const previewRowsVM = computed(() => {
  if (!previewRows.value) return [];
  const cols = previewRows.value.columns || [];
  const out = [];
  for (const g of previewRows.value.groups || []) {
    for (const b of g.blocks || []) {
      const rows = b.rows.map((r) => cols.map((c, ci) => (r.cells[ci] || {}).text ?? ''));
      const n = rows.length;
      const spans = cols.map((c, ci) => {
        const s = new Array(n).fill(1);
        if (c.merge === 'block') {
          s.fill(0);
          if (n) s[0] = n;
        } else if (c.merge === 'value') {
          let i = 0;
          while (i < n) {
            let j = i + 1;
            while (j < n && rows[j][ci] === rows[i][ci] && rows[i][ci] !== '') j++;
            s[i] = j - i;
            for (let k = i + 1; k < j; k++) s[k] = 0;
            i = j;
          }
        }
        return s;
      });
      rows.forEach((r, ri) => {
        out.push({
          alt: b.alt,
          cells: cols.map((c, ci) => ({ text: r[ci], span: spans[ci][ri] }))
        });
      });
    }
  }
  return out;
});
</script>

<template>
  <h1>扫菲 · 生产看板模板配置</h1>

  <div v-if="!token" class="panel" style="max-width: 360px; margin: 24px auto">
    <h3 style="margin-top: 0">管理员登录</h3>
    <div class="bar" style="flex-direction: column; align-items: stretch; gap: 8px; box-shadow: none; padding: 0; background: transparent">
      <input v-model="phone" placeholder="手机号" @keyup.enter="doLogin" />
      <input v-model="password" type="password" placeholder="密码" @keyup.enter="doLogin" />
      <button :disabled="loggingIn" @click="doLogin">{{ loggingIn ? '登录中…' : '登录并进入配置' }}</button>
    </div>
    <div class="hint">与小程序同一套管理员账号（密码登录）。登录态保存在本机浏览器。</div>
  </div>

  <div v-else class="bar">
    <label>公司 ID</label>
    <input v-model="companyId" style="width: 70px" @change="saveLocal(); loadFields()" />
    <button class="ghost" @click="loadFields(); loadTemplates()">重新连接</button>
    <button class="ghost" @click="loadTemplates">刷新模板列表</button>
    <button class="ghost" @click="logout">退出</button>
    <span v-if="msg" class="ok">{{ msg }}</span>
  </div>
  <div v-if="err" class="err panel">{{ err }}</div>

  <div class="layout">
    <div class="panel left">
      <h3>模板列表</h3>
      <div v-for="t in templates" :key="t.id" class="tpl-item" :class="{ on: t.id === currentId }" @click="editTemplate(t)">
        <span>{{ t.name }}</span>
        <span v-if="t.is_default" class="tag">默认</span>
      </div>
      <button class="ghost" style="margin-top: 10px" @click="newTemplate">＋ 新建模板</button>
    </div>

    <div class="panel main">
      <div class="steps">
        <span class="step-chip" :class="{ on: excelInfo }">① 导入 Excel 表头</span>
        <span class="step-chip on">② 拖拽排序 + 绑定字段/公式</span>
        <span class="step-chip" :class="{ on: previewRows }">③ 试跑预览</span>
        <span class="step-chip" :class="{ on: currentId }">④ 保存模板</span>
      </div>

      <h3>① 导入客户 Excel 表头（支持两级/合并表头）</h3>
      <input type="file" accept=".xlsx,.xlsm" @change="onUpload" />
      <div v-if="excelInfo" class="hint">
        第 {{ excelInfo.header_row }} 行 · {{ excelInfo.two_level ? '两级表头' : '单级表头' }} · {{ excelInfo.columns.length }} 列
      </div>

      <h3>② 列定义（拖 ⠿ 或点 ▲▼ 调整列序）</h3>
      <div class="hint">
        绑定=系统字段（下拉按类目分组）；公式支持四则/比较/IF(...)/and/or/三元，字段直接写 key（如 cut_qty - order_qty）；
        merge=block 为随款号块整列合并；列宽留空 = 按内容自适应（渲染端估宽并均摊剩余屏宽）。
      </div>
      <div v-for="(m, i) in mappings" :key="i" class="map-row" :class="{ dragging: dragIndex === i }">
          <span class="grip" title="按住拖动可调整列顺序" @pointerdown="onGripDown($event, i)">⠿</span>
          <span class="mv" @click="moveColumn(i, -1)" title="上移">▲</span>
          <span class="mv" @click="moveColumn(i, 1)" title="下移">▼</span>
          <div class="xl-title">
            {{ m.title }}
            <small v-if="m.parent">{{ m.parent }}</small>
          </div>
          <select v-model="m.bind">
            <option value="field">字段</option>
            <option value="formula">公式</option>
            <option value="static">静态</option>
          </select>
          <select v-if="m.bind === 'field'" v-model="m.field_key">
            <optgroup v-for="grp in fieldGroups" :key="grp.category" :label="grp.category">
              <option v-for="f in grp.fields" :key="f.key" :value="f.key">{{ f.label }}（{{ f.key }}）</option>
            </optgroup>
          </select>
          <input v-if="m.bind === 'formula'" v-model="m.expr" class="expr" placeholder="如 round(cut_qty / cut_total * 100, 1)" />
          <input v-if="m.bind === 'static'" v-model="m.static_text" class="expr" placeholder="静态文本" />
          <input v-model="m.title" style="width: 120px" placeholder="列标题" />
          <input v-model="m.width" style="width: 64px" placeholder="自适应" title="列宽 rpx；留空 = 按内容自适应" />
          <select v-model="m.align" style="width: 78px">
            <option value="left">左</option><option value="right">右</option><option value="center">中</option>
          </select>
          <select v-model="m.merge" style="width: 96px">
            <option value="none">不合并</option><option value="block">随块合并</option><option value="value">同值合并</option>
          </select>
          <button class="del" @click="delColumn(i)">删</button>
        </div>
      <button class="ghost" @click="addColumn">＋ 加一列</button>

      <h3>③ 试跑 &amp; ④ 保存</h3>
      <div class="bar" style="box-shadow:none; padding:0; background:transparent">
        <input v-model="tplName" style="width: 200px" placeholder="模板名称" />
        <label><input type="checkbox" v-model="isDefault" /> 设为默认模板</label>
        <button :disabled="busy" @click="doPreview">试跑（本月）</button>
        <button :disabled="busy" @click="doSave">保存模板</button>
      </div>

      <div v-if="previewRows" style="overflow:auto; max-height: 480px">
        <table class="preview">
          <thead>
            <tr>
              <th v-for="c in previewRows.columns" :key="c.id"
                  :style="c.width > 0 ? 'min-width:' + Math.round(c.width / 2) + 'px' : ''">{{ c.title }}</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="(row, ri) in previewRowsVM" :key="ri">
              <tr :style="row.alt ? 'background:#f6f8fb' : ''">
                <template v-for="(cell, ci) in row.cells" :key="ci">
                  <td v-if="cell.span > 0" :rowspan="cell.span > 1 ? cell.span : undefined"
                      :class="{ num: previewRows.columns[ci].align === 'right' }"
                      :style="cell.span > 1 ? 'vertical-align:middle' : ''">
                    {{ cell.text }}
                  </td>
                </template>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
