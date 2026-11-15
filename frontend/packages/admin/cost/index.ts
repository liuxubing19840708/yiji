/**
 * 订单成本页（cost 域 P1，决策表 D1/D3）：
 * - 订单盈亏表列表：订单号/款式/收入/材料/人工/外发/制费/合计/单件成本/毛利额/毛利率；
 * - is_stale 行标「数据待更新」徽标（软提醒不拦截发货，D3）；
 * - missing_price_cnt>0 行标「缺价N行」（后端暂无下钻端点，点开弹口径说明：
 *   lot 无价/0 价不进成本，去入库单/批次档补价后重算——INT 待 SA3 下钻端点接入）；
 * - no_order_cnt 顶部汇总区提示「未归属成本床次 N 个」（未挂单床次成本不入订单）；
 * - recompute 单订单按钮 + recompute-batch（按当前日期范围，cost.manage 显隐）；
 * - overhead 录入弹层（cost.manage）；
 * - 工资对账单入口（cost.view 同点子页）。
 * 权限：页面挂 cost.view；重算/制费按钮挂 cost.manage。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { monthRange } from '../../../utils/format';
import {
  fetchOrderCosts, recomputeOrderCost, recomputeOrderCostsBatch,
  updateOrderOverhead, OrderCost
} from '../../../services/cost';

interface CostVM {
  id: number;
  order: number;
  orderNo: string;
  customerName: string;
  styleText: string;
  revenue: string;
  materialCost: string;
  laborCost: string;
  outsourcingCost: string;
  overheadCost: string;
  totalCost: string;
  outputQty: number;
  unitCost: string;
  grossProfit: string;
  grossMargin: string;
  noOrderCnt: number;
  missingPriceCnt: number;
  isStale: boolean;
  computedAt: string;
  computedByName: string;
  orderIndex: number;
}

function fmtMoney(v: string | null | undefined, dash = '—'): string {
  return v === null || v === undefined || v === '' ? dash : String(v);
}

function toVM(c: OrderCost): CostVM {
  return {
    id: c.id,
    order: c.order,
    orderNo: c.order_no,
    customerName: c.customer_name || '',
    styleText: [c.style_no, c.style_name].filter(Boolean).join(' '),
    revenue: fmtMoney(c.revenue, '0.00'),
    materialCost: fmtMoney(c.material_cost, '0.00'),
    laborCost: fmtMoney(c.labor_cost, '0.00'),
    outsourcingCost: fmtMoney(c.outsourcing_cost, '0.00'),
    overheadCost: fmtMoney(c.overhead_cost, '0.00'),
    totalCost: fmtMoney(c.total_cost, '0.00'),
    outputQty: c.output_qty || 0,
    unitCost: fmtMoney(c.unit_cost),
    grossProfit: fmtMoney(c.gross_profit),
    grossMargin: c.gross_margin === null || c.gross_margin === undefined ? '—' : `${c.gross_margin}%`,
    noOrderCnt: c.no_order_cnt || 0,
    missingPriceCnt: c.missing_price_cnt || 0,
    isStale: !!c.is_stale,
    computedAt: (c.computed_at || '').replace('T', ' ').slice(0, 16),
    computedByName: c.computed_by_name || '',
    orderIndex: -1
  };
}

Page({
  data: {
    denied: false,
    canManage: false,
    // ---- 过滤 ----
    start: '',
    end: '',
    quick: 'month',
    keyword: '',
    // ---- 列表 ----
    rows: [] as CostVM[],
    loading: false,
    error: false,
    page: 1,
    hasMore: false,
    /** 顶部汇总：未归属成本床次（可见页求和，口径=后端逐行 no_order_cnt） */
    noOrderTotal: 0,
    // ---- 批量重算 ----
    batchRunning: false,
    // ---- 制费弹层 ----
    overheadVisible: false,
    overheadSaving: false,
    overheadOrderIndex: -1,
    overheadValue: ''
  },

  onShow() {
    if (!requireLogin()) return;
    const denied = !hasPerm('cost.view');
    this.setData({
      denied,
      canManage: hasPerm('cost.manage')
    });
    if (!denied) {
      if (!this.data.start) {
        const r = monthRange();
        this.setData({ start: r.start, end: r.end, quick: 'month' });
      }
      this.reload();
    }
  },

  onPullDownRefresh() {
    this.reload();
    wx.stopPullDownRefresh();
  },

  onReachBottom() {
    if (this.data.hasMore && !this.data.loading) this.loadPage(this.data.page + 1);
  },

  // ============ 过滤 ============

  onRangeChange(e: WechatMiniprogram.CustomEvent<{ start: string; end: string; quick: string }>) {
    this.setData({ start: e.detail.start, end: e.detail.end, quick: e.detail.quick || '' });
  },

  onKeyword(e: WechatMiniprogram.Input) {
    this.setData({ keyword: e.detail.value });
  },

  onSearch() {
    this.reload();
  },

  // ============ 列表 ============

  reload() {
    this.loadPage(1);
  },

  loadPage(page: number) {
    const { start, end, keyword } = this.data;
    this.setData({ loading: true, error: false });
    fetchOrderCosts({
      page,
      page_size: 20,
      date_from: start || undefined,
      date_to: end || undefined,
      keyword: keyword.trim() || undefined
    })
      .then((res) => {
        const vms = (res.results || []).map(toVM);
        // no_order_cnt 顶部汇总：本页求和（跨页口径由过滤条件收敛）
        const noOrderTotal = vms.reduce((s, r) => s + r.noOrderCnt, 0);
        this.setData({
          loading: false,
          rows: page === 1 ? vms : this.data.rows.concat(vms),
          page,
          hasMore: !!res.next,
          noOrderTotal: page === 1 ? noOrderTotal : this.data.noOrderTotal + noOrderTotal
        });
      })
      .catch(() => this.setData({ loading: false, error: true }));
  },

  // ============ 缺价说明（后端暂无下钻端点，弹口径说明） ============

  onMissingPrice() {
    wx.showModal({
      title: '缺价行说明',
      content: '缺价 = 该订单关联批次无单价或 0 价（lot 无价/0 价不进材料成本）。请到「轻量仓库-入库单」核对批次单价（带采购行自动取行价），补价后点「重算」即入成本。',
      showCancel: false
    });
  },

  // ============ 重算（cost.manage） ============

  onRecompute(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const row = this.data.rows[idx];
    if (!row) return;
    wx.showLoading({ title: '重算中…' });
    recomputeOrderCost(row.order)
      .then(() => {
        wx.hideLoading();
        wx.showToast({ title: '已重算', icon: 'success' });
        this.reload();
      })
      .catch(() => wx.hideLoading());
  },

  /** 批量重算：按当前日期范围（订单创建日期），跑完刷新 */
  onRecomputeBatch() {
    const { start, end } = this.data;
    if (!start && !end) {
      wx.showToast({ title: '请先选日期范围（批量按订单创建日期）', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '批量重算',
      content: `将重算创建日期 ${start || '起'} ~ ${end || '今'} 范围内全部订单成本，确定？`,
      success: (m) => {
        if (!m.confirm) return;
        this.setData({ batchRunning: true });
        wx.showLoading({ title: '批量重算中…' });
        recomputeOrderCostsBatch({ date_from: start || undefined, date_to: end || undefined })
          .then((r) => {
            wx.hideLoading();
            this.setData({ batchRunning: false });
            const failCnt = (r.failed || []).length;
            wx.showModal({
              title: '批量重算完成',
              content: `成功 ${r.count} 单${failCnt ? `，失败 ${failCnt} 单（详情看后端返回）` : ''}。`,
              showCancel: false,
              success: () => this.reload()
            });
          })
          .catch(() => {
            wx.hideLoading();
            this.setData({ batchRunning: false });
          });
      }
    });
  },

  // ============ 制费弹层（cost.manage） ============

  onOpenOverhead(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const row = this.data.rows[idx];
    if (!row) return;
    this.setData({
      overheadVisible: true,
      overheadOrderIndex: idx,
      overheadValue: row.overheadCost === '0.00' ? '' : row.overheadCost
    });
  },

  onOverheadInput(e: WechatMiniprogram.Input) {
    this.setData({ overheadValue: e.detail.value });
  },

  onCloseOverhead() {
    this.setData({ overheadVisible: false });
  },

  noop() { /* 阻止冒泡 */ },

  onSaveOverhead() {
    const idx = this.data.overheadOrderIndex;
    const row = this.data.rows[idx];
    const v = this.data.overheadValue;
    if (!row) return;
    const num = Number(v);
    if (v === '' || !Number.isFinite(num) || num < 0) {
      wx.showToast({ title: '请填写不小于 0 的制费金额', icon: 'none' });
      return;
    }
    this.setData({ overheadSaving: true });
    updateOrderOverhead(row.order, v)
      .then(() => {
        this.setData({ overheadSaving: false, overheadVisible: false });
        wx.showToast({ title: '制费已保存（待重算生效）', icon: 'none' });
        this.reload();
      })
      .catch(() => this.setData({ overheadSaving: false }));
  },

  // ============ 工资对账单入口（cost.view 同点） ============

  onGoWageReconciliation() {
    wx.navigateTo({ url: '/packages/admin/cost-wage/index' });
  },

  /** 订单损益视图（2026-11-02 评审 Top10 #2）：两口径单订单毛利/应收实绩 */
  onGoOrderPnl() {
    wx.navigateTo({ url: '/packages/admin/order-pnl/index' });
  }
});
