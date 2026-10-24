/**
 * 工序 Tab（规格 §9.1）：
 * - 固定工序：全部工序列表 + is_fixed 开关（updateProcess），固定工序 ∪ 指定工序 = 扫菲可选
 * - 指定工序：本人 assigned_processes 展示（来自 /process-selector 中非固定项），修改入口指向员工管理
 * - 工序模板（F1/B1 契约，管理员可见）：GET/POST /process-templates + PUT/DELETE，
 *   用于建单页「关联工序」一键填充（名称 + 工序多选 + 每道工价）。
 *   后端 /process-templates 由并行 B1 开发中：404 时模板区降级提示，不白屏。
 */
import { requireLogin } from '../../utils/auth';
import { hasPerm } from '../../utils/perm';
import {
  createProcess, deleteProcess, fetchProcesses, fetchProcessSelector, updateProcess
} from '../../services/catalog';
import {
  fetchProcessTemplates, createProcessTemplate, updateProcessTemplate, deleteProcessTemplate
} from '../../services/process-template';
import { Process, ProcessTemplate } from '../../services/types';

/** 后端 ProcessSerializer 实际字段为 default_price（types.ts 写的是 price_default，以后端为准） */
type ProcRow = Process & { default_price?: string };

/** 模板列表 VM */
interface TplVM {
  id: number;
  name: string;
  count: number;
  /** N 道工序摘要（工序名顿号串，process_name 缺省时用工序列表映射兜底） */
  summary: string;
}

/** 模板弹层工序 chip */
interface TplChip {
  id: number;
  name: string;
  selected: boolean;
  price: string;
}

/** 模板弹层已选工序的工价输入行（引用 chip 下标） */
interface TplPriceRow {
  chipIdx: number;
  id: number;
  name: string;
  price: string;
}

