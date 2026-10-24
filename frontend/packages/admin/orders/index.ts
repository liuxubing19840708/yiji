/**
 * 订单管理（规格 §2.5/§五 P1 新增）：列表 + 新建/编辑。
 * 列表卡=单号+状态徽标 / 客户·交期(+灯) / 完成率进度条 / 总量；
 * 筛选 tab（生产中/已完成/已取消/全部）+ 关键字。
 * 新建/编辑（F1/B1 契约改版）：
 *   - 单号后端自动生成（SO+日期+序号），表单不再录入，顶部显示说明行（编辑模式只读展示）；
 *   - 第一步选款式（必选）：弹层搜索列表，选中展示款式卡+工艺说明/图/视频（style-craft 组件）；
 *   - 色码矩阵：点行头=该行首个已填数量铺满整行；点列头=该列首个已填数量铺满整列；
 *   - 备注图片九宫格（≤9 张，压缩上传 /uploads，previewImage 预览）；
 *   - 关联工序（可选）：模板一键填充（GET /process-templates）+ 手工添加 + 改价/删行；
 *   - 提交 {style_id, items, remark, images, delivery_date?, customer?, process_items?}，不带 order_no。
 * 后端 /orders、/process-templates 由并行 SA1/B1 开发中：一律 try/catch 兜底，404 不白屏。
 */
import { requireLogin } from '../../../utils/auth';
import { ApiError } from '../../../utils/request';
import { mediaUrl } from '../../../utils/media';
import { chooseImage, uploadImage } from '../../../utils/upload';
import { fetchCustomers, fetchProcesses, fetchStyles, fetchStyle } from '../../../services/catalog';
import {
  fetchProductionColors, fetchProductionHints
} from '../../../services/production';
import {
  fetchOrders, fetchOrder, createOrder, updateOrder, OrderPayload
} from '../../../services/orders';
import {
  fetchProcessTemplates
} from '../../../services/process-template';
import { Order, OrderItem, Customer, Paginated, Style, Process, ProcessTemplate } from '../../../services/types';

const PAGE_SIZE = 20;
/** 备注图片上限 */
const MAX_IMAGES = 9;

/** 状态筛选 tab（§五：生产中/已完成/已取消/全部） */
const FILTERS = [
  { key: 'producing', label: '生产中' },
  { key: 'done', label: '已完成' },
  { key: 'cancelled', label: '已取消' },
  { key: 'all', label: '全部' }
];

/** 状态徽标样式（producing 橙 / done 绿 / cancelled 灰） */
const STATUS_CLS: Record<string, string> = {
  producing: 'badge',
  done: 'badge-green',
  cancelled: 'badge-gray'
};

/** 内置常用色/尺码池（与编菲页一致：厂内历史在前，内置兜底） */
const COMMON_COLORS = ['黑色', '白色', '灰色', '红色', '蓝色', '绿色', '黄色',
  '橙色', '紫色', '粉色', '棕色', '咖啡', '军绿', '卡其', '米色', '花色'];
const SIZE_POOL = ['S', 'M', 'L', 'XL', 'XXL', '3XL', '4XL', 'F', '均码',
  '80', '90', '95', '100', '110', '120', '130', '140', '150', '160'];

/** 列表卡 VM */
interface OrderVM {
  id: number;
  order_no: string;
  statusDisplay: string;
  statusCls: string;
  customerName: string;
  deliveryDate: string;
  /** 交期灯：''=无交期 / green / yellow(≤7天) / red(过期未完成) */
  lamp: string;
  progress: number;
  progressText: string;
  totalQty: number;
  registeredQty: number;
}

/** 关联工序行 */
interface ProcRowVM {
  process_id: number;
  name: string;
  price: string;
}

/** 接口未就绪（404）判定：页面降级为「服务升级中」提示，不白屏 */
function isServiceDown(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}

