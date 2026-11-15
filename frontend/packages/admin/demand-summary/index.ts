/**
 * 采购需求汇总页（warehouse 域 P2；P2.5 安全库存 2026-11-02 对齐华遨）：
 * - 款式/客户筛选 + GET /demand-summary（视图计算不落库）；
 * - summary 计数条（物料数/需采购数/订单口径分布/水位预警数）；
 * - conversion_pending_cnt>0 显「待补参数」黄条（缺克重/幅宽的行不入合计）；
 * - rows 物料聚合表：需求/库存/在途/建议采购量（>0 高亮）；
 * - P2.5「含安全库存」开关：切到补货点口径 qty_reorder（含水位，只增不减）；
 *   设了水位的物料即使无订单需求也出现在表里（纯水位预警行）；
 * - P2.5「一键生成采购单」：按当前口径把 >0 行写成草稿采购单
 *   （demand_source=summary_import、物料锚点、单号 CG 自动），跳采购列表改单。
 * 口径：qty_suggested=Σ(active BOM gross_usage×未投产件数)−库存−在途（不含水位）；
 * qty_reorder=max(0, 同式+safety_stock)。权限：warehouse.manage。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import {
  fetchDemandSummary, createPurchaseOrder, DemandSummary, DemandSummaryRow
} from '../../../services/warehouse';
import { fetchStyles, fetchCustomers } from '../../../services/catalog';
import type { Style, Customer } from '../../../services/types';

interface RowVM extends DemandSummaryRow {
  suggestedPositive: boolean;
  suggestedNegative: boolean;
  /** 当前口径（开关切换）下的应采量 */
  qtyPick: string;
  pickPositive: boolean;
}

function toVM(r: DemandSummaryRow, withSafety: boolean): RowVM {
  const sug = Number(r.qty_suggested || 0);
  const pick = withSafety ? Number(r.qty_reorder || 0) : sug;
  return {
    ...r,
    suggestedPositive: sug > 0,
    suggestedNegative: sug < 0,
    qtyPick: withSafety ? r.qty_reorder : r.qty_suggested,
    pickPositive: pick > 0
  };
}

Page({
  data: {
    denied: false,
    loading: false,
    error: false,
    loaded: false,
    styles: [] as Style[],
    styleNames: ['全部款式'],
    styleIndex: 0,
    customers: [] as Customer[],
    customerNames: ['全部客户'],
    customerIndex: 0,
    summary: null as DemandSummary['summary'] | null,
    rows: [] as RowVM[],
    /** P2.5 补货点口径开关（含安全库存） */
    withSafety: false,
    /** P2.5 一键生成采购单提交中 */
    genBusy: false
  },

  onShow() {
    if (!requireLogin()) return;
    const denied = !hasPerm('warehouse.manage');
    this.setData({ denied });
    if (denied) return;
    if (!this.data.styles.length) this.loadFilters();
    this.reload();
  },

  onPullDownRefresh() {
    this.reload();
    wx.stopPullDownRefresh();
  },

  loadFilters() {
    Promise.all([
      fetchStyles({ page: 1, page_size: 200 }),
      fetchCustomers({ page: 1, page_size: 200 })
    ])
      .then(([styleRes, custRes]) => {
        const styles = styleRes.results || [];
        const customers = custRes.results || [];
        this.setData({
          styles,
          styleNames: ['全部款式'].concat(styles.map((s) => s.style_no || String(s.id))),
          customers,
          customerNames: ['全部客户'].concat(customers.map((c) => c.name))
        });
      })
      .catch(() => undefined); // 筛选器拉取失败不阻塞汇总查询（可不筛）
  },

  onStyleChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ styleIndex: Number(e.detail.value) }, () => this.reload());
  },

  onCustomerChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ customerIndex: Number(e.detail.value) }, () => this.reload());
  },

  /** 含安全库存开关：纯前端口径切换（qty_reorder 后端已随行下发，不重拉） */
  onToggleSafety() {
    const withSafety = !this.data.withSafety;
    this.setData({
      withSafety,
      rows: this.data.rows.map((r) => toVM(r, withSafety))
    });
  },

  /** 一键生成草稿采购单：当前口径 pick>0 的行 → POST /purchase-orders */
  onGeneratePo() {
    if (this.data.genBusy) return;
    const picks = this.data.rows.filter((r) => r.pickPositive);
    if (!picks.length) {
      wx.showToast({ title: '当前口径没有需采购的物料', icon: 'none' });
      return;
    }
    const label = this.data.withSafety
      ? `含安全库存 ${picks.length} 行` : `${picks.length} 行空差`;
    wx.showModal({
      title: '生成采购单',
      content: `按当前口径（${label}）生成一张草稿采购单？生成后请到轻量仓库补供应商与单价。`,
      success: (r) => {
        if (!r.confirm) return;
        this.setData({ genBusy: true });
        createPurchaseOrder({
          remark: `需求汇总生成（${this.data.withSafety ? '含安全库存' : '订单空差'}口径）`,
          items: picks.map((p) => ({
            fabric_name: p.material_name,
            qty: p.qtyPick,
            unit: p.unit,
            material_id: p.material_id,
            demand_source: 'summary_import'
          }))
        })
          .then(() => {
            this.setData({ genBusy: false });
            wx.showToast({ title: '草稿采购单已生成', icon: 'success' });
            wx.navigateTo({ url: '/packages/admin/warehouse/index' });
          })
          .catch(() => this.setData({ genBusy: false }));
      }
    });
  },

  reload() {
    const style = this.data.styleIndex > 0
      ? this.data.styles[this.data.styleIndex - 1].id : undefined;
    const customer = this.data.customerIndex > 0
      ? this.data.customers[this.data.customerIndex - 1].id : undefined;
    this.setData({ loading: true, error: false });
    fetchDemandSummary({ style_id: style, customer_id: customer })
      .then((res) => {
        this.setData({
          loading: false,
          loaded: true,
          summary: res.summary,
          rows: (res.rows || []).map((r) => toVM(r, this.data.withSafety))
        });
      })
      .catch(() => this.setData({ loading: false, error: true, loaded: true }));
  }
});
