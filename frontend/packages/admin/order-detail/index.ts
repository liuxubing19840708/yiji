/**
 * 订单详情（规格 §2.5.2/§五 P1 新增）：
 * - 基本信息（状态徽标 + 交期灯 绿/黄≤7天/红）；
 * - 色码矩阵每格「订单数/已登记/完成率」，落后格（<80%）红底，汇总行整单完成率；
 * - 关联床次列表（GET /productions?order_id=）+「＋开床」直达建床向导（order_id 预挂）；
 * - 状态操作确认弹窗（生产中→已完成/已取消；满登记提示一键标完成；删除后端拦挂单）。
 * 后端 /orders 由并行 SA1 开发中：try/catch 兜底，404/未就绪提示「服务升级中」不白屏。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { ApiError } from '../../../utils/request';
import { mediaUrl } from '../../../utils/media';
import { fetchStyle } from '../../../services/catalog';
import {
  fetchOrder, fetchOrderProgress, fetchOrderProductions,
  updateOrderStatus, deleteOrder
} from '../../../services/orders';
import { Order, OrderItem, OrderProgress, Production, Paginated, Style, Ticket } from '../../../services/types';

/** 落后格阈值（§2.5.2 决策：<80% 红底） */
const LOW_THRESHOLD = 80;

/** 状态徽标样式 */
const STATUS_CLS: Record<string, string> = {
  producing: 'badge',
  done: 'badge-green',
  cancelled: 'badge-gray'
};

interface DetailCell {
  size: string;
  orderQty: number;
  regQty: number;
  pct: number;
  low: boolean;
}

interface DetailRow {
  color: string;
  cells: DetailCell[];
  /** 行合计：订单数/已登记/完成率（合计列三行显示，与床次矩阵同构） */
  total: number;
  regTotal: number;
  totalPct: number;
}

/** 接口未就绪（404）判定 */
function isServiceDown(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}