Page({
  data: {
    stage: 'list' as 'list' | 'edit',
    filters: FILTERS,
    filterKey: 'producing',
    keyword: '',
    orders: [] as OrderVM[],
    page: 1,
    hasMore: false,
    loading: false,
    error: false,
    /** 服务未就绪（/orders 404）：列表页展示引导文案 */
    serviceDown: false,

    // ================= 新建/编辑表单 =================
    editingId: 0,
    saving: false,
    /** 编辑模式只读展示的单号（新建不录入，后端自动生成） */
    orderNo: '',
    orderTypes: ['自产', '外发'],
    orderTypeIndex: 0,
    customers: [] as Customer[],
    customerNames: ['不指定客户'] as string[],
    customerIndex: 0,
    customer: 0,
    deliveryDate: '',
    remark: '',

    // ================= 款式（必选第一步） =================
    styleId: 0,
    /** 传给 style-craft 组件的款式对象（style_no/name/image/style_image/craft_note/craft_images/video） */
    styleObj: null as Record<string, unknown> | null,
    stylePanelVisible: false,
    styleKeyword: '',
    styleOptions: [] as Style[],
    styleLoading: false,
    styleError: false,

    // ================= 备注图片九宫格 =================
    images: [] as string[],
    /** 展示用（/uploads 返回相对路径，补域名后真机才显示缩略图） */
    imageUrls: [] as string[],
    imageUploading: false,

    // ================= 关联工序（可选，模板/手工） =================
    procRows: [] as ProcRowVM[],
    procs: [] as Process[],
    procNames: ['选择工序'] as string[],
    // ---- 模板弹层 ----
    tplSheetVisible: false,
    templates: [] as ProcessTemplate[],
    tplLoading: false,
    tplDown: false,

    // ---- 颜色×尺码数量矩阵（复用编菲交互） ----
    colors: [] as string[],
    sizes: [] as string[],
    /** cells[`${color}|${size}`] = 数量（>0 才存；cells 是唯一数据源，倍数视图录入时即时折算写入） */
    cells: {} as Record<string, number>,
    // ---- 视图页签（对齐编菲：横向/纵向/倍数/排列，2026-10-21 用户需求） ----
    views: [
      { key: 'h', name: '横向' },
      { key: 'v', name: '纵向' },
      { key: 'multi', name: '倍数' },
      { key: 'array', name: '排列' }
    ],
    view: 'h',
    /** 倍数视图：基数 × 每格份数（份数存 multiCounts，折算件数实时写回 cells） */
    multiBase: '',
    multiCounts: {} as Record<string, number>,
    /** 排列视图：每格件数一键铺满 */
    arrayQty: '',
    colorPanelVisible: false,
    colorOptions: [] as Array<{ name: string; added: boolean }>,
    sizePanelVisible: false,
    sizeOptions: [] as Array<{ name: string; added: boolean }>,
    mtxCols: [] as string[],
    mtxRows: [] as Array<{ label: string; cells: Array<{ key: string; main: string; sub: string }>; total: number }>,
    grandTotal: 0,
    // ---- 数字键盘弹层 ----
    sheetVisible: false,
    sheetValue: '',
    sheetKey: '',
    /** cell=件数 / count=倍数份数 / base=倍数基数 / array=排列每格件数 */
    sheetMode: 'cell' as 'cell' | 'count' | 'base' | 'array',
    sheetTitle: '录入数量'
  },

  onLoad(options: Record<string, string>) {
    // 从订单详情「编辑」进入：带 id 载入订单
    if (options.id) {
      this.openEdit(Number(options.id));
    }
  },

  onShow() {
    if (!requireLogin()) return;
    if (this.data.stage === 'list') {
      this.reloadOrders();
    } else if (!this.data.customers.length) {
      this.loadCustomers();
    }
  },

  onPullDownRefresh() {
    if (this.data.stage === 'list') this.reloadOrders();
    wx.stopPullDownRefresh();
  },

  // ============================================================
  // 列表
  // ============================================================

  onFilter(e: WechatMiniprogram.TouchEvent) {
    const key = String(e.currentTarget.dataset.key || '');
    if (!key || key === this.data.filterKey) return;
    this.setData({ filterKey: key });
    this.reloadOrders();
  },

  onKeyword(e: WechatMiniprogram.Input) {
    this.setData({ keyword: e.detail.value });
  },

  onSearch() {
    this.reloadOrders();
  },

  reloadOrders() {
    const params: Record<string, unknown> = { page: 1, page_size: PAGE_SIZE };
    if (this.data.filterKey !== 'all') params.status = this.data.filterKey;
    const kw = this.data.keyword.trim();
    if (kw) params.keyword = kw;
    this.setData({ loading: true, error: false, page: 1 });
    fetchOrders(params as Parameters<typeof fetchOrders>[0])
      .then((res: Paginated<Order>) => {
        this.setData({
          loading: false,
          serviceDown: false,
          orders: (res.results || []).map(toVM),
          hasMore: !!res.next
        });
      })
      .catch((e) => {
        this.setData({
          loading: false,
          error: true,
          serviceDown: isServiceDown(e)
        });
      });
  },

  loadMoreOrders() {
    if (!this.data.hasMore || this.data.loading) return;
    const nextPage = this.data.page + 1;
    const params: Record<string, unknown> = { page: nextPage, page_size: PAGE_SIZE };
    if (this.data.filterKey !== 'all') params.status = this.data.filterKey;
    const kw = this.data.keyword.trim();
    if (kw) params.keyword = kw;
    this.setData({ loading: true });
    fetchOrders(params as Parameters<typeof fetchOrders>[0])
      .then((res: Paginated<Order>) => {
        this.setData({
          loading: false,
          orders: this.data.orders.concat((res.results || []).map(toVM)),
          hasMore: !!res.next,
          page: nextPage
        });
      })
      .catch(() => this.setData({ loading: false }));
  },

  onReachBottom() {
    if (this.data.stage === 'list') this.loadMoreOrders();
  },

  /** 卡片点击 → 订单详情 */
  onOpenOrder(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    if (!id) return;
    wx.navigateTo({ url: `/packages/admin/order-detail/index?id=${id}` });
  },

  onRetry() {
    this.reloadOrders();
  },

  // ============================================================
  // 新建 / 编辑（stage='edit'）
  // ============================================================

  onAddOrder() {
    this.setData({
      stage: 'edit',
      editingId: 0,
      orderNo: '',
      orderTypeIndex: 0,
      customerIndex: 0,
      customer: 0,
      deliveryDate: '',
      remark: '',
      styleId: 0,
      styleObj: null,
      images: [],
      imageUrls: [],
      procRows: [],
      colors: [],
      sizes: [],
      cells: {},
      view: 'h',
      multiBase: '',
      multiCounts: {},
      arrayQty: '',
      colorPanelVisible: false,
      colorOptions: [],
      sizePanelVisible: false,
      sizeOptions: []
    });
    this.loadCustomers();
    this.loadProcs();
    this.rebuildMatrix();
  },

  /** 编辑：从详情页带 id 进入（onLoad options.id / 详情页跳转） */
  openEdit(id: number) {
    this.setData({ stage: 'edit', editingId: id });
    // 客户/工序基础数据先就位再算 picker 索引（否则下拉首屏为空时索引恒 0）
    Promise.all([fetchOrder(id), this.loadCustomers(), this.loadProcs()])
      .then(([o]) => {
        const items = o.items || [];
        const cells: Record<string, number> = {};
        const colors: string[] = [];
        const sizes: string[] = [];
        items.forEach((it: OrderItem) => {
          cells[`${it.color}|${it.size}`] = it.qty;
          if (colors.indexOf(it.color) < 0) colors.push(it.color);
          if (sizes.indexOf(it.size) < 0) sizes.push(it.size);
        });
        const idx = o.customer
          ? this.data.customers.findIndex((c) => c.id === o.customer) + 1
          : 0;
        // 关联工序回填：process_name 缺省时用 /processes 映射兜底
        const nameMap: Record<number, string> = {};
        this.data.procs.forEach((p) => { nameMap[p.id] = p.name; });
        const procRows: ProcRowVM[] = (o.process_items || []).map((it) => {
          // 契约 Order.process_items 只有 {process_id, price}；后端若多带 process_name 则优先用
          const rawIt = it as unknown as { process_name?: string };
          return {
            process_id: it.process_id,
            name: rawIt.process_name || nameMap[it.process_id] || `工序${it.process_id}`,
            price: String(it.price || '')
          };
        });
        this.setData({
          orderNo: o.order_no || '',
          orderTypeIndex: o.order_type === '外发' ? 1 : 0,
          customerIndex: idx,
          customer: o.customer || 0,
          deliveryDate: o.delivery_date || '',
          remark: o.remark || '',
          images: (o.images || []).slice(),
          imageUrls: (o.images || []).map((u: string) => mediaUrl(u)),
          styleId: o.style || 0,
          styleObj: o.style
            ? { style_no: o.style_no || '', name: o.style_name || '', style_image: o.style_image || '' }
            : null,
          procRows,
          colors,
          sizes,
          cells
        });
        this.rebuildMatrix();
        // 编辑模式补拉款式详情：工艺说明/图/视频（Order 只带款式基础字段）
        if (o.style) {
          fetchStyle(o.style)
            .then((s: Style) => {
              // 仍停在编辑态才回填（用户可能已取消返回列表）
              if (this.data.stage !== 'edit' || this.data.editingId !== id) return;
              this.setData({ styleObj: this.toStyleObj(s) });
            })
            .catch(() => { /* 款式详情失败：只展示订单自带款式基础字段 */ });
        }
      })
      .catch((e) => {
        wx.showToast({
          title: isServiceDown(e) ? '服务升级中，请稍后再试' : '订单加载失败',
          icon: 'none'
        });
        this.setData({ stage: 'list' });
        this.reloadOrders();
      });
  },

  /** 拉客户下拉（失败 resolve 空表，不阻塞订单保存） */
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
        .catch(() => resolve([])); /* 客户下拉失败不阻塞订单保存 */
    });
  },

  /** 拉工序列表（手工添加工序下拉 + 模板工序名映射兜底；失败 resolve 空表不阻塞） */
  loadProcs(): Promise<Process[]> {
    return new Promise((resolve) => {
      fetchProcesses()
        .then((procs) => {
          this.setData({
            procs,
            procNames: ['选择工序'].concat(procs.map((p) => p.name))
          });
          resolve(procs);
        })
        .catch(() => resolve([]));
    });
  },

  onCancelEdit() {
    this.setData({ stage: 'list' });
    this.reloadOrders();
  },

  onRemark(e: WechatMiniprogram.Input) { this.setData({ remark: e.detail.value }); },

  onOrderType(e: WechatMiniprogram.PickerChange) {
    this.setData({ orderTypeIndex: Number(e.detail.value) });
  },

  onCustomer(e: WechatMiniprogram.PickerChange) {
    const idx = Number(e.detail.value);
    const c = idx > 0 ? this.data.customers[idx - 1] : null;
    this.setData({ customerIndex: idx, customer: c ? c.id : 0 });
  },

  /** 空客户引导：跳客户管理添加后返回会经 onShow 重新拉取 */
  onGoCustomers() {
    wx.navigateTo({ url: '/packages/admin/customers/index' });
  },

  onDeliveryDate(e: WechatMiniprogram.PickerChange) {
    this.setData({ deliveryDate: String(e.detail.value || '') });
  },

  onClearDeliveryDate() {
    this.setData({ deliveryDate: '' });
  },

  noop() { /* 阻止冒泡 */ },

  // ============================================================
  // 款式选择（必选第一步）：弹层搜索列表 + 款式卡（style-craft 组件）
  // ============================================================

  onOpenStylePanel() {
    this.setData({ stylePanelVisible: true, styleKeyword: '', styleOptions: [] });
    this.searchStyles('');
  },

  onCloseStylePanel() {
    this.setData({ stylePanelVisible: false });
  },

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

  /** 列表项可能缺工艺字段：选中后拉详情补齐（失败不影响基础展示） */
  onPickStyle(e: WechatMiniprogram.TouchEvent) {
    const s = this.data.styleOptions[Number(e.currentTarget.dataset.index)];
    if (!s) return;
    this.setData({ styleId: s.id, styleObj: this.toStyleObj(s), stylePanelVisible: false });
    fetchStyle(s.id)
      .then((detail: Style) => {
        if (this.data.styleId === s.id) {
          this.setData({ styleObj: this.toStyleObj(detail) });
        }
      })
      .catch(() => { /* 详情失败：用列表数据兜底 */ });
  },

  /** Style → style-craft 组件入参（字段全部兜底缺省） */
  toStyleObj(s: Style): Record<string, unknown> {
    const raw = s as unknown as Record<string, unknown>;
    return {
      style_no: String(raw.style_no || ''),
      name: String(raw.name || ''),
      style_image: String(raw.style_image || raw.image || ''),
      craft_note: String(raw.craft_note || ''),
      craft_images: Array.isArray(raw.craft_images) ? raw.craft_images : [],
      video: String(raw.video || '')
    };
  },

  onClearStyle() {
    this.setData({ styleId: 0, styleObj: null });
  },

  // ============================================================
  // 备注图片九宫格（≤9 张，压缩上传，previewImage 预览）
  // ============================================================

  onAddImages() {
    if (this.data.imageUploading) return;
    const left = MAX_IMAGES - this.data.images.length;
    if (left <= 0) {
      wx.showToast({ title: `最多 ${MAX_IMAGES} 张`, icon: 'none' });
      return;
    }
    wx.chooseMedia({
      count: left,
      mediaType: ['image'],
      sizeType: ['compressed'],
      success: (res) => {
        const paths = (res.tempFiles || []).map((f) => f.tempFilePath).filter(Boolean);
        if (!paths.length) return;
        this.uploadImages(paths);
      },
      fail: () => { /* 用户取消 */ }
    });
  },

  /** 逐张压缩上传（复用 utils/upload 4MB 压缩逻辑），失败中止并提示已传数量 */
  async uploadImages(paths: string[]) {
    this.setData({ imageUploading: true });
    wx.showLoading({ title: '上传图片…', mask: true });
    let done = 0;
    try {
      for (const p of paths) {
        if (this.data.images.length >= MAX_IMAGES) break;
        const url = await uploadImage(p);
        done++;
        this.setData({
          images: this.data.images.concat([url]),
          imageUrls: this.data.imageUrls.concat([mediaUrl(url)])
        });
      }
    } catch (e) {
      const msg = (e as Error).message || '上传失败';
      wx.showToast({ title: done ? `已传 ${done} 张，${msg}` : msg, icon: 'none' });
    } finally {
      wx.hideLoading();
      this.setData({ imageUploading: false });
    }
  },

  onRemoveImage(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    if (idx < 0 || idx >= this.data.images.length) return;
    const images = this.data.images.slice();
    const imageUrls = this.data.imageUrls.slice();
    images.splice(idx, 1);
    imageUrls.splice(idx, 1);
    this.setData({ images, imageUrls });
  },

  /** 备注图片预览（当前张起步） */
  onPreviewImage(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const urls = this.data.imageUrls.slice().length
      ? this.data.imageUrls.slice()
      : this.data.images.map((u) => mediaUrl(u));
    if (!urls.length) return;
    wx.previewImage({ current: urls[idx] || urls[0], urls });
  },

  // ============================================================
  // 关联工序（可选）：模板一键填充 + 手工添加 + 改价/删行
  // ============================================================

  onOpenTplSheet() {
    this.setData({ tplSheetVisible: true });
    if (this.data.tplDown || !this.data.templates.length) {
      this.setData({ tplLoading: true, tplDown: false });
      fetchProcessTemplates()
        .then((res) => {
          const list = Array.isArray(res) ? res : ((res as unknown as { results?: ProcessTemplate[] }).results || []);
          this.setData({ templates: list, tplLoading: false });
          if (!list.length) {
            wx.showToast({ title: '暂无模板，可手工添加工序', icon: 'none' });
          }
        })
        .catch((e) => {
          this.setData({ tplLoading: false, tplDown: isServiceDown(e) });
        });
    }
  },

  onCloseTplSheet() {
    this.setData({ tplSheetVisible: false });
  },

  /** 选中模板 → 填充工序+工价行（可再改价/删行/追加） */
  onPickTemplate(e: WechatMiniprogram.TouchEvent) {
    const t = this.data.templates[Number(e.currentTarget.dataset.index)];
    if (!t) return;
    const nameMap: Record<number, string> = {};
    this.data.procs.forEach((p) => { nameMap[p.id] = p.name; });
    const rows: ProcRowVM[] = (t.items || []).map((it) => ({
      process_id: it.process_id,
      name: it.process_name || nameMap[it.process_id] || `工序${it.process_id}`,
      price: String(it.price || '')
    }));
    this.setData({ procRows: rows, tplSheetVisible: false });
    wx.showToast({ title: `已填充 ${rows.length} 道工序`, icon: 'none' });
  },

  /** 手工添加工序（下拉选择；重复添加提示） */
  onPickProc(e: WechatMiniprogram.PickerChange) {
    const idx = Number(e.detail.value);
    if (idx <= 0) return;
    const p = this.data.procs[idx - 1];
    if (!p) return;
    if (this.data.procRows.some((r) => r.process_id === p.id)) {
      wx.showToast({ title: '该工序已在列表', icon: 'none' });
      return;
    }
    const defPrice = String((p as Process & { default_price?: string }).default_price || '');
    this.setData({
      procRows: this.data.procRows.concat([{ process_id: p.id, name: p.name, price: defPrice }])
    });
  },

  onProcPrice(e: WechatMiniprogram.Input) {
    const idx = Number(e.currentTarget.dataset.index);
    this.setData({ [`procRows[${idx}].price`]: e.detail.value });
  },

  onRemoveProcRow(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const rows = this.data.procRows.slice();
    if (idx < 0 || idx >= rows.length) return;
    rows.splice(idx, 1);
    this.setData({ procRows: rows });
  },

  // ============ 颜色 / 尺码 chips（复用编菲矩阵交互） ============

  onAddColor() {
    const visible = !this.data.colorPanelVisible;
    this.setData({ colorPanelVisible: visible });
    if (visible && !this.data.colorOptions.length) {
      fetchProductionColors()
        .then((res) => this.buildColorOptions((res.results || []).map((r) => r.name)))
        .catch(() => this.buildColorOptions([]));
    }
  },

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
    if (!name || this.data.colors.indexOf(name) >= 0) return;
    this.setData({
      colors: this.data.colors.concat([name]),
      colorOptions: this.data.colorOptions.map(
        (o) => (o.name === name ? { ...o, added: true } : o))
    });
    this.rebuildMatrix();
  },

  onOpenColorCustom() {
    this.promptChip('添加颜色', '请输入颜色名称', (name) => {
      if (this.data.colors.indexOf(name) >= 0) {
        wx.showToast({ title: '该颜色已存在', icon: 'none' });
        return;
      }
      this.setData({
        colors: this.data.colors.concat([name]),
        colorOptions: this.data.colorOptions.map(
          (o) => (o.name === name ? { ...o, added: true } : o))
      });
      this.rebuildMatrix();
    });
  },

  onAddSize() {
    const visible = !this.data.sizePanelVisible;
    this.setData({ sizePanelVisible: visible });
    if (visible && !this.data.sizeOptions.length) {
      fetchProductionHints()
        .then((res) => this.buildSizeOptions((res.sizes || []).map((s) => s.name)))
        .catch(() => this.buildSizeOptions([]));
    }
  },

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
    if (!name || this.data.sizes.indexOf(name) >= 0) return;
    this.setData({
      sizes: this.data.sizes.concat([name]),
      sizeOptions: this.data.sizeOptions.map(
        (o) => (o.name === name ? { ...o, added: true } : o))
    });
    this.rebuildMatrix();
  },

  onOpenSizeCustom() {
    this.promptChip('添加尺码', '请输入尺码（如 M / XL）', (name) => {
      if (this.data.sizes.indexOf(name) >= 0) {
        wx.showToast({ title: '该尺码已存在', icon: 'none' });
        return;
      }
      this.setData({
        sizes: this.data.sizes.concat([name]),
        sizeOptions: this.data.sizeOptions.map(
          (o) => (o.name === name ? { ...o, added: true } : o))
      });
      this.rebuildMatrix();
    });
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
    wx.showModal({
      title: '删除颜色',
      content: `删除「${color}」将同时清除其已录入的数量，确定吗？`,
      success: (res) => {
        if (!res.confirm) return;
        this.setData({
          colors: this.data.colors.filter((v) => v !== color),
          cells: omitKey(this.data.cells, (c) => c.split('|')[0] === color),
          colorOptions: this.data.colorOptions.map(
            (o) => (o.name === color ? { ...o, added: false } : o))
        });
        this.rebuildMatrix();
      }
    });
  },

  onRemoveSize(e: WechatMiniprogram.TouchEvent) {
    const size = String(e.currentTarget.dataset.size);
    wx.showModal({
      title: '删除尺码',
      content: `删除「${size}」将同时清除其已录入的数量，确定吗？`,
      success: (res) => {
        if (!res.confirm) return;
        this.setData({
          sizes: this.data.sizes.filter((v) => v !== size),
          cells: omitKey(this.data.cells, (c) => c.split('|')[1] === size),
          sizeOptions: this.data.sizeOptions.map(
            (o) => (o.name === size ? { ...o, added: false } : o))
        });
        this.rebuildMatrix();
      }
    });
  },

  // ============ 矩阵渲染 + 视图切换 + 数字键盘（横向/纵向/倍数/排列，对齐编菲） ============

  onSwitchView(e: WechatMiniprogram.TouchEvent) {
    const key = String((e.currentTarget.dataset as { key?: string }).key || '');
    if (!key || key === this.data.view) return;
    this.setData({ view: key, sheetVisible: false });
    this.rebuildMatrix(); // 转置/模式变了必须重算（漏调会导致横向/纵向不生效）
  },

  rebuildMatrix() {
    const { view, colors, sizes, cells, multiCounts, multiBase } = this.data;
    const base = Number(multiBase) || 0;
    const byColor = view !== 'v'; // 纵向转置：尺码为行
    const rowKeys = byColor ? colors : sizes;
    const colKeys = byColor ? sizes : colors;
    const rows = rowKeys.map((rk) => {
      let total = 0;
      const rowCells = colKeys.map((ck) => {
        const key = byColor ? `${rk}|${ck}` : `${ck}|${rk}`;
        if (view === 'multi') {
          const count = multiCounts[key] || 0;
          total += base * count;
          return {
            key,
            main: count > 0 ? String(base * count) : '',
            sub: count > 0 ? `${base || 0}×${count}` : ''
          };
        }
        const qty = cells[key] || 0;
        total += qty;
        return { key, main: qty > 0 ? String(qty) : '', sub: '' };
      });
      return { label: rk, cells: rowCells, total };
    });
    const grandTotal = rows.reduce((s, r) => s + r.total, 0);
    this.setData({ mtxCols: colKeys, mtxRows: rows, grandTotal });
  },

  /**
   * 点行头：该行首个已填数量铺满整行（无已填值 toast 提示先填一个数）。
   * 纵向视图行=尺码，铺的是该尺码在各颜色下（cells 键仍按 色|尺码 组装）；
   * 倍数视图铺「份数」（取该行首个已录份数，件数=基数×份数 同步折算）。
   */
  onTapRowHead(e: WechatMiniprogram.TouchEvent) {
    const idx = Number((e.currentTarget.dataset as { row?: number }).row);
    const { view, colors, sizes, cells, multiCounts } = this.data;
    const byColor = view !== 'v';
    const rowKeys = byColor ? colors : sizes;
    const colKeys = byColor ? sizes : colors;
    const rk = rowKeys[idx];
    if (!rk) return;
    const multi = view === 'multi';
    const src = multi ? multiCounts : cells;
    let sample = 0;
    for (const ck of colKeys) {
      const v = src[`${rk}|${ck}`] || 0;
      if (v > 0) { sample = v; break; }
    }
    if (!sample) {
      wx.showToast({ title: multi ? '请先在该行填一个份数' : '请先在该行填一个数量', icon: 'none' });
      return;
    }
    const next = { ...cells };
    const nextCounts = multi ? { ...multiCounts } : multiCounts;
    colKeys.forEach((ck) => {
      const key = `${rk}|${ck}`;
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

  /** 点列头：该列首个已填数量铺满整列（纵向视图列=颜色；倍数视图铺份数） */
  onTapColHead(e: WechatMiniprogram.TouchEvent) {
    const idx = Number((e.currentTarget.dataset as { col?: number }).col);
    const { view, colors, sizes, cells, multiCounts } = this.data;
    const byColor = view !== 'v';
    const rowKeys = byColor ? colors : sizes;
    const colKeys = byColor ? sizes : colors;
    const ck = colKeys[idx];
    if (!ck) return;
    const multi = view === 'multi';
    const src = multi ? multiCounts : cells;
    let sample = 0;
    for (const rk of rowKeys) {
      const v = src[`${rk}|${ck}`] || 0;
      if (v > 0) { sample = v; break; }
    }
    if (!sample) {
      wx.showToast({ title: multi ? '请先在该列填一个份数' : '请先在该列填一个数量', icon: 'none' });
      return;
    }
    const next = { ...cells };
    const nextCounts = multi ? { ...multiCounts } : multiCounts;
    rowKeys.forEach((rk) => {
      const key = `${rk}|${ck}`;
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

  onTapCell(e: WechatMiniprogram.TouchEvent) {
    const key = String((e.currentTarget.dataset as { key?: string }).key || '');
    if (!key) return;
    if (this.data.view === 'multi') {
      const base = Number(this.data.multiBase) || 0;
      if (!base) {
        wx.showToast({ title: '请先设置倍数基数', icon: 'none' });
        this.openSheet('base', '倍数基数（件）', this.data.multiBase, '');
        return;
      }
      this.openSheet('count', `份数（基数 ${base}）`, String(this.data.multiCounts[key] || ''), key);
      return;
    }
    // 排列视图只读预览（一键铺满入口在视图头部），横向/纵向录件数
    this.openSheet('cell', '录入数量', String(this.data.cells[key] || ''), key);
  },

  openSheet(mode: 'cell' | 'count' | 'base' | 'array', title: string, value: string, key: string) {
    this.setData({ sheetVisible: true, sheetMode: mode, sheetTitle: title, sheetValue: value, sheetKey: key });
    wx.nextTick(() => this.syncKeypad());
  },

  // ---- 倍数 / 排列 的全局输入 ----

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
        wx.showToast({ title: '已铺满', icon: 'success' });
      }
    });
  },

  onCloseSheet() {
    this.setData({ sheetVisible: false });
  },

  onSheetInput(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ sheetValue: e.detail.value });
  },

  onSheetConfirm() {
    const { sheetMode, sheetKey, sheetValue, cells, multiCounts } = this.data;
    const n = Number(sheetValue) || 0;
    if (sheetMode === 'base') {
      // 基数变化：已录份数按新基数折算回 cells（cells 是唯一数据源）
      const base = n;
      const next = { ...cells };
      Object.keys(multiCounts).forEach((k) => {
        const q = base * (multiCounts[k] || 0);
        if (q > 0) next[k] = q; else delete next[k];
      });
      this.setData({ multiBase: sheetValue.trim(), cells: next, sheetVisible: false });
      this.rebuildMatrix();
      return;
    }
    if (sheetMode === 'array') {
      // 排列视图：每格件数存全局 arrayQty，铺满由「一键铺满」触发
      this.setData({ arrayQty: sheetValue.trim(), sheetVisible: false });
      return;
    }
    if (sheetMode === 'count') {
      if (!sheetKey) return;
      const base = Number(this.data.multiBase) || 0;
      if (n > 0 && !base) {
        wx.showToast({ title: '请先设置倍数基数', icon: 'none' });
        return;
      }
      const nextCounts = { ...multiCounts };
      const next = { ...cells };
      if (n > 0) {
        nextCounts[sheetKey] = n;
        next[sheetKey] = base * n;
      } else {
        delete nextCounts[sheetKey];
        delete next[sheetKey];
      }
      this.setData({ multiCounts: nextCounts, cells: next, sheetVisible: false });
      this.rebuildMatrix();
      return;
    }
    // cell（件数）：只写本格，不自动填充（2026-10-21 用户反馈：复制只由行头/列头点击触发）
    const next = { ...cells };
    if (n > 0) {
      next[sheetKey] = n;
    } else {
      delete next[sheetKey];
    }
    this.setData({ cells: next, sheetVisible: false });
    this.rebuildMatrix();
  },

  syncKeypad() {
    const keypad = this.selectComponent('#order-keypad') as WechatMiniprogram.Component.TrivialInstance | null;
    if (keypad && typeof (keypad as unknown as { setValue?: (v: string) => void }).setValue === 'function') {
      (keypad as unknown as { setValue: (v: string) => void }).setValue(this.data.sheetValue);
    }
  },

  // ============ 保存（保存前合计已实时展示在底部；单号不提交，后端自动生成） ============

  onSaveOrder() {
    if (this.data.saving) return;
    const { cells, colors, sizes, editingId } = this.data;
    if (!this.data.styleId) {
      wx.showToast({ title: '请先选择款式', icon: 'none' });
      return;
    }
    const items: Array<{ color: string; size: string; qty: number }> = [];
    colors.forEach((color) => {
      sizes.forEach((size) => {
        const qty = cells[`${color}|${size}`] || 0;
        if (qty > 0) items.push({ color, size, qty });
      });
    });
    if (!items.length) {
      wx.showToast({ title: '请先录入颜色×尺码数量', icon: 'none' });
      return;
    }
    if (this.data.grandTotal <= 0) {
      wx.showToast({ title: '总数量须大于 0', icon: 'none' });
      return;
    }
    // 工价行校验（留空按 0 提交，登记时再补价）
    const processItems = this.data.procRows.map((r) => ({
      process_id: r.process_id,
      price: String(r.price).trim() || '0'
    }));
    const payload: OrderPayload = {
      // 后端序列化器字段名是 style（传 style_id 后端收不到 → 400 请关联款式）
      style: this.data.styleId,
      customer: this.data.customer || null,
      delivery_date: this.data.deliveryDate || null,
      order_type: this.data.orderTypes[this.data.orderTypeIndex],
      remark: this.data.remark.trim(),
      images: this.data.images.slice(),
      process_items: processItems,
      items
    };
    this.setData({ saving: true });
    const done = () => {
      this.setData({ saving: false, stage: 'list' });
      wx.showToast({ title: editingId ? '订单已保存' : '订单已创建', icon: 'success' });
      this.reloadOrders();
    };
    const fail = (e: unknown) => {
      this.setData({ saving: false });
      if (isServiceDown(e)) {
        wx.showToast({ title: '服务升级中，请稍后再试', icon: 'none' });
      }
      /* 其余错误（400「字段名：原因」）toast 已由请求层统一弹出 */
    };
    if (editingId) {
      updateOrder(editingId, payload).then(done).catch(fail);
    } else {
      createOrder(payload).then(done).catch(fail);
    }
  }
});

