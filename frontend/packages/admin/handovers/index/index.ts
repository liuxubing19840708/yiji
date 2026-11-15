/** 交接列表：待我接收 / 我发起；无差异接收可在列表直接完成。 */
import { requireLogin } from '../../../../utils/auth';
import { ApiError } from '../../../../utils/request';
import { hasPerm } from '../../../../utils/perm';
import {
  fetchHandovers, receiveHandover, Handover, HandoverStatus
} from '../../../../services/handover';

interface HandoverRow extends Handover {
  statusText: string;
  targetText: string;
  createdAtText: string;
  canAccept: boolean;
}

const STATUS_TEXT: Record<HandoverStatus, string> = {
  pending: '待接收', received: '已接收', rejected: '已拒收', cancelled: '已取消'
};

function decorate(item: Handover, canReceive: boolean): HandoverRow {
  return {
    ...item,
    statusText: STATUS_TEXT[item.status] || item.status,
    targetText: item.receiver_name || (item.target_group_no !== null ? `目标组 ${item.target_group_no}` : '未指定接收方'),
    createdAtText: (item.created_at || '').replace('T', ' ').slice(0, 16),
    canAccept: canReceive && item.status === 'pending'
  };
}

const CREATED_STATUSES: Array<{ value: '' | HandoverStatus; label: string }> = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '待接收' },
  { value: 'received', label: '已接收' },
  { value: 'rejected', label: '已拒收' },
  { value: 'cancelled', label: '已取消' }
];

Page({
  receiveRequestIds: {} as Record<number, string>,

  data: {
    denied: false,
    box: 'mine' as 'mine' | 'created',
    createdStatus: '' as '' | HandoverStatus,
    createdStatusIndex: 0,
    createdStatusLabels: CREATED_STATUSES.map((item) => item.label),
    loading: true,
    error: false,
    loadingMore: false,
    rows: [] as HandoverRow[],
    page: 1,
    hasMore: false,
    receivingId: 0,
    canCreate: false,
    canManage: false,
    batchNo: ''
  },

  onLoad(options: Record<string, string>) {
    if (options.batch_no) this.setData({ batchNo: options.batch_no });
  },

  onShow() {
    if (!requireLogin()) return;
    this.setData({
      denied: !hasPerm('handover.view'),
      canCreate: hasPerm('handover.create'),
      canManage: hasPerm('handover.manage')
    });
    if (!this.data.denied) this.load(true);
  },

  onPullDownRefresh() {
    this.load(true).then(() => wx.stopPullDownRefresh());
  },

  onReachBottom() {
    if (this.data.hasMore && !this.data.loadingMore && !this.data.loading) this.load(false);
  },

  load(reset: boolean): Promise<void> {
    const page = reset ? 1 : this.data.page + 1;
    this.setData(reset ? { loading: true, error: false } : { loadingMore: true, error: false });
    return fetchHandovers({
      box: this.data.box,
      status: this.data.box === 'mine' ? 'pending' : (this.data.createdStatus || undefined),
      batch_no: this.data.batchNo || undefined,
      kind: 'cutting',
      page,
      page_size: 20
    }).then((res) => {
      const canReceive = hasPerm('handover.receive');
      const incoming = (res.results || []).map((item) => decorate(item, canReceive));
      const rows = reset ? incoming : this.data.rows.concat(incoming);
      this.setData({
        loading: false,
        loadingMore: false,
        rows,
        page,
        hasMore: !!res.next
      });
    }).catch(() => {
      this.setData({ loading: false, loadingMore: false, error: true });
    });
  },

  onRetry() {
    this.load(true);
  },

  onBoxChange(e: WechatMiniprogram.TouchEvent) {
    const box = e.currentTarget.dataset.box as 'mine' | 'created';
    if (box === this.data.box) return;
    this.setData({ box, batchNo: '', rows: [], page: 1 }, () => this.load(true));
  },

  onCreatedStatusChange(e: WechatMiniprogram.PickerChange) {
    const index = Number(e.detail.value);
    const option = CREATED_STATUSES[index];
    if (!option) return;
    this.setData({
      createdStatus: option.value,
      createdStatusIndex: index,
      rows: [],
      page: 1
    }, () => this.load(true));
  },

  onCreate() {
    if (!this.data.canCreate) return;
    wx.navigateTo({
      url: '/packages/admin/full-production/index?entry=all&tab=cutting&handover_batch=1'
    });
  },

  onOpen(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    if (id) wx.navigateTo({ url: `/packages/admin/handovers/detail/index?id=${id}` });
  },

  onOpenBatch(e: WechatMiniprogram.TouchEvent) {
    const batchNo = String(e.currentTarget.dataset.batch || '');
    if (batchNo) {
      wx.navigateTo({ url: `/packages/admin/handovers/batch/index?batch_no=${encodeURIComponent(batchNo)}` });
    }
  },

  onAccept(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    if (!id || this.data.receivingId) return;
    wx.showModal({
      title: '确认全部接收',
      content: '将按交接单数量全量接收；如有短少、破损等差异，请进入详情登记。',
      confirmText: '全部接收',
      success: (modal) => {
        if (!modal.confirm) return;
        this.setData({ receivingId: id });
        const request_id = this.receiveRequestIds[id]
          || (this.receiveRequestIds[id] = `receive-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`);
        receiveHandover(id, { request_id })
          .then(() => {
            delete this.receiveRequestIds[id];
            wx.showToast({ title: '已接收', icon: 'success' });
            this.setData({ receivingId: 0 }, () => this.load(true));
          })
          .catch((error: unknown) => {
            const retrySame = !(error instanceof ApiError) || error.status === 0 || error.status >= 500;
            if (!retrySame) delete this.receiveRequestIds[id];
            this.setData({ receivingId: 0 });
          });
      }
    });
  },

  onPolicy() {
    if (this.data.canManage) wx.navigateTo({ url: '/packages/admin/handovers/policy/index' });
  }
});