Page({
  data: {
    id: 0,
    loading: false,
    error: false,
    serviceDown: false,
    orderNo: '',
    status: 'producing',
    statusDisplay: '生产中',
    statusCls: 'badge',
    customerName: '',
    deliveryDate: '',
    /** 交期灯：''=无交期 / green / yellow / red */
    lamp: '',
    daysLeftText: '',
    orderType: '',
    remark: '',
    /** 备注图片（mediaUrl 补域名后，previewImage 用） */
    remarkImages: [] as string[],
    /** 传给 style-craft 组件的款式对象（款号/名称/图 + 工艺说明/图/视频，字段可能缺省） */
    styleInfo: null as Record<string, unknown> | null,
    /** 款式卡默认收起，点击展开时才拉取工艺详情（2026-10-22 用户需求） */
    styleId: 0,
    styleExpanded: false,
    styleLoaded: false,
    totalQty: 0,
    registeredQty: 0,
    progress: 0,
    /** 满登记提示一键标完成（status=producing 且完成率≥100%） */
    fullRegister: false,
    // ---- 色码矩阵 ----
    matrixLoaded: false,
    matrixFailed: false,
    sizes: [] as string[],
    rows: [] as DetailRow[],
    regGrand: 0,
    // ---- 关联床次 ----
    productions: [] as Array<{
      id: number; bedNo: string; styleNo: string; styleName: string;
      totalQty: number; registeredQty: number; progress: number; cutDone: boolean; locked: boolean;
    }>,
    productionsLoading: false,
    // ---- 操作 ----
    canOpenBed: false,
    canPreproductionView: false,
    acting: false
  },

  onLoad(options: Record<string, string>) {
    this.setData({
      id: Number(options.id) || 0,
      // ＋开床按钮按 production.manage 点显隐（挂单开床是生产动作）
      canOpenBed: hasPerm('production.manage'),
      canPreproductionView: hasPerm('preproduction.view')
    });
  },

  onShow() {
    if (!requireLogin()) return;
    this.reload();
  },

  onPullDownRefresh() {
    this.reload();
    wx.stopPullDownRefresh();
  },

  reload() {
    if (!this.data.id) return;
    this.setData({ loading: true, error: false });
    fetchOrder(this.data.id)
      .then((o: Order) => {
        const items = o.items || [];
        const total = Number(o.total_qty) || items.reduce((s, it) => s + (it.qty || 0), 0);
        const registered = Number(o.registered_qty) || 0;
        const progress = total > 0
          ? Math.min(100, Math.round(Number(o.progress !== undefined && o.progress !== null ? o.progress : (registered / total) * 100)))
          : 0;
        // 交期灯：绿/黄(距交期≤7天)/红(过期未完成)；无交期无灯
        let lamp = '';
        let daysLeftText = '';
        if (o.status === 'done') {
          lamp = 'green';
        } else if (o.delivery_date) {
          const days = o.days_left !== undefined && o.days_left !== null
            ? Number(o.days_left)
            : daysUntil(o.delivery_date);
          if (days < 0) { lamp = 'red'; daysLeftText = `已过期 ${Math.abs(days)} 天`; }
          else if (days <= 7) { lamp = 'yellow'; daysLeftText = days === 0 ? '今天到期' : `剩 ${days} 天`; }
          else { lamp = 'green'; daysLeftText = `剩 ${days} 天`; }
        }
        wx.setNavigationBarTitle({ title: o.order_no || '订单详情' });
        this.setData({
          loading: false,
          serviceDown: false,
          orderNo: o.order_no || '',
          status: o.status,
          statusDisplay: o.status_display || statusText(o.status),
          statusCls: STATUS_CLS[o.status] || 'badge-gray',
          customerName: o.customer_name || '未指定客户',
          deliveryDate: o.delivery_date || '',
          lamp,
          daysLeftText,
          orderType: o.order_type || '',
          remark: o.remark || '',
          remarkImages: (o.images || []).map((u) => mediaUrl(u)),
          styleId: Number(o.style || 0),
          styleInfo: o.style
            ? { style_no: o.style_no || '', name: o.style_name || '', style_image: o.style_image || '' }
            : null,
          totalQty: total,
          registeredQty: registered,
          progress,
          fullRegister: o.status === 'producing' && progress >= 100
        });
        this.buildMatrix(items);
        // 款式详情（工艺说明/图/视频）改为点开款式卡时按需拉取（2026-10-22 用户需求：默认收起）
      })
      .catch((e) => {
        this.setData({
          loading: false,
          error: true,
          serviceDown: isServiceDown(e)
        });
      });
    this.loadProductions();
  },

  // ============ 款式卡（默认收起，点开展开并按需拉工艺详情） ============

  onToggleStyle() {
    const target = !this.data.styleExpanded;
    this.setData({ styleExpanded: target });
    // 首次展开才补拉工艺说明/图/视频（Order 只带款式基础字段）；失败保留基础字段
    if (target && !this.data.styleLoaded && this.data.styleId) {
      const orderId = this.data.id;
      fetchStyle(this.data.styleId)
        .then((s: Style) => {
          if (this.data.id !== orderId) return;
          const raw = s as unknown as Record<string, unknown>;
          this.setData({
            styleLoaded: true,
            styleInfo: {
              ...(this.data.styleInfo || {}),
              craft_note: String(raw.craft_note || ''),
              craft_images: Array.isArray(raw.craft_images) ? raw.craft_images : [],
              video: String(raw.video || '')
            }
          });
        })
        .catch(() => { /* 工艺详情拉取失败：只展示款式基础字段 */ });
    }
  },

  // ============ 色码矩阵（订单数/已登记/完成率，落后格红底） ============

  /** 先以订单 items 为分母建矩阵（完成率=0），再由 progress 接口填分子。
   *  items 是 (颜色,尺码) 平铺——按颜色聚合成一行，同色多尺码不重复占行 */
  buildMatrix(items: OrderItem[]) {
    const sizes: string[] = [];
    items.forEach((it) => {
      if (sizes.indexOf(it.size) < 0) sizes.push(it.size);
    });
    const byColor = new Map<string, Map<string, number>>();
    const order: string[] = [];
    items.forEach((it) => {
      if (!byColor.has(it.color)) {
        byColor.set(it.color, new Map());
        order.push(it.color);
      }
      byColor.get(it.color)!.set(it.size, (byColor.get(it.color)!.get(it.size) || 0) + it.qty);
    });
    const rows: DetailRow[] = order.map((color) => {
      const qtyBySize = byColor.get(color)!;
      let total = 0;
      const cells = sizes.map((size) => {
        const qty = qtyBySize.get(size) || 0;
        total += qty;
        return { size, orderQty: qty, regQty: 0, pct: 0, low: false };
      });
      return { color, cells, total, regTotal: 0, totalPct: 0 };
    });
    this.setData({ matrixLoaded: true, sizes, rows, regGrand: 0 });
    this.loadProgress();
  },

  loadProgress() {
    fetchOrderProgress(this.data.id)
      .then((res) => {
        // 防御性归一化：后端并行开发中，兼容 cells/results 平铺与 rows 嵌套两种结构
        const map = normalizeProgress(res);
        this.applyProgress(map);
      })
      .catch((e) => {
        if (isServiceDown(e)) {
          wx.showToast({ title: '完成度服务升级中', icon: 'none' });
          this.setData({ matrixFailed: true });
        }
        /* 其余失败：矩阵保留 0 完成率展示 */
      });
  },

  applyProgress(map: Map<string, { reg: number; pct: number }>) {
    let regGrand = 0;
    const rows = this.data.rows.map((row) => {
      let regTotal = 0;
      const cells = row.cells.map((cell) => {
        const hit = map.get(`${row.color}|${cell.size}`);
        const regQty = hit ? hit.reg : 0;
        const pct = cell.orderQty > 0
          ? Math.min(100, Math.round(hit && hit.pct !== undefined ? hit.pct : (regQty / cell.orderQty) * 100))
          : 0;
        regTotal += regQty;
        return { ...cell, regQty, pct, low: cell.orderQty > 0 && pct < LOW_THRESHOLD };
      });
      regGrand += regTotal;
      const totalPct = row.total > 0 ? Math.min(100, Math.round((regTotal / row.total) * 100)) : 0;
      return { ...row, cells, regTotal, totalPct };
    });
    this.setData({ rows, regGrand, matrixFailed: false });
  },

  // ============ 关联床次 ============

  loadProductions() {
    this.setData({ productionsLoading: true });
    fetchOrderProductions(this.data.id, { page_size: 50 })
      .then((res: Paginated<Production>) => {
        const list = (res.results || []).map((p) => {
          const raw = p as unknown as Record<string, unknown>;
          const total = Number(p.total_qty) || 0;
          const reg = Number(raw.registered_qty) || 0;
          return {
            id: p.id,
            bedNo: p.bed_no,
            styleNo: p.style_no || '',
            styleName: p.style_name || '',
            totalQty: total,
            registeredQty: reg,
            progress: total > 0 ? Math.min(100, Math.round((reg / total) * 100)) : 0,
            cutDone: !!p.is_cut_done,
            locked: !!p.is_locked
          };
        });
        this.setData({ productionsLoading: false, productions: list });
      })
      .catch(() => this.setData({ productionsLoading: false }));
  },

  onOpenBed() {
    // ＋开床：直达建床向导，预挂本单（向导自动带出客户+色码余量）
    wx.navigateTo({ url: `/packages/admin/production-create/index?order_id=${this.data.id}` });
  },

  onOpenPreproduction() {
    wx.navigateTo({ url: `/packages/admin/preproduction/index?id=${this.data.id}` });
  },

  /** 备注图片预览（当前张起步，全组可左右滑） */
  onPreviewRemarkImage(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    if (!this.data.remarkImages.length) return;
    wx.previewImage({
      current: this.data.remarkImages[idx] || this.data.remarkImages[0],
      urls: this.data.remarkImages
    });
  },

  onTapProduction(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    if (!id) return;
    wx.navigateTo({ url: `/packages/admin/production-detail/index?id=${id}` });
  },

  // ============ 状态操作（确认弹窗） ============

  onEdit() {
    wx.navigateTo({ url: `/packages/admin/orders/index?id=${this.data.id}` });
  },

  /** 变更留痕时间线（差距 #3，2026-11-03）：改价/改状态/删除等审计聚合页 */
  onOpenAuditLogs() {
    wx.navigateTo({ url: `/packages/admin/order-audit/index?id=${this.data.id}` });
  },

  onMarkDone() {
    if (this.data.acting) return;
    const below = this.data.progress < 100;
    wx.showModal({
      title: '标记完成',
      content: below
        ? `当前完成率 ${this.data.progress}%（未满 100%），确定标记为已完成吗？`
        : '登记已满，确定标记为已完成吗？',
      success: (res) => {
        if (!res.confirm) return;
        this.setData({ acting: true });
        updateOrderStatus(this.data.id, 'done')
          .then(() => {
            this.setData({ acting: false });
            wx.showToast({ title: '已标记完成', icon: 'success' });
            this.reload();
          })
          .catch(() => this.setData({ acting: false }));
      }
    });
  },

  onCancelOrder() {
    if (this.data.acting) return;
    wx.showModal({
      title: '取消订单',
      content: '取消后订单不再计入生产动线，确定取消该订单吗？',
      confirmColor: '#FA5151',
      success: (res) => {
        if (!res.confirm) return;
        this.setData({ acting: true });
        updateOrderStatus(this.data.id, 'cancelled')
          .then(() => {
            this.setData({ acting: false });
            wx.showToast({ title: '已取消订单', icon: 'success' });
            this.reload();
          })
          .catch(() => this.setData({ acting: false }));
      }
    });
  },

  onDeleteOrder() {
    if (this.data.acting) return;
    wx.showModal({
      title: '删除订单',
      content: '已挂床次的订单不可删除（只可取消），确定删除吗？',
      confirmColor: '#FA5151',
      success: (res) => {
        if (!res.confirm) return;
        this.setData({ acting: true });
        deleteOrder(this.data.id)
          .then(() => {
            this.setData({ acting: false });
            wx.showToast({ title: '已删除', icon: 'success' });
            setTimeout(() => wx.navigateBack(), 600);
          })
          .catch(() => this.setData({ acting: false }));
      }
    });
  }
});