/** 列表行 → VM（进度/交期灯在前端兜底自算，后端字段缺省不阻塞展示） */
function toVM(o: Order): OrderVM {
  const total = Number(o.total_qty) || 0;
  const registered = Number(o.registered_qty) || 0;
  const progress = total > 0
    ? Math.min(100, Math.round(Number(o.progress !== undefined && o.progress !== null ? o.progress : (registered / total) * 100)))
    : 0;
  let lamp = '';
  if (o.status === 'done') {
    lamp = 'green';
  } else if (o.delivery_date) {
    const days = o.days_left !== undefined && o.days_left !== null
      ? Number(o.days_left)
      : daysUntil(o.delivery_date);
    if (days < 0) lamp = 'red';
    else if (days <= 7) lamp = 'yellow';
    else lamp = 'green';
  }
  return {
    id: o.id,
    order_no: o.order_no,
    statusDisplay: o.status_display || statusText(o.status),
    statusCls: STATUS_CLS[o.status] || 'badge-gray',
    customerName: o.customer_name || '未指定客户',
    deliveryDate: o.delivery_date || '',
    lamp,
    progress,
    progressText: `${progress}%`,
    totalQty: total,
    registeredQty: registered
  };
}

function statusText(status: string): string {
  if (status === 'done') return '已完成';
  if (status === 'cancelled') return '已取消';
  return '生产中';
}

/** 交期 → 距今天数（days_left 缺省时前端兜底） */
function daysUntil(date: string): number {
  const d = new Date(`${date}T00:00:00`);
  if (Number.isNaN(d.getTime())) return 0;
  const now = new Date();
  const base = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((d.getTime() - base.getTime()) / 86400000);
}

/** 过滤 cells：pred(key) 命中的键剔除 */
function omitKey(cells: Record<string, number>, pred: (key: string) => boolean): Record<string, number> {
  const out: Record<string, number> = {};
  Object.keys(cells).forEach((k) => {
    if (!pred(k)) out[k] = cells[k];
  });
  return out;
}
