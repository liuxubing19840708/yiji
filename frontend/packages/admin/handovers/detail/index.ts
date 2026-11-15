/** 交接详情：正常全收、逐项差异、拒收、改派及事件留痕。 */
import { requireLogin } from '../../../../utils/auth';
import { ApiError } from '../../../../utils/request';
import { hasPerm } from '../../../../utils/perm';
import {
  fetchHandover, receiveHandover, rejectHandover, reassignHandover,
  Handover, HandoverItem, HandoverEvent, HandoverDifferenceType, ReceiveDifference
} from '../../../../services/handover';

interface DifferenceTypeOption {
  value: Exclude<HandoverDifferenceType, ''>;
  label: string;
}

interface ItemVM extends HandoverItem {
  difference: boolean;
  receivedInput: string;
  differenceIndex: number;
  differenceTypeText: string;
}

interface EventVM extends HandoverEvent {
  actionText: string;
  createdAtText: string;
}

interface DetailVM extends Omit<Handover, 'items' | 'events'> {
  items: ItemVM[];
  events: EventVM[];
  statusText: string;
  targetText: string;
  createdAtText: string;
  receivedAtText: string;
  canReceive: boolean;
  canReassign: boolean;
}

const TYPES: DifferenceTypeOption[] = [
  { value: 'shortage', label: '短少' },
  { value: 'overage', label: '多出' },
  { value: 'damage', label: '破损' },
  { value: 'wrong_item', label: '错货' },
  { value: 'quality', label: '质量问题' },
  { value: 'other', label: '其他' }
];
const ACTION_TEXT: Record<string, string> = {
  created: '发起交接', received: '接收', rejected: '拒收', reassigned: '改派', cancelled: '取消'
};
const STATUS_TEXT: Record<string, string> = {
  pending: '待接收', received: '已接收', rejected: '已拒收', cancelled: '已取消'
};

function timeText(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 16) : '';
}

function requestId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function retrySameIntent(error: unknown): boolean {
  return !(error instanceof ApiError) || error.status === 0 || error.status >= 500;
}