/** 交期 → 距今天数（days_left 缺省时前端兜底） */
function daysUntil(date: string): number {
  const d = new Date(`${date}T00:00:00`);
  if (Number.isNaN(d.getTime())) return 0;
  const now = new Date();
  const base = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((d.getTime() - base.getTime()) / 86400000);
}

function statusText(status: string): string {
  if (status === 'done') return '已完成';
  if (status === 'cancelled') return '已取消';
  return '生产中';
}

/**
 * progress 响应归一化：key=`${color}|${size}` → {reg, pct}。
 * 兼容结构（SA1 并行开发中的防御口径）：
 *   {cells|results: [{color,size,registered_qty,progress?}]} 或
 *   {rows: [{color, sizes: [{size, registered_qty, progress?}]}]}
 */
function normalizeProgress(res: OrderProgress): Map<string, { reg: number; pct: number }> {
  const map = new Map<string, { reg: number; pct: number }>();
  const raw = res as unknown as Record<string, unknown>;
  const flat = (res && res.cells)
    || (raw.items as OrderProgress['cells'] | undefined)   // SA1 实际返回 items
    || (raw.results as OrderProgress['cells'] | undefined) || [];
  if (Array.isArray(flat)) {
    flat.forEach((c) => {
      if (c && c.color && c.size) {
        map.set(`${c.color}|${c.size}`, {
          reg: Number(c.registered_qty) || 0,
          pct: Number(c.progress) || 0
        });
      }
    });
  }
  if (!map.size && raw && Array.isArray(raw.rows)) {
    (raw.rows as Array<{ color: string; sizes?: Array<{ size: string; registered_qty?: number; progress?: number }> }>).forEach((row) => {
      (row.sizes || []).forEach((c) => {
        if (row.color && c && c.size) {
          map.set(`${row.color}|${c.size}`, {
            reg: Number(c.registered_qty) || 0,
            pct: Number(c.progress) || 0
          });
        }
      });
    });
  }
  return map;
}
