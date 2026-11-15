/**
 * 到料暂收列表页（warehouse 域 P2）：
 * - 状态 chip（全部/draft 草稿/confirmed 已确认）+ keyword（单号/供应商/面料）；
 * - 新建暂收 → 详情页 create 模式（支持选采购单行带出或纯手工）；
 * - 需求汇总入口（同挂 warehouse.manage）。
 * 权限：页面挂 warehouse.manage（复用 P1 点）。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { fetchReceipts, Receipt } from '../../../services/warehouse';

const STATUS_CHIPS: Array<{ key: '' | 'draft' | 'confirmed'; label: string }> = [
  { key: '', label: '全部' },
  { key: 'draft', label: '草稿' },
  { key: 'confirmed', label: '已确认' }
];

const STATUS_CLASS: Record<string, string> = {
  draft: 'badge-gray',
  confirmed: 'badge-green'
};

const STATUS_TEXT: Record<string, string> = {
  draft: '草稿',
  confirmed: '已确认'
};

interface RecVM {
  id: number;
  receiptNo: string;
  supplier: string;
  purchaseNo: string;
  status: string;
  statusText: string;
  statusClass: string;
  itemCount: number;
  remark: string;
  createdAt: string;
}

function toVM(r: Receipt): RecVM {
  return {
    id: r.id,
    receiptNo: r.receipt_no,
    supplier: r.supplier_name || r.supplier_code || '—',
    purchaseNo: r.purchase_no || '',
    status: r.status,
    statusText: STATUS_TEXT[r.status] || r.status,
    statusClass: STATUS_CLASS[r.status] || 'badge-gray',
    itemCount: (r.items || []).length,
    remark: r.remark || '',
    createdAt: (r.created_at || '').replace('T', ' ').slice(0, 16)
  };
}

Page({
  data: {
    denied: false,
    canManage: false,
    statusChips: STATUS_CHIPS,
    statusIndex: 0,
    keyword: '',
    rows: [] as RecVM[],
    loading: false,
    error: false,
    page: 1,
    hasMore: false,
    loaded: false
  },

  onShow() {
    if (!requireLogin()) return;
    const denied = !hasPerm('warehouse.manage');
    this.setData({ denied, canManage: hasPerm('warehouse.manage') });
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
    fetchReceipts({
      page,
      page_size: 20,
      status: chip.key || undefined,
      keyword: this.data.keyword.trim() || undefined
    })
      .then((res) => {
        const next = (res as { next?: string | null }).next;
        const vms = (res.results || []).map(toVM);
        this.setData({
          loading: false,
          loaded: true,
          rows: page === 1 ? vms : this.data.rows.concat(vms),
          page,
          hasMore: !!next
        });
      })
      .catch(() => this.setData({ loading: false, error: true, loaded: true }));
  },

  onOpenDetail(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    if (!id) return;
    wx.navigateTo({ url: `/packages/admin/receipt-detail/index?id=${id}` });
  },

  onOpenCreate() {
    wx.navigateTo({ url: '/packages/admin/receipt-detail/index?mode=create' });
  },

  onGoDemandSummary() {
    wx.navigateTo({ url: '/packages/admin/demand-summary/index' });
  }
});
