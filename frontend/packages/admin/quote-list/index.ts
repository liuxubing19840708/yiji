/**
 * 报价列表页（quotation 域 P2，SA-P2-3 后端已交付）：
 * - 状态筛选 chip（全部/draft 草稿/sent 已发送/won 已成单/lost 已输单）；
 * - keyword 搜索：报价单号/客户名/款号/款式名模糊（后端 Q 口径）；
 * - 单号 Q+日期+3 位自动号；行展示 款式/客户/数量/总价/毛利率/状态徽标；
 * - won 单未关联订单显示「待关联订单」角标（回填对比软提醒入口）；
 * - 新建按钮挂 quotation.manage → quote-edit。
 * 权限：页面挂 quotation.view；新建/行内管理动作按 quotation.manage 显隐。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { fetchQuotations, QuotationListItem, QuoteStatus } from '../../../services/quotation';

/** 状态 chip 定义（draft→sent/won/lost 流转，won/lost 终态） */
const STATUS_CHIPS: Array<{ key: '' | QuoteStatus; label: string }> = [
  { key: '', label: '全部' },
  { key: 'draft', label: '草稿' },
  { key: 'sent', label: '已发送' },
  { key: 'won', label: '已成单' },
  { key: 'lost', label: '已输单' }
];

/** 行 VM：展示字段预格式化（金额字符串透传，不自算） */
interface QuoteVM {
  id: number;
  quoteNo: string;
  customerName: string;
  styleText: string;
  qty: number;
  totalPrice: string;
  profitRate: string;
  status: QuoteStatus;
  statusText: string;
  statusClass: string;
  quoteDate: string;
  orderNo: string;
  needLinkOrder: boolean;
  isFinal: boolean;
  canManage: boolean;
}

const STATUS_CLASS: Record<QuoteStatus, string> = {
  draft: 'badge-gray',
  sent: 'badge-blue',
  won: 'badge-green',
  lost: 'badge-red'
};

function toVM(q: QuotationListItem, canManage: boolean): QuoteVM {
  return {
    id: q.id,
    quoteNo: q.quote_no,
    customerName: q.customer_name || '',
    styleText: [q.style_no, q.style_name].filter(Boolean).join(' '),
    qty: q.qty,
    totalPrice: q.total_price,
    profitRate: q.profit_rate === null || q.profit_rate === undefined
      ? '—' : `${q.profit_rate}%`,
    status: q.status,
    statusText: q.status_display || q.status,
    statusClass: STATUS_CLASS[q.status] || 'badge-gray',
    quoteDate: (q.quote_date || '').slice(0, 10),
    orderNo: q.order_no || '',
    needLinkOrder: q.status === 'won' && !q.order,
    isFinal: q.status === 'won' || q.status === 'lost',
    canManage
  };
}

Page({
  data: {
    denied: false,
    canManage: false,
    statusChips: STATUS_CHIPS,
    statusIndex: 0,
    keyword: '',
    rows: [] as QuoteVM[],
    loading: false,
    error: false,
    page: 1,
    hasMore: false,
    loaded: false
  },

  onShow() {
    if (!requireLogin()) return;
    const denied = !hasPerm('quotation.view');
    this.setData({
      denied,
      canManage: hasPerm('quotation.manage')
    });
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

  // ============ 列表 ============

  reload() {
    this.loadPage(1);
  },

  loadPage(page: number) {
    const chip = STATUS_CHIPS[this.data.statusIndex];
    this.setData({ loading: true, error: false });
    fetchQuotations({
      page,
      page_size: 20,
      status: chip.key || undefined,
      keyword: this.data.keyword.trim() || undefined
    })
      .then((res) => {
        const canManage = this.data.canManage;
        const vms = (res.results || []).map((q) => toVM(q, canManage));
        this.setData({
          loading: false,
          loaded: true,
          rows: page === 1 ? vms : this.data.rows.concat(vms),
          page,
          hasMore: !!res.next
        });
      })
      .catch(() => this.setData({ loading: false, error: true, loaded: true }));
  },

  // ============ 跳转 ============

  onOpenDetail(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    if (!id) return;
    wx.navigateTo({ url: `/packages/admin/quote-detail/index?id=${id}` });
  },

  onCreate() {
    wx.navigateTo({ url: '/packages/admin/quote-edit/index' });
  }
});