Page({
  receiveRequestId: '',
  rejectRequestId: '',
  reassignRequestId: '',

  data: {
    id: 0,
    denied: false,
    loading: true,
    error: false,
    detail: null as DetailVM | null,
    showDiscrepancies: false,
    receiveReason: '',
    typeNames: TYPES.map((item) => item.label),
    rejectMode: false,
    rejectReason: '',
    reassignMode: false,
    receiverInput: '',
    reassignReason: '',
    action: ''
  },

  onLoad(options: Record<string, string>) {
    const id = Number(options.id) || 0;
    this.setData({ id, loading: id > 0, error: id <= 0 });
  },

  onShow() {
    if (!requireLogin()) return;
    this.setData({ denied: !hasPerm('handover.view') });
    if (!this.data.denied && this.data.id) this.load();
  },

  onPullDownRefresh() {
    this.load().then(() => wx.stopPullDownRefresh());
  },

  load(): Promise<void> {
    if (!this.data.id) return Promise.resolve();
    this.setData({ loading: true, error: false });
    return fetchHandover(this.data.id).then((item) => {
      const detail: DetailVM = {
        ...item,
        statusText: STATUS_TEXT[item.status] || item.status,
        targetText: item.receiver_name || (item.target_group_no !== null ? `目标组 ${item.target_group_no}` : '未指定接收方'),
        createdAtText: timeText(item.created_at),
        receivedAtText: timeText(item.received_at),
        canReceive: item.status === 'pending' && hasPerm('handover.receive'),
        canReassign: item.status === 'pending' && hasPerm('handover.manage'),
        items: (item.items || []).map((row) => ({
          ...row,
          difference: false,
          receivedInput: String(row.quantity),
          differenceIndex: 0,
          differenceTypeText: (TYPES.find((type) => type.value === row.difference_type) || { label: '' }).label
        })),
        events: (item.events || []).map((event) => ({
          ...event,
          actionText: ACTION_TEXT[event.action] || event.action,
          createdAtText: timeText(event.created_at)
        }))
      };
      this.setData({
        loading: false,
        detail,
        showDiscrepancies: false,
        receiveReason: '',
        rejectMode: false,
        reassignMode: false,
        action: ''
      });
    }).catch(() => this.setData({ loading: false, error: true, action: '' }));
  },

  onRetry() {
    this.load();
  },

  onReceiveAll() {
    const detail = this.data.detail;
    if (!detail || !detail.canReceive || this.data.action) return;
    wx.showModal({
      title: '确认全部接收',
      content: `共 ${detail.total_quantity} 件，确认无数量、质量或货项差异？`,
      confirmText: '全部接收',
      success: (modal) => {
        if (!modal.confirm) return;
        this.setData({ action: 'receive' });
        receiveHandover(detail.id, {
          request_id: this.receiveRequestId || (this.receiveRequestId = requestId('receive'))
        })
          .then(() => {
            this.receiveRequestId = '';
            wx.showToast({ title: '已接收', icon: 'success' });
            this.load();
          })
          .catch((error) => {
            if (!retrySameIntent(error)) this.receiveRequestId = '';
            this.setData({ action: '' });
          });
      }
    });
  },

  onToggleDiscrepancies() {
    if (!this.data.detail || !this.data.detail.canReceive) return;
    this.setData({ showDiscrepancies: !this.data.showDiscrepancies });
  },

  onToggleItemDifference(e: WechatMiniprogram.TouchEvent) {
    const index = Number(e.currentTarget.dataset.index);
    const item = this.data.detail && this.data.detail.items[index];
    if (!item) return;
    this.receiveRequestId = '';
    this.setData({ [`detail.items[${index}].difference`]: !item.difference });
  },

  onReceivedInput(e: WechatMiniprogram.Input) {
    const index = Number(e.currentTarget.dataset.index);
    this.receiveRequestId = '';
    this.setData({ [`detail.items[${index}].receivedInput`]: e.detail.value });
  },

  onDifferenceTypeChange(e: WechatMiniprogram.PickerChange) {
    const index = Number(e.currentTarget.dataset.index);
    this.receiveRequestId = '';
    this.setData({ [`detail.items[${index}].differenceIndex`]: Number(e.detail.value) });
  },

  onDifferenceReasonInput(e: WechatMiniprogram.Input) {
    const index = Number(e.currentTarget.dataset.index);
    this.receiveRequestId = '';
    this.setData({ [`detail.items[${index}].reason`]: e.detail.value });
  },

  onReceiveReasonInput(e: WechatMiniprogram.Input) {
    this.receiveRequestId = '';
    this.setData({ receiveReason: e.detail.value });
  },

  onReceiveWithDifferences() {
    const detail = this.data.detail;
    if (!detail || !detail.canReceive || this.data.action) return;
    const differences: ReceiveDifference[] = [];
    for (let i = 0; i < detail.items.length; i += 1) {
      const item = detail.items[i];
      if (!item.difference) continue;
      const quantity = Number(item.receivedInput);
      const option = TYPES[item.differenceIndex];
      if (!option) {
        wx.showToast({ title: '请选择差异类型', icon: 'none' });
        return;
      }
      if (!/^\d+$/.test(item.receivedInput.trim()) || !Number.isInteger(quantity) ||
        (option.value !== 'overage' && quantity > item.quantity)) {
        wx.showToast({
          title: option.value === 'overage' ? '多出时实收须为非负整数' : `菲票${item.ticket}实收应为0至${item.quantity}的整数`,
          icon: 'none'
        });
        return;
      }
      if (!option || !item.reason.trim()) {
        wx.showToast({ title: '每项差异必须填写原因', icon: 'none' });
        return;
      }
      differences.push({
        id: item.id,
        received_quantity: quantity,
        difference_type: option.value,
        reason: item.reason.trim()
      });
    }
    if (!differences.length) {
      wx.showToast({ title: '请至少展开一项差异，或直接全部接收', icon: 'none' });
      return;
    }
    this.setData({ action: 'receive' });
    const reason = this.data.receiveReason.trim();
    receiveHandover(detail.id, {
      request_id: this.receiveRequestId || (this.receiveRequestId = requestId('receive')),
      reason: reason || undefined,
      items: differences
    }).then(() => {
      this.receiveRequestId = '';
      wx.showToast({ title: '差异接收成功', icon: 'success' });
      this.load();
    }).catch((error) => {
      if (!retrySameIntent(error)) this.receiveRequestId = '';
      this.setData({ action: '' });
    });
  },

  onShowReject() {
    if (this.data.detail && this.data.detail.canReceive) {
      this.setData({ rejectMode: true, reassignMode: false, rejectReason: '' });
    }
  },

  onRejectReasonInput(e: WechatMiniprogram.Input) {
    this.rejectRequestId = '';
    this.setData({ rejectReason: e.detail.value });
  },

  onReject() {
    const detail = this.data.detail;
    const reason = this.data.rejectReason.trim();
    if (!detail || this.data.action) return;
    if (!reason) {
      wx.showToast({ title: '请填写拒收原因', icon: 'none' });
      return;
    }
    this.setData({ action: 'reject' });
    rejectHandover(detail.id, {
      request_id: this.rejectRequestId || (this.rejectRequestId = requestId('reject')), reason
    })
      .then(() => {
        this.rejectRequestId = '';
        wx.showToast({ title: '已拒收', icon: 'success' });
        this.load();
      })
      .catch((error) => {
        if (!retrySameIntent(error)) this.rejectRequestId = '';
        this.setData({ action: '' });
      });
  },

  onShowReassign() {
    if (this.data.detail && this.data.detail.canReassign) {
      this.setData({ reassignMode: true, rejectMode: false, receiverInput: '', reassignReason: '' });
    }
  },

  onReceiverInput(e: WechatMiniprogram.Input) {
    this.reassignRequestId = '';
    this.setData({ receiverInput: e.detail.value });
  },

  onReassignReasonInput(e: WechatMiniprogram.Input) {
    this.reassignRequestId = '';
    this.setData({ reassignReason: e.detail.value });
  },

  onReassign() {
    const detail = this.data.detail;
    const receiver = Number(this.data.receiverInput);
    const reason = this.data.reassignReason.trim();
    if (!detail || this.data.action) return;
    if (!Number.isInteger(receiver) || receiver <= 0 || !reason) {
      wx.showToast({ title: '请填写有效接收人ID和改派原因', icon: 'none' });
      return;
    }
    this.setData({ action: 'reassign' });
    reassignHandover(detail.id, {
      request_id: this.reassignRequestId || (this.reassignRequestId = requestId('reassign')),
      receiver, reason
    })
      .then(() => {
        this.reassignRequestId = '';
        wx.showToast({ title: '已改派', icon: 'success' });
        this.load();
      })
      .catch((error) => {
        if (!retrySameIntent(error)) this.reassignRequestId = '';
        this.setData({ action: '' });
      });
  },

  onCancelForm() {
    this.setData({ rejectMode: false, reassignMode: false });
  },

  onOpenBatch() {
    const detail = this.data.detail;
    if (detail) {
      wx.navigateTo({ url: `/packages/admin/handovers/batch/index?batch_no=${encodeURIComponent(detail.batch_no)}` });
    }
  }
});
