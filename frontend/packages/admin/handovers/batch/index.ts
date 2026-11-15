/** 批次实时汇总：batch_no 仅用于聚合，不虚构批次主单状态。 */
import { requireLogin } from '../../../../utils/auth';
import { hasPerm } from '../../../../utils/perm';
import { fetchHandoverBatch, Handover, HandoverBatch } from '../../../../services/handover';

interface BatchRow extends Handover {
  statusText: string;
  targetText: string;
}

const STATUS_TEXT: Record<string, string> = {
  pending: '待接收', received: '已接收', rejected: '已拒收', cancelled: '已取消'
};

Page({
  data: {
    batchNo: '',
    denied: false,
    loading: true,
    error: false,
    summary: null as HandoverBatch['summary'] | null,
    rows: [] as BatchRow[]
  },

  onLoad(options: Record<string, string>) {
    let batchNo = options.batch_no || '';
    try { batchNo = decodeURIComponent(batchNo); } catch (e) { /* 保留原值 */ }
    this.setData({ batchNo });
  },

  onShow() {
    if (!requireLogin()) return;
    this.setData({ denied: !hasPerm('handover.view') });
    if (!this.data.denied) this.load();
  },

  onPullDownRefresh() {
    this.load().then(() => wx.stopPullDownRefresh());
  },

  load(): Promise<void> {
    if (!this.data.batchNo) {
      this.setData({ loading: false, error: true });
      return Promise.resolve();
    }
    this.setData({ loading: true, error: false });
    return fetchHandoverBatch(this.data.batchNo).then((res) => {
      this.setData({
        loading: false,
        summary: res.summary,
        rows: (res.items || []).map((item) => ({
          ...item,
          statusText: STATUS_TEXT[item.status] || item.status,
          targetText: item.receiver_name || (item.target_group_no !== null ? `目标组 ${item.target_group_no}` : '未指定接收方')
        }))
      });
    }).catch(() => this.setData({ loading: false, error: true }));
  },

  onRetry() {
    this.load();
  },

  onOpen(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    if (id) wx.navigateTo({ url: `/packages/admin/handovers/detail/index?id=${id}` });
  }
});
