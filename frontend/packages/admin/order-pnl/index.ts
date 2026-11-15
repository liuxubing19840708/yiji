/**
 * 订单损益页（2026-11-02 评审 Top10 #2）：单订单一行，老板"这单赚没赚"一屏答。
 * - 两口径并列：理论（revenue−total_cost=gross_profit，OrderCost 算定）与
 *   实收（ar_received−total_cost=real_profit，含折扣/尾数/坏账差异）；
 * - 应收实绩：应收/已收/未回三列（Receivable 按 order 聚合，不挂单应收不计）；
 * - is_stale 行黄标"待重算"（上游领料/登记/制费变动未重算，软提醒不拦截）；
 * - 排序：默认（-id）→ 毛利↓ → 毛利↑（本地排序，同应收页口径）；
 * - 空态引导：无成本行时提示先在成本页重算。
 * 权限：cost.view（只读页，无管理动作）。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { fetchOrderPnl, OrderPnlRow } from '../../../services/cost';

const STATUS_CHIPS: Array<{ key: string; label: string }> = [
  { key: '', label: '全部' },
  { key: 'producing', label: '生产中' },
  { key: 'done', label: '已完成' },
  { key: 'cancelled', label: '已取消' }
];

/** 排序模式循环：0 默认 → 1 毛利↓ → 2 毛利↑ */
const SORT_LABELS = ['排序:默认', '排序:毛利↓', '排序:毛利↑'];

interface PnlVM {
  id: number;
  orderId: number;
  orderNo: string;
  customerName: string;
  styleNo: string;
  revenue: string;
  totalCost: string;
  grossProfit: string;
  grossMargin: string | null;
  arAmount: string;
  arReceived: string;
  arGap: string;
  realProfit: string;
  realNeg: boolean;
  isStale: boolean;
  computedAt: string;
}

function toVM(r: OrderPnlRow): PnlVM {
  const real = Number(r.real_profit) || 0;
  return {
    id: r.id,
    orderId: r.order,
    orderNo: r.order_no,
    customerName: r.customer_name || '',
    styleNo: r.style_no || '',
    revenue: r.revenue,
    totalCost: r.total_cost,
    grossProfit: r.gross_profit,
    grossMargin: r.gross_margin,
    arAmount: r.ar_amount,
    arReceived: r.ar_received,
    arGap: r.ar_gap,
    realProfit: r.real_profit,
    realNeg: real < 0,
    isStale: !!r.is_stale,
    computedAt: (r.computed_at || '').replace('T', ' ').slice(0, 16)
  };
}

Page({
  data: {
    denied: false,
    statusChips: STATUS_CHIPS,
    statusIndex: 0,
    sortLabels: SORT_LABELS,
    sortMode: 0,
    keyword: '',
    rows: [] as PnlVM[],
    loading: false,
    error: false,
    page: 1,
    hasMore: false,
    loaded: false,
    /** 汇总条（当前已加载页的理论/实收/未回合计——分页只累计已加载部分） */
    sumRevenue: '0',
    sumRealProfit: '0',
    sumGap: '0'
  },

  onShow() {
    if (!requireLogin()) return;
    const denied = !hasPerm('cost.view');
    this.setData({ denied });
    if (!denied) this.reload();
  },

  onPullDownRefresh() {
    this.reload();
    wx.stopPullDownRefresh();
  },

  onReachBottom() {
    if (this.data.hasMore && !this.data.loading) this.loadPage(this.data.page + 1);
  },

  // ============ 过滤 ============

  onStatusChip(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    if (idx === this.data.statusIndex) return;
    this.setData({ statusIndex: idx });
    this.reload();
  },

  onKeyword(e: WechatMiniprogram.Input) {
    this.setData({ keyword: e.detail.value });
  },

  onSearch() {
    this.reload();
  },

  onToggleSort() {
    this.setData({ sortMode: (this.data.sortMode + 1) % 3 });
    this.applyLocalSort();
  },

  /** 毛利本地排序（后端无排序参数；默认恢复 -id 后端序） */
  applyLocalSort() {
    const mode = this.data.sortMode;
    if (mode === 0) {
      this.setData({ rows: this.data.rows.slice().sort((a, b) => b.id - a.id) });
      return;
    }
    const dir = mode === 1 ? -1 : 1;
    this.setData({
      rows: this.data.rows.slice().sort((a, b) => (Number(a.grossProfit) - Number(b.grossProfit)) * dir)
    });
  },

  // ============ 列表 ============

  reload() {
    this.loadPage(1);
  },

  loadPage(page: number) {
    const chip = STATUS_CHIPS[this.data.statusIndex];
    this.setData({ loading: true, error: false });
    fetchOrderPnl({
      page,
      page_size: 20,
      status: chip.key || undefined,
      keyword: this.data.keyword.trim() || undefined
    })
      .then((res) => {
        const vms = (res.results || []).map(toVM);
        const rows = page === 1 ? vms : this.data.rows.concat(vms);
        this.setData({
          loading: false,
          loaded: true,
          rows,
          page,
          hasMore: !!res.next
        }, () => {
          this.recalcSummary();
          if (page === 1) this.applyLocalSort();
        });
      })
      .catch(() => this.setData({ loading: false, error: true, loaded: true }));
  },

  /** 已加载行的合计（本地累加，分页补加载后自动刷新；口径=理论/实收/未回） */
  recalcSummary() {
    let revenue = 0;
    let real = 0;
    let gap = 0;
    for (const r of this.data.rows) {
      revenue += Number(r.revenue) || 0;
      real += Number(r.realProfit) || 0;
      gap += Number(r.arGap) || 0;
    }
    this.setData({
      sumRevenue: revenue.toFixed(2),
      sumRealProfit: real.toFixed(2),
      sumGap: gap.toFixed(2)
    });
  },

  onOpenOrder(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.order);
    if (!id) return;
    wx.navigateTo({ url: `/packages/admin/order-detail/index?id=${id}` });
  },

  noop() { /* 阻止冒泡 */ }
});
