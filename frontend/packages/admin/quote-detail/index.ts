/**
 * 报价详情页（quotation 域 P2）：
 * - 详情直显：用料行（两级用量：BOM定额行/手选物料，amount 随行下发透传）、
 *   人工行（rate_warning_msg 超界直显不改值，D2；addon_amount 附加费单独项）、
 *   算定合计（服务端重算，前端不累加）；
 * - 流转（quotation.manage）：draft→发送/成单/输单，sent→成单/输单；
 *   won/lost 为终态无流转按钮（后端 400 兜底）；成单弹层选关联订单（可暂不关联）；
 * - 回填对比入口（quotation.view 同点子页）；
 * - 编辑/删除（quotation.manage）。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import {
  fetchQuotation, markQuotationStatus, deleteQuotation,
  QuotationDetail, QuoteMaterialRow
} from '../../../services/quotation';
import { fetchOrders } from '../../../services/orders';
import type { Order } from '../../../services/types';

interface MatVM extends QuoteMaterialRow {
  title: string;
  unit: string;
}

/** 流转按钮按当前状态生成（won/lost 终态 = 空数组） */
const FLOW_ACTIONS: Record<string, Array<{ status: 'sent' | 'won' | 'lost'; label: string; cls: string }>> = {
  draft: [
    { status: 'sent', label: '标记已发送', cls: 'act' },
    { status: 'won', label: '标记成单', cls: 'act act-won' },
    { status: 'lost', label: '标记输单', cls: 'act act-lost' }
  ],
  sent: [
    { status: 'won', label: '标记成单', cls: 'act act-won' },
    { status: 'lost', label: '标记输单', cls: 'act act-lost' }
  ]
};

Page({
  data: {
    denied: false,
    canManage: false,
    id: 0,
    loading: true,
    error: false,
    quote: null as QuotationDetail | null,
    mats: [] as MatVM[],
    flowActions: [] as Array<{ status: 'sent' | 'won' | 'lost'; label: string; cls: string }>,
    // ---- 成单弹层（选关联订单） ----
    wonVisible: false,
    wonSaving: false,
    orders: [] as Array<{ id: number; label: string }>,
    orderNames: [] as string[],
    orderIndex: 0,
    flowTarget: '' as '' | 'sent' | 'won' | 'lost'
  },

  onLoad(query: Record<string, string | undefined>) {
    this.setData({
      id: Number(query.id || 0),
      denied: !hasPerm('quotation.view'),
      canManage: hasPerm('quotation.manage')
    });
  },

  onShow() {
    if (!requireLogin()) return;
    if (this.data.denied || !this.data.id) return;
    this.load();
  },

  load() {
    this.setData({ loading: true, error: false });
    fetchQuotation(this.data.id)
      .then((q) => {
        const mats: MatVM[] = (q.materials || []).map((m) => ({
          ...m,
          title: `${m.material_code || ''} ${m.material_name || ''}`.trim() || '—',
          unit: m.material_unit || ''
        }));
        this.setData({
          loading: false,
          quote: q,
          mats,
          flowActions: this.data.canManage ? (FLOW_ACTIONS[q.status] || []) : []
        });
      })
      .catch(() => this.setData({ loading: false, error: true }));
  },

  // ============ 状态流转（quotation.manage） ============

  onFlow(e: WechatMiniprogram.TouchEvent) {
    const target = String(e.currentTarget.dataset.status || '') as 'sent' | 'won' | 'lost';
    if (!target) return;
    if (target === 'won') {
      // won 弹层选关联订单（won→won 带 order_id 补挂/改挂后端已放行，缺口 C 修）
      if (!this.data.orders.length) {
        fetchOrders({ page: 1, page_size: 200 })
          .then((res) => {
            const orders = (res.results || []).map((o: Order) => ({ id: o.id, label: o.order_no }));
            this.setData({
              orders,
              orderNames: ['暂不关联订单'].concat(orders.map((o) => o.label)),
              orderIndex: 0,
              wonVisible: true,
              flowTarget: target
            });
          })
          .catch(() => {
            this.setData({
              orders: [],
              orderNames: ['暂不关联订单'],
              orderIndex: 0,
              wonVisible: true,
              flowTarget: target
            });
          });
      } else {
        this.setData({ wonVisible: true, flowTarget: target });
      }
      return;
    }
    wx.showModal({
      title: target === 'sent' ? '标记已发送' : '标记输单',
      content: target === 'sent'
        ? '发送后仍可成单/输单，确定？'
        : '输单为终态（不可再流转），确定？',
      success: (m) => {
        if (m.confirm) this.doMark(target);
      }
    });
  },

  onOrderChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ orderIndex: Number(e.detail.value) });
  },

  onCloseWon() {
    this.setData({ wonVisible: false });
  },

  noop() { /* 阻止冒泡 */ },

  onConfirmWon() {
    const target = this.data.flowTarget as 'won';
    const idx = this.data.orderIndex;
    // index 0 = 暂不关联；>=1 → orders[idx-1]
    const orderId = idx > 0 && this.data.orders[idx - 1] ? this.data.orders[idx - 1].id : undefined;
    this.setData({ wonSaving: true });
    markQuotationStatus(this.data.id, { status: target, order_id: orderId })
      .then(() => {
        this.setData({ wonSaving: false, wonVisible: false });
        wx.showToast({ title: '已标记成单', icon: 'success' });
        this.load();
      })
      .catch(() => this.setData({ wonSaving: false }));
  },

  doMark(target: 'sent' | 'won' | 'lost') {
    wx.showLoading({ title: '提交中…' });
    markQuotationStatus(this.data.id, { status: target })
      .then(() => {
        wx.hideLoading();
        wx.showToast({ title: '已更新', icon: 'success' });
        this.load();
      })
      .catch(() => wx.hideLoading());
  },

  // ============ 编辑 / 删除 / 回填对比 ============

  onEdit() {
    wx.navigateTo({ url: `/packages/admin/quote-edit/index?id=${this.data.id}` });
  },

  onDelete() {
    wx.showModal({
      title: '删除报价单',
      content: `删除后列表不再显示（软删，单号流水不复用）。确定删除 ${this.data.quote ? this.data.quote.quote_no : ''} ？`,
      confirmColor: '#F53F3F',
      success: (m) => {
        if (!m.confirm) return;
        wx.showLoading({ title: '删除中…' });
        deleteQuotation(this.data.id)
          .then(() => {
            wx.hideLoading();
            wx.showToast({ title: '已删除', icon: 'success' });
            setTimeout(() => wx.navigateBack(), 600);
          })
          .catch(() => wx.hideLoading());
      }
    });
  },

  onGoCompare() {
    wx.navigateTo({ url: `/packages/admin/quote-compare/index?id=${this.data.id}` });
  },

  /** 转订单：跳订单页并按本报价单预填（客户/款式/款式工序价），色码矩阵留白待填 */
  onToOrder() {
    wx.navigateTo({ url: `/packages/admin/orders/index?quote=${this.data.id}` });
  }
});
