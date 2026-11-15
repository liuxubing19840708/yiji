/**
 * 建床向导（重设计 §2.6.1，2026-10-23 再精简为 2 步）：
 *   ① 选单选款：挂单可选（订单入口带客户+色码余量，可跳过）+ 选款式（必选）；
 *      ——订单入口（带 order_id）直接进生成扎，不再经过本步（款式随订单带出）；
 *   ② 生成扎：颜色×尺码矩阵（历史颜色/尺码点选+自定义，视图=横向/纵向/倍数/排列/扎号，
 *      匹号与物料已去掉 2026-10-21）+ 起始扎号/每扎上限 +
 *      实时尾扎预览「整除 N 扎+尾扎 M 件」；挂单余量自动预填，超订单余量弹「确认继续？」。
 *      ——裁剪计划环节已去掉（2026-10-23 用户拍板）：订单类型/客户/面料以订单页为准
 *      （订单类型默认自产、客户随订单、面料留空），床次号自动顺延（最近一床 +1），
 *      裁完标记在床次详情人工操作；
 *      ——工序工价环节已去掉（2026-10-21）：工序在床次详情配置。
 * 成功页：落床次详情入口 + 引导打印菲票（label-preview，可跳过）。
 * 保存契约不变：createTicketsBatch（POST /tickets/batch）items + bundle_limit + start_bundle_no；
 * 建床推迟到保存时（总量 0，编菲 batch 后端按菲票汇总重算）。
 * 离开拦截选型见旧注释（enableAlertBeforeUnload 仅拦返回键/手势，脏数据时开启）。
 */
import { requireLogin, getCompanyId } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import {
  createProduction, fetchProduction, fetchProductionColors, fetchProductionHints,
  fetchProductions
} from '../../../services/production';
import { createTicketsBatch } from '../../../services/catalog';
import { fetchStyles, fetchCustomers } from '../../../services/catalog';
import { fetchOrder, fetchOrders, fetchOrderProgress } from '../../../services/orders';
import { startPrintFlow, cancelPrintFlow } from '../../../utils/print-flow';
import { ApiError } from '../../../utils/request';
import { PrintTicketData } from '../../../utils/bluetooth/templates';
import { Style, Customer, Paginated, Production, Order } from '../../../services/types';

const PAGE_SIZE = 20;

/** 内置常用服装色（调研痛点：颜色手写繁琐）——排在厂内历史颜色之后兜底 */
const COMMON_COLORS = ['黑色', '白色', '灰色', '红色', '蓝色', '绿色', '黄色',
  '橙色', '紫色', '粉色', '棕色', '咖啡', '军绿', '卡其', '米色', '花色'];

/** 内置常用尺码池（成人装 + 童装码 + 均码），排在厂内历史尺码之后兜底 */
const SIZE_POOL = ['S', 'M', 'L', 'XL', 'XXL', '3XL', '4XL', 'F', '均码',
  '80', '90', '95', '100', '110', '120', '130', '140', '150', '160'];

/** 扎号参数记忆键：按厂记住上次用的每扎上限/起始扎号（下次编菲直接带出） */
const BUNDLE_PREF_KEY = 'yisao_bundle_pref';

/** 视图页签（2026-10-21 用户拍板：匹号与物料下线，编菲不再收集面料批次文本） */
const VIEWS = [
  { key: 'h', name: '横向' },
  { key: 'v', name: '纵向' },
  { key: 'multi', name: '倍数' },
  { key: 'array', name: '排列' },
  { key: 'bundle', name: '扎号' }
];

/** 向导步骤展示（5=成功页）。内部步号不变：1=选单选款、4=生成扎（历史兼容，
 *  3 曾为工序工价、2 曾为裁剪计划，两环节均已去掉） */
const WIZ_STEPS = [
  { no: 1, name: '选单选款' },
  { no: 2, name: '生成扎' }
];

/** 数字键盘弹层用途 */
type SheetMode = 'cell' | 'count' | 'base' | 'array' | 'limit' | 'start' | 'tail';