Page({
  data: {
    loading: true,
    loadError: false,
    /** 全部工序（带开关） */
    list: [] as ProcRow[],
    /** 本人指定工序（/process-selector 中的非固定项） */
    assigned: [] as Array<{ id: number; name: string; price: string }>,
    savingId: 0,
    /** 管理员可新建工序（2026-10-18 需求：小程序内补上工序档案入口） */
    isAdmin: false,
    showAdd: false,
    addSaving: false,
    addName: '',
    addPrice: '',

    // ================= 工序模板（管理员可见） =================
    canManage: false,
    templates: [] as TplVM[],
    /** /process-templates 未就绪（404）：模板区降级提示 */
    tplDown: false,
    tplLoading: false,
    // ---- 模板新建/编辑弹层 ----
    showTpl: false,
    tplEditingId: 0,
    tplName: '',
    tplChips: [] as TplChip[],
    tplRows: [] as TplPriceRow[],
    tplSaving: false,
    // ---- 弹层键盘适配（新增模板窗口被输入法覆盖的修复） ----
    /** 当前键盘高度 px；0=收起。弹层滚动区据此补底部内边距 */
    kbHeight: 0,
    /** 聚焦输入框 id：scroll-into-view 滚到可见 */
    focusId: ''
  },

  onShow() {
    if (!requireLogin()) return;
    const tabBar = (this as unknown as { getTabBar?: () => { setSelected: (n: number) => void } }).getTabBar?.();
    if (tabBar) tabBar.setSelected(1);
    const isAdmin = !!getApp<IAppOption>().globalData.isAdmin;
    // 模板门控：is_admin 或「款式管理」权限点（与「＋新建工序」同族口径）
    this.setData({ isAdmin, canManage: isAdmin || hasPerm('styles.manage') });
    this.load();
    if (isAdmin || hasPerm('styles.manage')) {
      this.loadTemplates();
    }
    // 弹层键盘适配：监听键盘高度，滚动区补 padding，输入框不被键盘盖住
    // （input 关 adjust-position，页面不再被顶飞，由弹层内部自行让位）
    (this as unknown as { _kb?: (r: { height: number }) => void })._kb =
      this.onKbChange.bind(this);
    wx.onKeyboardHeightChange(
      (this as unknown as { _kb: (r: { height: number }) => void })._kb);
  },

  onHide() {
    const kb = (this as unknown as { _kb?: (r: { height: number }) => void })._kb;
    if (kb) wx.offKeyboardHeightChange(kb);
    if (this.data.kbHeight) this.setData({ kbHeight: 0 });
  },

  onUnload() {
    const kb = (this as unknown as { _kb?: (r: { height: number }) => void })._kb;
    if (kb) wx.offKeyboardHeightChange(kb);
  },

  onKbChange(res: { height: number }) {
    this.setData({ kbHeight: res.height || 0 });
    if (!res.height) this.setData({ focusId: '' });
  },

  /** 输入框聚焦：记录 id 供 scroll-into-view 滚到可见 */
  onFieldFocus(e: WechatMiniprogram.TouchEvent) {
    const id = String((e.currentTarget as unknown as { id: string }).id || '');
    if (id) this.setData({ focusId: id });
  },

  async load() {
    this.setData({ loading: true, loadError: false });
    try {
      const [procRes, selRes] = await Promise.all([
        // 兼容分页 / 数组两种返回形态（后端 GET /processes 走分页）
        fetchProcesses() as unknown as ProcRow[] | { results?: ProcRow[] },
        fetchProcessSelector() as unknown as
          | Array<{ id: number; name: string; price: string; is_fixed: boolean }>
          | { results?: Array<{ id: number; name: string; price: string; is_fixed: boolean }> }
      ]);
      const procs = Array.isArray(procRes) ? procRes : (procRes.results || []);
      const sel = Array.isArray(selRes) ? selRes : (selRes.results || []);
      procs.sort((a, b) => (a.sort || 0) - (b.sort || 0) || a.id - b.id);
      this.setData({
        list: procs,
        assigned: sel.filter((s) => !s.is_fixed),
        loading: false
      });
    } catch (e) {
      this.setData({ loading: false, loadError: true });
    }
  },

  onRetry() {
    this.load();
    if (this.data.canManage) this.loadTemplates();
  },

  // ============================================================
  // 工序模板（管理员）：列表 + 新建/编辑/删除
  // ============================================================

  async loadTemplates() {
    this.setData({ tplLoading: true });
    try {
      const res = await fetchProcessTemplates() as unknown as ProcessTemplate[] | { results?: ProcessTemplate[] };
      const list = Array.isArray(res) ? res : (res.results || []);
      this._rawTemplates = list;
      this.setData({ templates: list.map((t) => this.toTplVM(t)), tplDown: false, tplLoading: false });
    } catch (e) {
      // B1 未就绪（404）或网络失败：降级提示，不阻塞本页其它区块
      this.setData({ tplLoading: false, templates: [], tplDown: true });
    }
  },

  /** 模板 → 列表 VM：摘要=前 4 道工序名顿号串（process_name 缺省用工序列表映射） */
  toTplVM(t: ProcessTemplate): TplVM {
    const nameMap: Record<number, string> = {};
    this.data.list.forEach((p) => { nameMap[p.id] = p.name; });
    const items = t.items || [];
    const names = items.map((it) => it.process_name || nameMap[it.process_id] || `工序${it.process_id}`);
    const shown = names.slice(0, 4).join('、');
    return {
      id: t.id,
      name: t.name,
      count: items.length,
      summary: names.length
        ? (names.length > 4 ? `${shown} 等 ${names.length} 道工序` : shown)
        : '空模板'
    };
  },

  /** 打开弹层（新建：editingId=0；点模板行：编辑预填） */
  onOpenTpl(tpl?: ProcessTemplate) {
    const chips: TplChip[] = this.data.list.map((p) => ({
      id: p.id,
      name: p.name,
      selected: false,
      price: ''
    }));
    if (tpl) {
      (tpl.items || []).forEach((it) => {
        const chip = chips.find((c) => c.id === it.process_id);
        if (chip) {
          chip.selected = true;
          chip.price = String(it.price || '');
        }
      });
    }
    this.setData({
      showTpl: true,
      tplEditingId: tpl ? tpl.id : 0,
      tplName: tpl ? tpl.name : '',
      tplChips: chips,
      tplRows: chips.filter((c) => c.selected).map((c, i) => ({ chipIdx: i, id: c.id, name: c.name, price: c.price }))
    });
  },

  onAddTemplate() {
    if (!this.data.list.length) {
      wx.showToast({ title: '请先创建工序，再建模板', icon: 'none' });
      return;
    }
    this.onOpenTpl();
  },

  onTapTemplate(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    // 从原始模板回填（列表 VM 只有摘要）
    const raw = this._rawTemplates.find((t) => t.id === id);
    if (!raw) return;
    this.onOpenTpl(raw);
  },

  /** 原始模板缓存（列表 VM 不含 items 全量） */
  _rawTemplates: [] as ProcessTemplate[],

  async onDeleteTemplate(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const vm = this.data.templates.find((t) => t.id === id);
    if (!vm) return;
    const res = await wx.showModal({
      title: '删除模板',
      content: `确定删除模板「${vm.name}」？不影响已创建的订单。`,
      confirmText: '删除',
      confirmColor: '#e64340'
    });
    if (!res.confirm) return;
    try {
      await deleteProcessTemplate(id);
      wx.showToast({ title: '已删除', icon: 'success' });
      this.loadTemplates();
    } catch (err) {
      // 错误 toast 已由请求层弹出
    }
  },

  // ---- 模板弹层交互（取消/保存在标题行，键盘弹出不遮挡——项目既有教训） ----
  onCloseTpl() {
    if (this.data.tplSaving) return;
    this.setData({ showTpl: false });
  },
  stopBubble() {
    // 阻止冒泡关弹层
  },
  onTplName(e: WechatMiniprogram.Input) {
    this.setData({ tplName: e.detail.value });
  },
  onTplToggleChip(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const chip = this.data.tplChips[idx];
    if (!chip) return;
    const selected = !chip.selected;
    // 默认带工序档案价（default_price），可改
    const defPrice = String((this.data.list.find((p) => p.id === chip.id) as ProcRow | undefined)?.default_price || '');
    this.setData({
      [`tplChips[${idx}].selected`]: selected,
      [`tplChips[${idx}].price`]: selected && !chip.price ? defPrice : chip.price
    });
    this.syncTplRows();
  },
  onTplPrice(e: WechatMiniprogram.Input) {
    const chipIdx = Number(e.currentTarget.dataset.chip);
    this.setData({ [`tplChips[${chipIdx}].price`]: e.detail.value });
    this.syncTplRows();
  },
  /** 已选工序 → 工价输入行（保持与 chips 同步） */
  syncTplRows() {
    const rows: TplPriceRow[] = [];
    this.data.tplChips.forEach((c, i) => {
      if (c.selected) rows.push({ chipIdx: i, id: c.id, name: c.name, price: c.price });
    });
    this.setData({ tplRows: rows });
  },
  async onConfirmTpl() {
    if (this.data.tplSaving) return;
    const name = this.data.tplName.trim();
    if (!name) {
      wx.showToast({ title: '请输入模板名称', icon: 'none' });
      return;
    }
    const items = this.data.tplChips
      .filter((c) => c.selected)
      .map((c) => ({ process_id: c.id, price: c.price.trim() || '0' }));
    if (!items.length) {
      wx.showToast({ title: '请至少选择一道工序', icon: 'none' });
      return;
    }
    for (const it of items) {
      if (Number.isNaN(Number(it.price)) || Number(it.price) < 0) {
        wx.showToast({ title: '工价须为非负数字', icon: 'none' });
        return;
      }
    }
    this.setData({ tplSaving: true });
    try {
      if (this.data.tplEditingId) {
        await updateProcessTemplate(this.data.tplEditingId, { name, items });
      } else {
        await createProcessTemplate({ name, items });
      }
      this.setData({ showTpl: false });
      wx.showToast({ title: '模板已保存', icon: 'success' });
      this.loadTemplates();
    } catch (err) {
      // 400（重名等）「字段名：原因」由 http 层 toast
    } finally {
      this.setData({ tplSaving: false });
    }
  },

  // ============================================================
  // 工序档案（原有）
  // ============================================================

  /** 固定工序开关（自定义 toggle 点按）：乐观更新，失败回滚（后端 PUT 全量校验，补齐原字段提交） */
  async onToggleFixed(e: WechatMiniprogram.TouchEvent) {
    if (this.data.savingId) return;
    const id = Number(e.currentTarget.dataset.id);
    const row = this.data.list.find((p) => p.id === id);
    if (!row) return;
    const next = !row.is_fixed;
    const idx = this.data.list.indexOf(row);
    this.setData({ savingId: id, [`list[${idx}].is_fixed`]: next });
    try {
      await updateProcess(id, {
        ...(row as unknown as Record<string, unknown>),
        is_fixed: next
      } as Partial<Process>);
      // 固定开关影响扫菲可选集，刷新指定工序展示
      this.load();
    } catch (err) {
      this.setData({ [`list[${idx}].is_fixed`]: !next });
    } finally {
      this.setData({ savingId: 0 });
    }
  },

  /** 指定工序的配置入口：跳指定工序页（员工维度多选管理） */
  goAssign() {
    wx.navigateTo({
      url: '/packages/admin/assigned/index',
      fail: () => {
        wx.showToast({ title: '指定工序页面尚未开放', icon: 'none' });
      }
    });
  },

  // ---- 管理员删除工序（长按行；DELETE /processes/:id，被款式使用/有登记记录时后端 400） ----
  async onDeleteProcess(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.isAdmin || this.data.savingId) return;
    const id = Number(e.currentTarget.dataset.id);
    const row = this.data.list.find((p) => p.id === id);
    if (!row) return;
    const res = await wx.showModal({
      title: '删除工序',
      content: `确定删除「${row.name}」？被款式使用或已有登记记录的工序无法删除`,
      confirmText: '删除',
      confirmColor: '#e64340'
    });
    if (!res.confirm) return;
    try {
      await deleteProcess(id);
      wx.showToast({ title: '已删除', icon: 'success' });
      this.load();
    } catch (err) {
      // 400（被款式使用/有登记记录）由 http 层 toast 具体原因
    }
  },

  // ---- 管理员新建工序（POST /processes，后端需「款式管理」权限点） ----
  onOpenAdd() {
    this.setData({ showAdd: true, addName: '', addPrice: '' });
  },
  onCloseAdd() {
    if (this.data.addSaving) return;
    this.setData({ showAdd: false });
  },
  onAddName(e: WechatMiniprogram.Input) {
    this.setData({ addName: e.detail.value });
  },
  onAddPrice(e: WechatMiniprogram.Input) {
    this.setData({ addPrice: e.detail.value });
  },
  async onConfirmAdd() {
    if (this.data.addSaving) return;
    const name = this.data.addName.trim();
    if (!name) {
      wx.showToast({ title: '请输入工序名称', icon: 'none' });
      return;
    }
    const price = this.data.addPrice.trim();
    if (price && isNaN(Number(price))) {
      wx.showToast({ title: '默认单价须为数字', icon: 'none' });
      return;
    }
    this.setData({ addSaving: true });
    try {
      await createProcess({
        name,
        default_price: price ? Number(price).toFixed(3) : '0.000',
        is_fixed: false,
        sort: this.data.list.length + 1
      } as Partial<Process>);
      this.setData({ showAdd: false });
      wx.showToast({ title: '工序已创建', icon: 'success' });
      this.load();
    } catch (err) {
      // 400（重名等）由 http 层 toast 具体原因
    } finally {
      this.setData({ addSaving: false });
    }
  }
});
