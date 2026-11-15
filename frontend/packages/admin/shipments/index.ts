/**
 * 出货单列表页（shipment 域 P2）：
 * - 状态 chip（全部/draft 草稿/confirmed 全部出货/partial 部分出货；
 *   shipped/signed 为预留态，暂不出口径与按钮）；
 * - 客户筛选 + keyword（出货单号/客户名/订单号/物流单号，后端 Q 口径）；
 * - 新建出货 → 详情页 create 模式。
 * 权限：页面挂 shipment.view；建单/审核/作废等动作按 shipment.manage 显隐。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { fetchShipments, Shipment, ShipmentStatus } from '../../../services/shipment';
import { fetchCustomers } from '../../../services/catalog';
import type { Customer } from '../../../services/types';

const STATUS_CHIPS: Array<{ key: '' | ShipmentStatus; label: string }> = [
  { key: '', label: '全部' },
  { key: 'draft', label: '草稿' },
  { key: 'confirmed', label: '全部出货' },
  { key: 'partial', label: '部分出货' }
];

const STATUS_CLASS: Record<ShipmentStatus, string> = {
  draft: 'badge-gray',
  confirmed: 'badge-green',
  partial: 'badge-blue',
  shipped: 'badge-gray',
  signed: 'badge-gray'
};

const STATUS_TEXT: Record<ShipmentStatus, string> = {
  draft: '草稿',
  confirmed: '全部出货',
  partial: '部分出货',
  shipped: '已发货',
  signed: '已签收'
};

interface ShpVM {
  id: number;
  shipmentNo: string;
  customerName: string;
  orderNo: string;
  status: ShipmentStatus;
  statusText: string;
  statusClass: string;
  totalQty: number;
  boxes: number;
  shipDate: string;
  logisticsNo: string;
  remark: string;
  createdAt: string;
}

function toVM(r: Shipment): ShpVM {
  return {
    id: r.id,
    shipmentNo: r.shipment_no,
    customerName: r.customer_name || '—',
    orderNo: r.order_no || '',
    status: r.status,
    statusText: r.status_display || STATUS_TEXT[r.status] || r.status,
    statusClass: STATUS_CLASS[r.status] || 'badge-gray',
    totalQty: r.total_qty || 0,
    boxes: r.boxes || 0,
    shipDate: r.ship_date || '',
    logisticsNo: r.logistics_no || '',
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
    customers: [] as Customer[],
    customerNames: ['全部客户'],
    customerIndex: 0,
    rows: [] as ShpVM[],
    loading: false,
    error: false,
    page: 1,
    hasMore: false,
    loaded: false
  },

  onShow() {
    if (!requireLogin()) return;
    const denied = !hasPerm('shipment.view');
    this.setData({ denied, canManage: hasPerm('shipment.manage') });
    if (!denied) {
      if (!this.data.customers.length) {
        fetchCustomers({ page: 1, page_size: 200 })
          .then((res) => {
            const customers = res.results || [];
            this.setData({
              customers,
              customerNames: ['全部客户'].concat(customers.map((c) => c.name))
            });
          })
          .catch(() => undefined);
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

  onStatusChip(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    if (idx === this.data.statusIndex) return;
    this.setData({ statusIndex: idx });
    this.reload();
  },

  onCustomerChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ customerIndex: Number(e.detail.value) });
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
    const ci = this.data.customerIndex;
    this.setData({ loading: true, error: false });
    fetchShipments({
      page,
      page_size: 20,
      status: chip.key || undefined,
      customer_id: ci > 0 && this.data.customers[ci - 1]
        ? this.data.customers[ci - 1].id : undefined,
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
    wx.navigateTo({ url: `/packages/admin/shipment-detail/index?id=${id}` });
  },

  onOpenCreate() {
    wx.navigateTo({ url: '/packages/admin/shipment-detail/index?mode=create' });
  }
});