/** 矩阵单元格 VM（横向/纵向/倍数/排列四视图共用一套表格结构） */
interface MtxCell {
  key: string;
  main: string;
  sub: string;
  editable: boolean;
}
interface MtxRow {
  label: string;
  cells: MtxCell[];
  total: number;
}
/** 扎号预览行 */
interface BundleRow {
  no: string;
  color: string;
  size: string;
  qty: number;
  /** 尾扎（可微调件数） */
  isTail: boolean;
  cellKey: string;
}
Page({
  data: {
    // ================= 向导步骤 =================
    steps: WIZ_STEPS,
    step: 1 as 1 | 2 | 3 | 4 | 5,
    saving: false,

    // ================= 挂单（第 1 步，可选） =================
    order: null as Order | null,
    /** 订单余量 cells[`${color}|${size}`]（进入第 4 步自动预填；含余 0 格供校验区分） */
    orderRemaining: {} as Record<string, number>,
    orderRemainingTotal: 0,
    /** 余量是否拉取成功（失败时校验③跳过，不阻塞开床） */
    orderRemainingReady: false,
    /** 订单色码清单（挂单建床校验用，2026-10-22；items 为空的订单不约束） */
    orderColors: [] as string[],
    orderSizes: [] as string[],
    /** 挂单选择面板 */
    orderPanelVisible: false,
    orderOptions: [] as Order[],
    orderLoading: false,

    // ================= 款式（第 1 步，必选） =================
    style: null as Style | null,
    stylePanelVisible: false,
    styleKeyword: '',
    styleOptions: [] as Style[],
    styleLoading: false,
    styleError: false,

    // ================= 裁剪计划（第 2 步） =================
    bedNo: '',
    orderTypes: ['自产', '外发'],
    orderTypeIndex: 0,
    customers: [] as Customer[],
    customerNames: ['不指定客户'] as string[],
    customerIndex: 0,
    customer: 0,
    sourceOrderNo: '', // 来源单号/客户订单号（质量追溯 P0，选填；床次详情可补填改）
    fabric: '',


    // ================= 生成扎（第 4 步，编菲矩阵） =================
    prodId: 0,
    prodBedNo: '',
    prodStyleNo: '',
    prodStyleName: '',
    colors: [] as string[],
    sizes: [] as string[],
    /** cells[`${color}|${size}`] = 件数（>0 才存） */
    cells: {} as Record<string, number>,
    views: VIEWS,
    view: 'h',
    // 倍数视图：基数 × 每格份数
    multiBase: '',
    multiCounts: {} as Record<string, number>,
    // 排列视图：每格件数
    arrayQty: '',
    // 扎号视图：起始扎号 + 每扎上限
    startBundleNo: '1',
    bundleLimit: '20',
    bundleRows: [] as BundleRow[],
    bundleTotal: 0,
    /** 实时尾扎预览：整除 N 扎 + 尾扎 M 件（输入每扎上限后显示） */
    tailSummary: '',
    // 颜色点选面板（厂内常用色 + 内置常用色，点选代替手写）
    colorPanelVisible: false,
    colorOptions: [] as Array<{ name: string; added: boolean }>,
    // 尺码点选面板（厂内历史尺码 + 内置尺码池，点选代替手写）
    sizePanelVisible: false,
    sizeOptions: [] as Array<{ name: string; added: boolean }>,
    // 矩阵渲染 VM
    mtxCols: [] as string[],
    mtxRows: [] as MtxRow[],
    mtxEditable: true,
    grandTotal: 0,
    // 数字键盘弹层
    sheetVisible: false,
    sheetMode: 'cell' as SheetMode,
    sheetTitle: '',
    sheetValue: '',
    sheetKey: '',
    /** tail 模式：该格整扎件数（floor(qty/limit)），微调重算用 */
    sheetTailFull: 0,

    // ================= 成功页（第 5 步） =================
    createdCount: 0,
    previewShow: false,
    previewTickets: [] as PrintTicketData[],
    /** 打印门槛（H4）：成功页「打印菲票」按钮按 print.manage ∪ production.manage 显隐 */
    canPrint: false,
    previewCompany: { name: '' } as { name?: string },
    /** 打印流程状态（print-progress 组件，utils/print-flow 驱动） */
    printFlow: { show: false, done: 0, total: 0 }
  },

  /** 离开拦截是否已开启（enableAlertBeforeUnload 重复调用会反复弹，做开关缓存） */
  _guardOn: false,
  /** 最近一次成功保存时的输入快照：hasUnsaved = 当前输入 ≠ 该快照（保存后同态=不脏） */
  _savedSnap: '',
  /** 表单页减负提示（床次号预填/面料历史）只拉一次 */
  _formHintsLoaded: false,
  /** 已创建的床次 id（防返回第 2 步重复建床） */
  _prodCreated: 0,

  onLoad(options: Record<string, string>) {
    this.setData({ canPrint: hasPerm('print.manage') || hasPerm('production.manage') });
    // 订单入口（2026-10-23 用户拍板）：直接进生成扎页，不再经过选单/裁剪计划；
    // 订单加载失败或老订单未关联款式时留在第 1 步人工补选
    if (options.order_id) {
      this.loadOrderContext(Number(options.order_id))
        .then(() => this.enterMatrixFromContext());
    }
    // 支持从款式卡片「编菲」入口带款式进入
    if (options.styleId) {
      this.fetchStyleDetail(Number(options.styleId));
    }
    // 支持从床次带 production_id 直达生成扎（如床次详情后续接入）
    if (options.production_id) {
      this.enterMatrixWithProduction(Number(options.production_id));
    }
  },

  onShow() {
    if (!requireLogin()) return;
    if (this.data.step === 1) {
      this.loadCustomers();
      this.loadFormHints();
    }
  },

  onUnload() {
    this._guardOn = false;
  },

  // ============================================================
  // 向导导航（每步校验不过禁下一步）
  // ============================================================

  goNext() {
    const { step } = this.data;
    if (step === 1) this.goMatrix();
  },

  goPrev() {
    // 生成扎回第 1 步（裁剪计划环节已去掉）；离开编菲时关闭脏数据拦截
    if (this.data.step === 4) {
      if (this._guardOn && typeof wx.disableAlertBeforeUnload === 'function') {
        wx.disableAlertBeforeUnload({});
        this._guardOn = false;
      }
      this.setData({ step: 1, sheetVisible: false });
    }
  },

  // ============================================================
  // 第 1 步：选单选款
  // ============================================================

  /** 挂单进入：拉订单 + 色码余量（失败降级为无单，不阻塞建床）。
   *  返回订单就位后的 Promise（订单入口直达生成扎依赖它串接） */
  loadOrderContext(orderId: number): Promise<void> {
    // 换单先失效旧余量（防止上一单数据误拦/误预填）
    this.setData({ orderRemaining: {}, orderRemainingTotal: 0, orderRemainingReady: false });
    // 客户列表先就位再拉订单，保证客户 picker 索引能算对
    return this.loadCustomers()
      .then(() => fetchOrder(orderId))
      .then((o: Order) => {
        this.setData({ order: o });
        // 客户自动带出（保存时随 payload 落库）
        if (o.customer) {
          const idx = this.data.customers.findIndex((c) => c.id === o.customer);
          if (idx >= 0) this.setData({ customerIndex: idx + 1, customer: o.customer });
        }
        // 订单色码清单（2026-10-22 用户需求：建床校验颜色/尺码须在订单内）
        const orderColors: string[] = [];
        const orderSizes: string[] = [];
        (o.items || []).forEach((it) => {
          if (it.color && orderColors.indexOf(it.color) < 0) orderColors.push(it.color);
          if (it.size && orderSizes.indexOf(it.size) < 0) orderSizes.push(it.size);
        });
        this.setData({ orderColors, orderSizes });
      })
      .catch(() => {
        wx.showToast({ title: '订单加载失败，将按无单开床', icon: 'none' });
      });
    fetchOrderProgress(orderId)
      .then((res) => {
        const remaining: Record<string, number> = {};
        let total = 0;
        // SA1 实际下发 items[]；cells 为旧格式兜底（字段名 qty vs order_qty）
        const rows = ((res && res.items) || (res && res.cells) || []) as Array<{
          color: unknown; size: unknown; qty?: unknown; order_qty?: unknown;
          cut_qty?: unknown; registered_qty?: unknown;
        }>;
        rows.forEach((c) => {
          const ordered = Number(c.qty !== undefined ? c.qty : c.order_qty) || 0;
          // 开床余量口径（2026-10-22 用户反馈）：订单数 − 已裁（挂单床次菲票聚合）。
          // 不用已登记数——登记是扫菲进度，第一次开床裁完但未扫菲时余量仍是整单，
          // 造成「二次建床自动填满 + 多床累计超订单不提示」两个问题。
          const cut = Number(c.cut_qty) || 0;
          const left = Math.max(ordered - cut, 0);
          // 全量保留（含 0）：校验②靠 left===undefined 区分「无数据」与「余 0」
          remaining[`${c.color}|${c.size}`] = left;
          if (left > 0) total += left;
        });
        this.setData({ orderRemaining: remaining, orderRemainingTotal: total, orderRemainingReady: true });
      })
      .catch(() => { /* 余量拉取失败：第 4 步不预填，校验③跳过（不阻塞开床） */ });
  },

  /** 「不挂单」：跳过订单，走散单/翻单路径 */
  onSkipOrder() {
    this.setData({ order: null, orderRemaining: {}, orderRemainingTotal: 0, orderRemainingReady: false, orderColors: [], orderSizes: [], orderPanelVisible: false });
  },

  onOpenOrderPanel() {
    this.setData({ orderPanelVisible: true, orderOptions: [], orderLoading: true });
    fetchOrders({ status: 'producing', page: 1, page_size: PAGE_SIZE })
      .then((res: Paginated<Order>) => {
        this.setData({ orderOptions: res.results || [], orderLoading: false });
      })
      .catch(() => {
        this.setData({ orderLoading: false });
        wx.showToast({ title: '订单服务未就绪，可先不挂单', icon: 'none' });
      });
  },

  onCloseOrderPanel() {
    this.setData({ orderPanelVisible: false });
  },

  onPickOrder(e: WechatMiniprogram.TouchEvent) {
    const o = this.data.orderOptions[Number(e.currentTarget.dataset.index)];
    if (!o) return;
    this.setData({ order: o, orderPanelVisible: false });
    // 换单重拉余量 + 带客户
    this.loadOrderContext(o.id);
  },

  // ============ 款式选择 ============

  onOpenStylePanel() {
    this.setData({ stylePanelVisible: true, styleKeyword: '', styleOptions: [] });
    this.searchStyles('');
  },

  onCloseStylePanel() {
    this.setData({ stylePanelVisible: false });
  },

  noop() { /* 阻止冒泡 */ },

  onStyleKeyword(e: WechatMiniprogram.Input) {
    this.setData({ styleKeyword: e.detail.value });
  },

  onStyleSearch() {
    this.searchStyles(this.data.styleKeyword.trim());
  },

  searchStyles(kw: string) {
    const params: Record<string, unknown> = { page: 1, page_size: PAGE_SIZE };
    if (kw) params.keyword = kw;
    this.setData({ styleLoading: true, styleError: false });
    fetchStyles(params as Parameters<typeof fetchStyles>[0])
      .then((res: Paginated<Style>) => {
        this.setData({ styleOptions: res.results || [], styleLoading: false });
      })
      .catch(() => this.setData({ styleLoading: false, styleError: true }));
  },

  onPickStyle(e: WechatMiniprogram.TouchEvent) {
    const s = this.data.styleOptions[Number(e.currentTarget.dataset.index)];
    if (!s) return;
    // 款式面料作默认（2026-10-21 款式面料字段）：表单已填则不覆盖
    this.setData({ style: s, stylePanelVisible: false, fabric: this.data.fabric || s.fabric || '' });
  },

  fetchStyleDetail(id: number) {
    // 预选款式：后端 styles 列表暂无 id 过滤参数（services 亦未声明），拉首页按 id 匹配
    fetchStyles({ page: 1, page_size: 50 })
      .then((res: Paginated<Style>) => {
        const hit = (res.results || []).find((s) => s.id === id) || null;
        if (hit) this.setData({ style: hit, fabric: this.data.fabric || hit.fabric || '' });
      })
      .catch(() => { /* 预选失败不阻塞 */ });
  },

  // ============ 第 2 步：裁剪计划表单 ============

  /** 拉客户下拉（失败 resolve 空表，不阻塞建床） */
  loadCustomers(): Promise<Customer[]> {
    return new Promise((resolve) => {
      fetchCustomers({ page: 1, page_size: 100 })
        .then((res) => {
          const customers = res.results || [];
          this.setData({
            customers,
            customerNames: ['不指定客户'].concat(customers.map((c) => c.name))
          });
          resolve(customers);
        })
        .catch(() => resolve([])); /* 客户下拉失败不阻塞建床 */
    });
  },

  /** 床次号预填：取最近一床的数字床次号 +1（用户已输入则不打扰；失败不阻塞，
   *  保存时空床次号有兜底拦截） */
  prefillBedNo(): Promise<void> {
    if (this.data.bedNo) return Promise.resolve();
    return fetchProductions({ page: 1, page_size: 1 })
      .then((res) => {
        const last = (res.results || [])[0];
        const lastBed = last ? String(last.bed_no || '') : '';
        if (lastBed && /^\d+$/.test(lastBed) && !this.data.bedNo) {
          this.setData({ bedNo: String(Number(lastBed) + 1) });
        }
      })
      .catch(() => { /* 预填失败不阻塞 */ });
  },

  /** 表单页减负：床次号预填（最近一床 +1，失败静默不阻塞） */
  loadFormHints() {
    if (this._formHintsLoaded) return;
    this._formHintsLoaded = true;
    this.prefillBedNo();
  },

  /** 第 1 步 → 生成扎（裁剪计划环节已去掉，2026-10-23 用户拍板）：
   *  款式必选——挂单由订单带出，老订单没关联款式时补选一次，无单路径必须选；
   *  床次号自动顺延；订单类型/客户/面料取默认（随订单/自产/空），保存时同批落库 */
  goMatrix() {
    const orderStyleless = !!this.data.order && !this.data.order.style;
    if ((!this.data.order || orderStyleless) && !this.data.style) {
      wx.showToast({ title: orderStyleless ? '该订单未关联款式，请补选' : '请选择款式', icon: 'none' });
      return;
    }
    // 床次已创建过（从生成扎返回再前进）：不重复建床上下文，直接回矩阵
    if (this._prodCreated) {
      this.enterMatrixWithId(this.data.prodId);
      return;
    }
    this.prefillBedNo().then(() => {
      this.applyProdContext();
      this.enterMatrixWithId(0);
    });
  },

  /** 订单入口直达生成扎：订单/款式就位即进（老订单未关联款式留在第 1 步补选） */
  enterMatrixFromContext() {
    const o = this.data.order;
    if (!o || !o.style) return;
    // 床次已创建过（理论不发生：onLoad 一次性路径）：防御性直回矩阵
    if (this._prodCreated) {
      this.enterMatrixWithId(this.data.prodId);
      return;
    }
    this.prefillBedNo().then(() => {
      this.applyProdContext();
      this.enterMatrixWithId(0);
    });
  },

  /** 固化床次上下文（床次号 + 款式快照，供保存 payload 与成功页展示） */
  applyProdContext() {
    this.setData({
      prodBedNo: this.data.bedNo,
      prodStyleNo: (this.data.style && this.data.style.style_no) || (this.data.order && this.data.order.style_no) || '',
      prodStyleName: (this.data.style && this.data.style.name) || (this.data.order && this.data.order.style_name) || ''
    });
  },

  // ============================================================
  // 生成扎（原第 2/3 步已并入：裁剪计划 2026-10-23 去掉、工序工价 2026-10-21 去掉）
  // ============================================================

  /** 进入编菲矩阵：带入床次上下文，挂单余量自动预填（§2.5.3） */
  enterMatrixWithId(productionId: number) {
    this.setData({
      step: 4,
      prodId: productionId,
      colors: [],
      // 默认尺码预填已去掉（2026-10-21 用户拍板）：尺码由用户自己在点选面板添加
      sizes: [],
      colorPanelVisible: false,
      colorOptions: [],
      sizePanelVisible: false,
      sizeOptions: [],
      cells: {},
      multiCounts: {},
      multiBase: '',
      arrayQty: '',
      // 扎号参数记忆：带出本厂上次保存用的起始扎号/每扎上限（无记忆用默认 1/20）
      ...(this.readBundlePref())
    });
    // 挂单余量自动预填：只填还有余量的格（余 0 的色码不再带出，2026-10-22 用户反馈）
    if (this.data.order && this.data.orderRemainingReady) {
      const pre: Record<string, number> = {};
      Object.keys(this.data.orderRemaining).forEach((k) => {
        const v = this.data.orderRemaining[k];
        if (v > 0) pre[k] = v;
      });
      if (Object.keys(pre).length) {
        this.setData({ cells: pre });
        wx.showToast({ title: '已按订单余量预填', icon: 'none' });
      } else {
        wx.showToast({ title: '该订单色码余量已裁完', icon: 'none' });
      }
    }
    this.rebuildMatrix();
    this.rebuildBundles();
    this._savedSnap = this.snapUnsaved(); // 进入矩阵的初始态即基线（空输入不脏）
    this.refreshGuard();
  },

  /** 带 production_id 直达编菲（如从床次详情进入） */
  enterMatrixWithProduction(id: number) {
    fetchProduction(id)
      .then((p: Production) => {
        this._prodCreated = id;
        this.setData({ style: null });
        this.setData({
          prodBedNo: p.bed_no,
          prodStyleNo: p.style_no || '',
          prodStyleName: p.style_name || ''
        });
        this.enterMatrixWithId(id);
      })
      .catch(() => {
        wx.showToast({ title: '床次加载失败', icon: 'none' });
      });
  },

  // ============ 颜色 / 尺码 chips ============

  /** 挂单时校验颜色/尺码是否在订单内（不在则提示并拦截；items 为空的订单不约束） */
  checkOrderColorSize(kind: 'color' | 'size', name: string): boolean {
    const list = kind === 'color' ? this.data.orderColors : this.data.orderSizes;
    if (!this.data.order || !list.length || list.indexOf(name) >= 0) return true;
    wx.showToast({
      title: `${kind === 'color' ? '颜色' : '尺码'}「${name}」不在订单中，请核对`,
      icon: 'none'
    });
    return false;
  },

  /** 「＋ 添加」→ 开合常用色面板；首次打开拉厂内历史颜色（用量倒序），失败静默用内置色兜底 */
  onAddColor() {
    const visible = !this.data.colorPanelVisible;
    this.setData({ colorPanelVisible: visible });
    if (visible && !this.data.colorOptions.length) {
      this.loadColorOptions();
    }
  },

  loadColorOptions() {
    fetchProductionColors()
      .then((res) => this.buildColorOptions((res.results || []).map((r) => r.name)))
      .catch(() => this.buildColorOptions([]));
  },

  /** 厂内常用色在前、内置常用色兜底、去重、标记已添加 */
  buildColorOptions(historyNames: string[]) {
    const seen = new Set<string>();
    const names: string[] = [];
    for (const n of historyNames.concat(COMMON_COLORS)) {
      if (n && !seen.has(n)) { seen.add(n); names.push(n); }
    }
    this.setData({
      colorOptions: names.map((name) => ({ name, added: this.data.colors.indexOf(name) >= 0 }))
    });
  },

  onPickColorChip(e: WechatMiniprogram.TouchEvent) {
    const name = String(e.currentTarget.dataset.name || '');
    if (!name) return;
    if (this.data.colors.indexOf(name) >= 0) return; // 已添加的 chip 置灰不响应
    if (!this.checkOrderColorSize('color', name)) return;
    this.setData({
      colors: this.data.colors.concat([name]),
      colorOptions: this.data.colorOptions.map(
        (o) => (o.name === name ? { ...o, added: true } : o))
    });
    this.rebuildMatrix();
    this.rebuildBundles();
    this.refreshGuard();
  },

  /** 手写兜底：面板里的「自定义…」 */
  onOpenColorCustom() {
    this.promptChip('添加颜色', '请输入颜色名称', (name) => {
      if (this.data.colors.indexOf(name) >= 0) {
        wx.showToast({ title: '该颜色已存在', icon: 'none' });
        return;
      }
      if (!this.checkOrderColorSize('color', name)) return;
      this.setData({
        colors: this.data.colors.concat([name]),
        colorOptions: this.data.colorOptions.map(
          (o) => (o.name === name ? { ...o, added: true } : o))
      });
      this.rebuildMatrix();
      this.rebuildBundles();
      this.refreshGuard();
    });
  },

  /** 「＋ 添加」→ 开合常用尺码面板；首次打开拉厂内历史尺码（用量倒序），失败静默用内置尺码池兜底 */
  onAddSize() {
    const visible = !this.data.sizePanelVisible;
    this.setData({ sizePanelVisible: visible });
    if (visible && !this.data.sizeOptions.length) {
      this.loadSizeOptions();
    }
  },

  loadSizeOptions() {
    fetchProductionHints()
      .then((res) => this.buildSizeOptions((res.sizes || []).map((s) => s.name)))
      .catch(() => this.buildSizeOptions([]));
  },

  /** 厂内历史尺码在前、内置尺码池兜底、去重、标记已添加 */
  buildSizeOptions(historyNames: string[]) {
    const seen = new Set<string>();
    const names: string[] = [];
    for (const n of historyNames.concat(SIZE_POOL)) {
      if (n && !seen.has(n)) { seen.add(n); names.push(n); }
    }
    this.setData({
      sizeOptions: names.map((name) => ({ name, added: this.data.sizes.indexOf(name) >= 0 }))
    });
  },

  onPickSizeChip(e: WechatMiniprogram.TouchEvent) {
    const name = String(e.currentTarget.dataset.name || '');
    if (!name) return;
    if (this.data.sizes.indexOf(name) >= 0) return; // 已添加的 chip 置灰不响应
    if (!this.checkOrderColorSize('size', name)) return;
    this.setData({
      sizes: this.data.sizes.concat([name]),
      sizeOptions: this.data.sizeOptions.map(
        (o) => (o.name === name ? { ...o, added: true } : o))
    });
    this.rebuildMatrix();
    this.rebuildBundles();
    this.refreshGuard();
  },

  /** 手写兜底：面板里的「自定义…」 */
  onOpenSizeCustom() {
    this.promptChip('添加尺码', '请输入尺码（如 M / XL）', (name) => {
      if (this.data.sizes.indexOf(name) >= 0) {
        wx.showToast({ title: '该尺码已存在', icon: 'none' });
        return;
      }
      if (!this.checkOrderColorSize('size', name)) return;
      this.setData({
        sizes: this.data.sizes.concat([name]),
        sizeOptions: this.data.sizeOptions.map(
          (o) => (o.name === name ? { ...o, added: true } : o))
      });
      this.rebuildMatrix();
      this.rebuildBundles();
      this.refreshGuard();
    });
  },

  // ============ 扎号参数记忆（按厂隔离，保存成功时写入） ============

  /** 读取上次保存用的每扎上限/起始扎号；无效或无记忆返回空对象（用默认 1/20） */
  readBundlePref(): Partial<{ startBundleNo: string; bundleLimit: string }> {
    try {
      const pref = wx.getStorageSync(`${BUNDLE_PREF_KEY}_${getCompanyId()}`) as { limit?: string; start?: string } | '';
      if (pref && pref.limit && Number(pref.limit) >= 1 && /^\d+$/.test(String(pref.start || ''))) {
        return { startBundleNo: String(pref.start || '1'), bundleLimit: String(pref.limit) };
      }
    } catch (e) { /* 读缓存失败用默认值 */ }
    return {};
  },

  saveBundlePref(limit: number, start: string) {
    try {
      wx.setStorageSync(`${BUNDLE_PREF_KEY}_${getCompanyId()}`, { limit: String(limit), start });
    } catch (e) { /* 记忆失败不阻塞保存 */ }
  },

  promptChip(title: string, placeholder: string, done: (name: string) => void) {
    wx.showModal({
      title,
      editable: true,
      placeholderText: placeholder,
      success: (res) => {
        const name = (res.content || '').trim();
        if (res.confirm && name) done(name);
      }
    });
  },

  onRemoveColor(e: WechatMiniprogram.TouchEvent) {
    const color = String(e.currentTarget.dataset.color);
    this.removeChip('color', color);
  },

  onRemoveSize(e: WechatMiniprogram.TouchEvent) {
    const size = String(e.currentTarget.dataset.size);
    this.removeChip('size', size);
  },

  /** 删除颜色/尺码：二次确认，其下格子一并清除 */
  removeChip(kind: 'color' | 'size', value: string) {
    wx.showModal({
      title: `删除${kind === 'color' ? '颜色' : '尺码'}`,
      content: `删除「${value}」将同时清除其已录入的件数，确定吗？`,
      success: (res) => {
        if (!res.confirm) return;
        const listKey = kind === 'color' ? 'colors' : 'sizes';
        const list = (this.data[listKey] as string[]).filter((v) => v !== value);
        const cells: Record<string, number> = {};
        Object.keys(this.data.cells).forEach((k) => {
          const [c, s] = k.split('|');
          if ((kind === 'color' ? c : s) !== value) cells[k] = this.data.cells[k];
        });
        const counts: Record<string, number> = {};
        Object.keys(this.data.multiCounts).forEach((k) => {
          const [c, s] = k.split('|');
          if ((kind === 'color' ? c : s) !== value) counts[k] = this.data.multiCounts[k];
        });
        const patch: Record<string, unknown> = { [listKey]: list, cells, multiCounts: counts };
        if (kind === 'color') {
          // 同步点选面板的「已添加」标记
          patch.colorOptions = this.data.colorOptions.map(
            (o) => (o.name === value ? { ...o, added: false } : o));
        } else {
          patch.sizeOptions = this.data.sizeOptions.map(
            (o) => (o.name === value ? { ...o, added: false } : o));
        }
        this.setData(patch);
        this.rebuildMatrix();
        this.rebuildBundles();
        this.refreshGuard();
      }
    });
  },

  // ============ 视图切换 ============

  onSwitchView(e: WechatMiniprogram.TouchEvent) {
    const key = String(e.currentTarget.dataset.key);
    if (!key || key === this.data.view) return;
    this.setData({ view: key, sheetVisible: false });
    this.rebuildMatrix(); // 视图转置/模式变了必须重算矩阵（此前漏调导致横向/纵向切换不生效）
  },

  // ============ 矩阵渲染（横向/纵向/倍数/排列共用） ============

  rebuildMatrix() {
    const { view, colors, sizes, cells, multiCounts, multiBase } = this.data;
    const base = Number(multiBase) || 0;
    const byColor = view !== 'v'; // 纵向转置：尺码为行
    const rowKeys = byColor ? colors : sizes;
    const colKeys = byColor ? sizes : colors;
    const editable = view === 'h' || view === 'v' || view === 'multi';
    const rows: MtxRow[] = rowKeys.map((rk) => {
      let total = 0;
      const rowCells: MtxCell[] = colKeys.map((ck) => {
        const key = byColor ? `${rk}|${ck}` : `${ck}|${rk}`;
        const qty = cells[key] || 0;
        const count = multiCounts[key] || 0;
        if (view === 'multi') {
          total += base * count;
          return {
            key,
            main: count > 0 ? String(base * count) : '',
            sub: count > 0 ? `${base || 0}×${count}` : '',
            editable: true
          };
        }
        total += qty;
        return { key, main: qty > 0 ? String(qty) : '', sub: '', editable };
      });
      return { label: rk, cells: rowCells, total };
    });
    const grandTotal = rows.reduce((s, r) => s + r.total, 0);
    this.setData({
      mtxCols: colKeys,
      mtxRows: rows,
      mtxEditable: editable,
      grandTotal
    });
  },

  /** 点击矩阵格 → 数字键盘弹层（横向/纵向改件数，倍数改份数） */
  onTapCell(e: WechatMiniprogram.TouchEvent) {
    const { key } = e.currentTarget.dataset as { key: string };
    if (!key) return;
    const { view, cells, multiCounts } = this.data;
    if (view === 'multi') {
      this.openSheet('count', `份数（基数 ${Number(this.data.multiBase) || 0}）`, String(multiCounts[key] || ''), key);
    } else if (view === 'h' || view === 'v') {
      this.openSheet('cell', '件数', String(cells[key] || ''), key);
    }
    // 排列视图为只读预览，格子不可点
  },

  /**
   * 点行头（横向复制，2026-10-22 用户需求）：该行首个已填值铺满整行——
   * 数字快捷填写，不改颜色/尺码表头；纵向视图行=尺码（cells 键仍为 色|尺码）；
   * 倍数视图铺份数（件数=基数×份数 同步折算）；排列视图为只读预览不响应。
   */
  onTapRowHead(e: WechatMiniprogram.TouchEvent) {
    if (this.data.view === 'array') return;
    const idx = Number((e.currentTarget.dataset as { row?: number }).row);
    const { view, colors, sizes, cells, multiCounts } = this.data;
    const byColor = view !== 'v';
    const rowKeys = byColor ? colors : sizes;
    const colKeys = byColor ? sizes : colors;
    const rk = rowKeys[idx];
    if (!rk) return;
    const multi = view === 'multi';
    const src = multi ? multiCounts : cells;
    const keyOf = (rk2: string, ck2: string) => (byColor ? `${rk2}|${ck2}` : `${ck2}|${rk2}`);
    let sample = 0;
    for (const ck of colKeys) {
      const v = src[keyOf(rk, ck)] || 0;
      if (v > 0) { sample = v; break; }
    }
    if (!sample) {
      wx.showToast({ title: multi ? '请先在该行填一个份数' : '请先在该行填一个数量', icon: 'none' });
      return;
    }
    const next = { ...cells };
    const nextCounts = multi ? { ...multiCounts } : multiCounts;
    colKeys.forEach((ck) => {
      const key = keyOf(rk, ck);
      if (multi) {
        nextCounts[key] = sample;
        next[key] = (Number(this.data.multiBase) || 0) * sample;
      } else {
        next[key] = sample;
      }
    });
    this.setData(multi ? { cells: next, multiCounts: nextCounts } : { cells: next });
    this.rebuildMatrix();
    wx.showToast({ title: multi ? `已按 ${sample} 份填充该行` : `已按 ${sample} 件填充该行`, icon: 'none', duration: 1200 });
  },

  /** 点列头（纵向复制）：该列首个已填值铺满整列（数字快捷填写，不改表头） */
  onTapColHead(e: WechatMiniprogram.TouchEvent) {
    if (this.data.view === 'array') return;
    const idx = Number((e.currentTarget.dataset as { col?: number }).col);
    const { view, colors, sizes, cells, multiCounts } = this.data;
    const byColor = view !== 'v';
    const rowKeys = byColor ? colors : sizes;
    const colKeys = byColor ? sizes : colors;
    const ck = colKeys[idx];
    if (!ck) return;
    const multi = view === 'multi';
    const src = multi ? multiCounts : cells;
    const keyOf = (rk2: string, ck2: string) => (byColor ? `${rk2}|${ck2}` : `${ck2}|${rk2}`);
    let sample = 0;
    for (const rk of rowKeys) {
      const v = src[keyOf(rk, ck)] || 0;
      if (v > 0) { sample = v; break; }
    }
    if (!sample) {
      wx.showToast({ title: multi ? '请先在该列填一个份数' : '请先在该列填一个数量', icon: 'none' });
      return;
    }
    const next = { ...cells };
    const nextCounts = multi ? { ...multiCounts } : multiCounts;
    rowKeys.forEach((rk) => {
      const key = keyOf(rk, ck);
      if (multi) {
        nextCounts[key] = sample;
        next[key] = (Number(this.data.multiBase) || 0) * sample;
      } else {
        next[key] = sample;
      }
    });
    this.setData(multi ? { cells: next, multiCounts: nextCounts } : { cells: next });
    this.rebuildMatrix();
    wx.showToast({ title: multi ? `已按 ${sample} 份填充该列` : `已按 ${sample} 件填充该列`, icon: 'none', duration: 1200 });
  },

  // ============ 倍数 / 排列 / 扎号 的全局输入 ============

  onTapMultiBase() {
    this.openSheet('base', '倍数基数（件）', this.data.multiBase, '');
  },

  onInputArrayQty() {
    this.openSheet('array', '每格件数（一键铺满）', this.data.arrayQty, '');
  },

  onApplyArray() {
    const qty = Number(this.data.arrayQty) || 0;
    if (qty <= 0) {
      wx.showToast({ title: '请先设定每格件数', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '批量填充',
      content: `将全部颜色×尺码格子填充为 ${qty} 件，覆盖已录入值，确定吗？`,
      success: (res) => {
        if (!res.confirm) return;
        const cells: Record<string, number> = {};
        this.data.colors.forEach((c) => {
          this.data.sizes.forEach((s) => {
            cells[`${c}|${s}`] = qty;
          });
        });
        this.setData({ cells });
        this.rebuildMatrix();
        this.rebuildBundles();
        this.refreshGuard();
        wx.showToast({ title: '已铺满', icon: 'success' });
      }
    });
  },

  onTapStartBundleNo() {
    this.openSheet('start', '起始扎号', this.data.startBundleNo, '');
  },

  onTapBundleLimit() {
    this.openSheet('limit', '每扎上限（件）', this.data.bundleLimit, '');
  },

  // ============ 扎号预览（按 bundle_limit 模拟后端拆扎规则 + 实时尾扎预览） ============

  rebuildBundles() {
    const { colors, sizes, cells } = this.data;
    const limit = Math.max(1, Math.floor(Number(this.data.bundleLimit) || 0));
    const start = Math.max(0, Math.floor(Number(this.data.startBundleNo) || 0));
    const rows: BundleRow[] = [];
    let no = start;
    let total = 0;
    let fullCount = 0;
    let tailQty = 0;
    colors.forEach((color) => {
      sizes.forEach((size) => {
        const key = `${color}|${size}`;
        const qty = cells[key] || 0;
        if (qty <= 0) return;
        const full = Math.floor(qty / limit);
        const tail = qty % limit;
        fullCount += full;
        tailQty += tail;
        for (let i = 0; i < full; i++) {
          rows.push({ no: String(no++), color, size, qty: limit, isTail: false, cellKey: key });
        }
        if (tail > 0) {
          rows.push({ no: String(no++), color, size, qty: tail, isTail: true, cellKey: key });
        }
        total += qty;
      });
    });
    // 实时尾扎预览（竞品细节 §2.6.1）：输入每扎上限后显示「整除 N 扎+尾扎 M 件」
    const tailSummary = total > 0
      ? `整除 ${fullCount} 扎 + 尾扎 ${tailQty} 件`
      : '';
    this.setData({ bundleRows: rows, bundleTotal: total, tailSummary });
  },

  /** 微调尾扎件数：等价于重设该格总量（整扎数不变 + 新尾数），与后端拆扎严格对齐 */
  onTapBundle(e: WechatMiniprogram.TouchEvent) {
    const { index } = e.currentTarget.dataset as { index: number };
    const row = this.data.bundleRows[Number(index)];
    if (!row) return;
    if (!row.isTail) {
      wx.showToast({ title: '整扎由每扎上限决定，仅尾扎可微调', icon: 'none' });
      return;
    }
    const full = Math.floor((this.data.cells[row.cellKey] || 0) / Math.max(1, Math.floor(Number(this.data.bundleLimit) || 0)));
    this.setData({ sheetTailFull: full });
    this.openSheet('tail', `扎 ${row.no}（${row.color}/${row.size}）件数`, String(row.qty), row.cellKey);
  },

  // ============ 匹号与物料（简化口径） ============


  // ============ 数字键盘弹层 ============

  openSheet(mode: SheetMode, title: string, value: string, key: string) {
    this.setData({ sheetVisible: true, sheetMode: mode, sheetTitle: title, sheetValue: value, sheetKey: key });
    wx.nextTick(() => this.syncKeypad());
  },

  onCloseSheet() {
    this.setData({ sheetVisible: false });
  },

  onSheetInput(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ sheetValue: e.detail.value });
  },

  onSheetConfirm() {
    const { sheetMode, sheetKey, sheetValue, sheetTailFull } = this.data;
    const n = Number(sheetValue) || 0;
    if (sheetMode === 'cell') {
      const cells = { ...this.data.cells };
      if (n > 0) {
        // 颜色行首个填数：本颜色其它尺码以该数量作为初始值（仍可逐格再改/清）
        const color = sheetKey.split('|')[0];
        const othersEmpty = this.data.sizes.every((s) => {
          const k = `${color}|${s}`;
          return k === sheetKey || !cells[k];
        });
        cells[sheetKey] = n;
        if (othersEmpty) {
          for (const s of this.data.sizes) {
            const k = `${color}|${s}`;
            if (k !== sheetKey) cells[k] = n;
          }
        }
      } else {
        delete cells[sheetKey];
      }
      this.setData({ cells });
      this.rebuildMatrix();
      this.rebuildBundles();
    } else if (sheetMode === 'count') {
      const counts = { ...this.data.multiCounts };
      if (n > 0) counts[sheetKey] = Math.floor(n); else delete counts[sheetKey];
      this.setData({ multiCounts: counts });
      this.rebuildMatrix();
    } else if (sheetMode === 'base') {
      this.setData({ multiBase: sheetValue });
      this.rebuildMatrix();
    } else if (sheetMode === 'array') {
      this.setData({ arrayQty: sheetValue });
    } else if (sheetMode === 'limit') {
      if (n < 1) {
        wx.showToast({ title: '每扎上限至少 1 件', icon: 'none' });
        return;
      }
      this.setData({ bundleLimit: String(Math.floor(n)) });
      this.rebuildBundles();
    } else if (sheetMode === 'start') {
      if (n < 0) {
        wx.showToast({ title: '起始扎号须为非负整数', icon: 'none' });
        return;
      }
      this.setData({ startBundleNo: String(Math.floor(n)) });
      this.rebuildBundles();
    } else if (sheetMode === 'tail') {
      // 新尾数 t → 该格总量 = 整扎×上限 + t；t=0 视为删除尾扎
      const limit = Math.max(1, Math.floor(Number(this.data.bundleLimit) || 0));
      const nextQty = sheetTailFull * limit + Math.floor(n);
      const cells = { ...this.data.cells };
      if (nextQty > 0) cells[sheetKey] = nextQty; else delete cells[sheetKey];
      this.setData({ cells });
      this.rebuildMatrix();
      this.rebuildBundles();
    }
    this.setData({ sheetVisible: false });
    this.refreshGuard();
  },

  /** 受控回显数字键盘显示值 */
  syncKeypad() {
    const keypad = this.selectComponent('#mtx-keypad') as WechatMiniprogram.Component.TrivialInstance | null;
    if (keypad && typeof (keypad as unknown as { setValue?: (v: string) => void }).setValue === 'function') {
      (keypad as unknown as { setValue: (v: string) => void }).setValue(this.data.sheetValue);
    }
  },

  // ============ 清空（二次确认） ============

  onClearMatrix() {
    wx.showModal({
      title: '清空编菲数据',
      content: '将清空全部件数与倍数，颜色尺码保留，确定吗？',
      confirmColor: '#FA5151',
      success: (res) => {
        if (!res.confirm) return;
        this.setData({
          cells: {},
          multiCounts: {},
          multiBase: '',
          arrayQty: '',
          sheetVisible: false
        });
        this.rebuildMatrix();
        this.rebuildBundles();
        this.refreshGuard();
        wx.showToast({ title: '已清空', icon: 'success' });
      }
    });
  },

  // ============ 未保存拦截 ============

  /** 当前「未保存判定」相关输入的快照（保存成功时同步，见 onSaveMatrix） */
  snapUnsaved(): string {
    const d = this.data;
    return JSON.stringify([d.colors, d.cells, d.multiCounts, d.multiBase,
      d.arrayQty, d.bundleLimit, d.startBundleNo]);
  },

  /**
   * 是否有未保存的编菲输入：与最近一次保存快照比对。
   * 不能只看「有没有输入」——保存成功后输入还在页面上，旧判定会让离开拦截永远关不掉
   * （保存成功 navigateBack 仍弹「尚未保存」，真 bug 已修）。
   */
  hasUnsaved(): boolean {
    if (this.data.step !== 4) return false;
    return this.snapUnsaved() !== this._savedSnap;
  },

  /** 脏数据 → 开启返回拦截（仅拦返回键/手势，选型说明见文件头注释） */
  refreshGuard() {
    if (this.data.step !== 4) return;
    const dirty = this.hasUnsaved();
    if (dirty === this._guardOn) return;
    if (typeof wx.enableAlertBeforeUnload !== 'function' || typeof wx.disableAlertBeforeUnload !== 'function') {
      return; // 低版本基础库无此 API：静默降级（onUnload 不拦，约定见汇报）
    }
    this._guardOn = dirty;
    try {
      if (dirty) {
        wx.enableAlertBeforeUnload({ message: '编菲数据尚未保存，确定离开吗？' });
      } else {
        wx.disableAlertBeforeUnload({});
      }
    } catch (e) {
      // Skyline 渲染等场景不支持：降级为不拦截
      this._guardOn = false;
    }
  },

  // ============ 保存（契约不变：POST /tickets/batch；挂单超量弹确认） ============

  onSaveMatrix() {
    if (this.data.saving) return;
    const { prodId, colors, sizes, cells, multiBase, multiCounts, bundleLimit, startBundleNo, view } = this.data;
    // 倍数视图保存的是「基数×份数」折算后的件数
    const base = Number(multiBase) || 0;
    const items: Array<{ color: string; size: string; qty: number }> = [];
    let submitTotal = 0;
    colors.forEach((color) => {
      sizes.forEach((size) => {
        const key = `${color}|${size}`;
        const qty = view === 'multi' ? base * (multiCounts[key] || 0) : (cells[key] || 0);
        if (qty > 0) {
          submitTotal += qty;
          items.push({ color, size, qty });
        }
      });
    });
    if (!items.length) {
      wx.showToast({ title: '请先录入各格件数', icon: 'none' });
      return;
    }
    const limit = Math.floor(Number(bundleLimit) || 0);
    if (limit < 1) {
      wx.showToast({ title: '每扎上限至少 1 件', icon: 'none' });
      return;
    }
    if (!/^\d+$/.test(startBundleNo)) {
      wx.showToast({ title: '起始扎号须为数字', icon: 'none' });
      return;
    }
    // 挂单建床校验（2026-10-22 用户需求：总数/尺码/颜色都校验）——
    // ① 颜色/尺码必须在该订单内（硬拦，items 为空的订单不约束）
    if (this.data.order) {
      const oc = this.data.orderColors;
      const os = this.data.orderSizes;
      for (const it of items) {
        if (oc.length && oc.indexOf(it.color) < 0) {
          wx.showToast({ title: `颜色「${it.color}」不在订单中，请核对`, icon: 'none' });
          return;
        }
        if (os.length && os.indexOf(it.size) < 0) {
          wx.showToast({ title: `尺码「${it.size}」不在订单中，请核对`, icon: 'none' });
          return;
        }
      }
      // ② 总数：逐格超该色码余量 → 弹明细确认（防手误，知情可继续）
      const overs = items
        .filter((it) => {
          const left = this.data.orderRemaining[`${it.color}|${it.size}`];
          return left !== undefined && it.qty > left;
        })
        .map((it) => `${it.color}/${it.size}：录 ${it.qty} 余 ${this.data.orderRemaining[`${it.color}|${it.size}`]}`);
      if (overs.length) {
        const shown = overs.slice(0, 3).join('\n') + (overs.length > 3 ? `\n…等 ${overs.length} 格` : '');
        wx.showModal({
          title: '超出订单余量',
          content: `${shown}\n确定继续开床吗？`,
          success: (res) => {
            if (res.confirm) this.doSaveMatrix(items, limit);
          }
        });
        return;
      }
      // ③ 整单总数兜底（逐格余量数据缺失时仍能拦总量；余 0 也拦——2026-10-22 用户反馈）
      if (this.data.orderRemainingReady && submitTotal > this.data.orderRemainingTotal) {
        wx.showModal({
          title: '超出订单余量',
          content: `本次开床 ${submitTotal} 件，超出订单余量 ${this.data.orderRemainingTotal} 件，确定继续吗？`,
          success: (res) => {
            if (res.confirm) this.doSaveMatrix(items, limit);
          }
        });
        return;
      }
    }
    this.doSaveMatrix(items, limit);
  },

  doSaveMatrix(items: Array<{ color: string; size: string; qty: number }>, limit: number) {
    const { prodId, startBundleNo } = this.data;
    this.setData({ saving: true });
    // 床次号兜底拦截：床次号自动顺延失败且无手动输入时，不落出脏床次
    if (!prodId && !(this.data.prodBedNo || this.data.bedNo).trim()) {
      this.setData({ saving: false });
      wx.showToast({ title: '床次号获取失败，请返回重试', icon: 'none' });
      return;
    }
    // 建床推迟到保存时（2026-10-21 用户拍板：总量 0 的空床次不允许创建）：
    // 床次不存在则先 POST /productions（总量 0，紧随其后的编菲 batch 会触发后端
    // _recalc_total 按菲票汇总重算），成功后立刻编菲，两步同批完成
    const ensureProduction: Promise<number> = prodId
      ? Promise.resolve(prodId)
      : this.createProductionWithBomBypass();
    ensureProduction
      .then((id) => {
        this.setData({ prodId: id });
        return createTicketsBatch({
          production_id: id,
          items,
          bundle_limit: limit,
          start_bundle_no: startBundleNo
        });
      })
      .then((tickets) => {
        this.setData({ saving: false });
        this.saveBundlePref(limit, startBundleNo); // 扎号参数记忆：下次编菲直接带出
        this._savedSnap = this.snapUnsaved(); // 已保存：基线对齐当前输入，hasUnsaved 才会转 false
        this.refreshGuard(); // 关闭离开拦截
        this.enterSuccess(tickets);
      })
      .catch(() => this.setData({ saving: false }));
  },

  /**
   * 建床（D4 无生效BOM拦截）：后端 400「该款式无生效BOM…」→ 弹可输入确认框收
   * 放行原因，带 bom_bypass=true + bypass_reason 重试一次（后端 ops audit 留痕）；
   * 其余错误原样上抛走统一 catch。
   */
  createProductionWithBomBypass(extra?: { bom_bypass: boolean; bypass_reason: string }): Promise<number> {
    const payload = { ...this.buildProductionPayload(), ...(extra || {}) };
    return createProduction(payload).then((created: Production) => {
      this._prodCreated = created.id;
      return created.id;
    }).catch((err: unknown) => {
      const isBomBlock = err instanceof ApiError && err.status === 400
        && (err.message || '').indexOf('BOM') >= 0;
      if (!isBomBlock) throw err;
      return new Promise<number>((resolve, reject) => {
        wx.showModal({
          title: '该款式无生效BOM',
          content: '可填原因放行开裁（后台留痕），或取消后到 BOM 管理激活',
          editable: true,
          placeholderText: '放行原因（必填）',
          success: (res) => {
            if (!res.confirm) { reject(err); return; }
            const reason = (res.content || '').trim();
            if (!reason) {
              wx.showToast({ title: '请填写放行原因', icon: 'none' });
              reject(err);
              return;
            }
            this.createProductionWithBomBypass({ bom_bypass: true, bypass_reason: reason })
              .then(resolve).catch(reject);
          },
          fail: () => reject(err)
        });
      });
    });
  },

  /** 建床 payload（总量恒 0：编菲保存后端按菲票自动汇总实裁数） */
  buildProductionPayload(): Parameters<typeof createProduction>[0] {
    const payload: Parameters<typeof createProduction>[0] = {
      bed_no: this.data.prodBedNo || this.data.bedNo,
      total_qty: 0,
      order_type: this.data.orderTypes[this.data.orderTypeIndex],
      customer: this.data.customer || null,
      fabric: this.data.fabric.trim(),
      source_order_no: this.data.sourceOrderNo.trim()
    };
    if (this.data.order) {
      payload.order_id = this.data.order.id;
    }
    // 无单，或挂单但老订单没关联款式：必须带款式（正常挂单由后端从订单继承）
    if (!this.data.order || !this.data.order.style) {
      const styleId = (this.data.style as Style).id;
      // style 与 style_id 双写：兼容 B1 前后端两种字段名（多余字段后端忽略）
      payload.style = styleId;
      payload.style_id = styleId;
    }
    return payload;
  },

  // ============================================================
  // 第 5 步：成功页（落床次详情入口 + 引导打印，可跳过）
  // ============================================================

  enterSuccess(tickets: Array<{ id: number; bundle_no: string; color: string; size: string; qty: number; qr_code: string }>) {
    const app = getApp<IAppOption>();
    const cur = app.globalData.companies.find((c) => c.id === app.globalData.companyId);
    const previewTickets: PrintTicketData[] = (tickets || []).map((t) => ({
      id: t.id,
      qr_code: String(t.qr_code || ''),
      bed_no: this.data.prodBedNo,
      bundle_no: String(t.bundle_no || ''),
      color: String(t.color || ''),
      size: String(t.size || ''),
      qty: Number(t.qty) || 0,
      style_no: this.data.prodStyleNo,
      // 来源单号带向导第 2 步已填值（M5：与建床 payload 同源），标签勾选该字段才打印
      source_order_no: this.data.sourceOrderNo.trim(),
      lot_no: ''
    }));
    this.setData({
      step: 5,
      createdCount: tickets ? tickets.length : 0,
      previewTickets,
      previewCompany: { name: cur ? cur.name : '' }
    });
    wx.showToast({ title: `已生成 ${previewTickets.length} 张菲票`, icon: 'success' });
  },

  /** 引导打印：打开标签预览（确认后发蓝牙；未连打印机会引导去打印管理） */
  onPreviewPrint() {
    this.setData({ previewShow: true });
  },

  onPreviewCancel() {
    this.setData({ previewShow: false });
  },

  onPreviewConfirm() {
    const tickets = this.data.previewTickets;
    this.setData({ previewShow: false });
    if (!tickets.length) return;
    // 统一打印流程：未连接时弹窗带「去连接」直达打印管理
    startPrintFlow(this, tickets, {
      company: this.data.previewCompany
    });
  },

  /** 打印进度弹层「取消打印」回调（print-flow 统一转发） */
  onFlowCancel() {
    cancelPrintFlow();
  },

  /** 成功页：落床次详情 */
  goProductionDetail() {
    wx.redirectTo({ url: `/packages/admin/production-detail/index?id=${this.data.prodId}` });
  },

  /** 跳过：返回列表 */
  finishWizard() {
    wx.navigateBack();
  }
});
