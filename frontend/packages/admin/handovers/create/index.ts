/**
 * 批量发起交接。菲票、节点与接收目标只能由调用方传入：
 * 1. page options.payload = encodeURIComponent(JSON.stringify(CreateOptions))；
 * 2. 或 opener EventChannel 发送同名 payload 事件。
 * 本页不请求、不猜测菲票/节点/接收人业务列表。
 */
import { requireLogin } from '../../../../utils/auth';
import { ApiError } from '../../../../utils/request';
import { hasPerm } from '../../../../utils/perm';
import {
  createHandovers, HandoverCreatePayload, HandoverCreateTarget
} from '../../../../services/handover';

interface CreateItemInput {
  ticket: number;
  quantity: number;
  ticket_label?: string;
}

interface CreateTargetInput extends HandoverCreateTarget {
  receiver_name?: string;
  target_group_name?: string;
  items: CreateItemInput[];
}

export interface HandoverCreateOptions {
  kind: 'cutting';
  source_node: number;
  target_node: number;
  source_node_name?: string;
  target_node_name?: string;
  remark?: string;
  targets: CreateTargetInput[];
}

interface TargetVM extends CreateTargetInput {
  targetText: string;
  totalQuantity: number;
}

function positiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0;
}

function requestId(): string {
  return `handover-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

Page({
  data: {
    denied: false,
    ready: false,
    invalidReason: '',
    sourceNode: 0,
    targetNode: 0,
    sourceNodeName: '',
    targetNodeName: '',
    remark: '',
    targets: [] as TargetVM[],
    totalQuantity: 0,
    totalTickets: 0,
    submitting: false,
    batchNo: '',
    createdCount: 0
  },

  requestId: '',

  onLoad(options: Record<string, string>) {
    if (options.payload) {
      try {
        this.acceptPayload(JSON.parse(decodeURIComponent(options.payload)) as HandoverCreateOptions);
      } catch (e) {
        this.setData({ invalidReason: '交接参数不是有效 JSON，请返回业务页重新进入' });
      }
    }
    try {
      this.getOpenerEventChannel().on('payload', (payload: HandoverCreateOptions) => {
        this.acceptPayload(payload);
      });
    } catch (e) { /* 直接 navigateTo 打开时没有 opener channel */ }
  },

  onShow() {
    if (!requireLogin()) return;
    this.setData({ denied: !hasPerm('handover.create') });
  },

  acceptPayload(input: HandoverCreateOptions) {
    if (!input || input.kind !== 'cutting' ||
      !positiveInteger(input.source_node) || !positiveInteger(input.target_node) ||
      !Array.isArray(input.targets) || !input.targets.length) {
      this.setData({ ready: false, invalidReason: '缺少有效的裁切节点或交接目标' });
      return;
    }

    const ticketIds: number[] = [];
    let duplicate = false;
    let invalid = false;
    let totalQuantity = 0;
    const targets: TargetVM[] = input.targets.map((target) => {
      if ((!positiveInteger(target.receiver) && !positiveInteger(target.target_group_no)) ||
        !Array.isArray(target.items) || !target.items.length) invalid = true;
      let total = 0;
      const items = (target.items || []).map((item) => {
        if (!positiveInteger(item.ticket) || !positiveInteger(item.quantity)) invalid = true;
        if (ticketIds.indexOf(item.ticket) >= 0) duplicate = true;
        ticketIds.push(item.ticket);
        total += positiveInteger(item.quantity) ? item.quantity : 0;
        return { ...item, ticket_label: item.ticket_label || `菲票 ${item.ticket}` };
      });
      const targetText = target.receiver_name ||
        (positiveInteger(target.receiver) ? `接收人 ${target.receiver}` : '') ||
        target.target_group_name || `目标组 ${target.target_group_no}`;
      return {
        ...target,
        receiver: positiveInteger(target.receiver) ? target.receiver : null,
        target_group_no: positiveInteger(target.target_group_no) ? target.target_group_no : null,
        items,
        targetText,
        totalQuantity: total
      };
    });
    totalQuantity = targets.reduce((sum, target) => sum + target.totalQuantity, 0);
    if (invalid || duplicate) {
      this.setData({
        ready: false,
        invalidReason: duplicate ? '同一菲票不能分配给多个接收目标' : '接收目标、菲票或数量无效'
      });
      return;
    }
    this.requestId = requestId();
    this.setData({
      ready: true,
      invalidReason: '',
      sourceNode: input.source_node,
      targetNode: input.target_node,
      sourceNodeName: input.source_node_name || '',
      targetNodeName: input.target_node_name || '',
      remark: input.remark || '',
      targets,
      totalQuantity,
      totalTickets: ticketIds.length,
      batchNo: '',
      createdCount: 0
    });
  },

  onRemarkInput(e: WechatMiniprogram.Input) {
    this.requestId = '';
    this.setData({ remark: e.detail.value });
  },

  onSubmit() {
    if (!this.data.ready || this.data.denied || this.data.submitting || this.data.batchNo) return;
    const targets: HandoverCreateTarget[] = this.data.targets.map((target) => ({
      receiver: target.receiver,
      target_group_no: target.target_group_no,
      items: target.items.map((item) => ({ ticket: item.ticket, quantity: item.quantity }))
    }));
    const payload: HandoverCreatePayload = {
      kind: 'cutting',
      request_id: this.requestId || (this.requestId = requestId()),
      source_node: this.data.sourceNode,
      target_node: this.data.targetNode,
      remark: this.data.remark.trim(),
      targets
    };
    this.setData({ submitting: true });
    createHandovers(payload).then((res) => {
      this.setData({
        submitting: false,
        batchNo: res.batch_no,
        createdCount: (res.items || []).length
      });
      wx.showToast({ title: '发起成功', icon: 'success' });
    }).catch((error) => {
      // 网络/服务故障重试沿用原幂等号；明确业务失败或修改备注后生成新意图。
      const retrySame = !(error instanceof ApiError) || error.status === 0 || error.status >= 500;
      if (!retrySame) this.requestId = '';
      this.setData({ submitting: false });
    });
  },

  onViewBatch() {
    if (!this.data.batchNo) return;
    wx.redirectTo({
      url: `/packages/admin/handovers/batch/index?batch_no=${encodeURIComponent(this.data.batchNo)}`
    });
  },

  onBack() {
    wx.navigateBack();
  }
});
