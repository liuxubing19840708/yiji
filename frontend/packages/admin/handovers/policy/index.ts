/** 裁片交接策略：公司默认、节点覆盖规则与服务端超时提醒。 */
import { requireLogin } from '../../../../utils/auth';
import { hasPerm } from '../../../../utils/perm';
import {
  fetchHandoverPolicies, createHandoverPolicy, updateHandoverPolicy,
  HandoverPolicy, HandoverPolicyPayload
} from '../../../../services/handover';

function optionalId(value: string): number | null {
  if (!value.trim()) return null;
  const id = Number(value);
  return Number.isInteger(id) && id > 0 ? id : null;
}

function policyLabel(policy: HandoverPolicy): string {
  if (!policy.source_node && !policy.target_node) return '公司默认';
  const source = policy.source_node ? `交出节点 ${policy.source_node}` : '全部交出节点';
  const target = policy.target_node ? `接收节点 ${policy.target_node}` : '全部接收节点';
  return `${source} → ${target}`;
}

function emptyPolicy(): HandoverPolicy {
  return {
    id: 0, kind: 'cutting', mode: 'off', source_node: null, target_node: null,
    target_group_no: null, receiver: null, timeout_minutes: 120, updated_by_name: ''
  };
}

Page({
  data: {
    denied: false,
    loading: true,
    error: false,
    saving: false,
    id: 0,
    policies: [] as HandoverPolicy[],
    policyLabels: [] as string[],
    policyIndex: 0,
    modeIndex: 0,
    modes: ['off', 'warn', 'strict'],
    modeNames: ['关闭', '提醒（不阻断）', '严格（必须经过交接）'],
    sourceNode: '',
    targetNode: '',
    targetGroupNo: '',
    receiver: '',
    timeoutMinutes: '120',
    updatedByName: ''
  },

  onShow() {
    if (!requireLogin()) return;
    this.setData({ denied: !hasPerm('handover.manage') });
    if (!this.data.denied) this.load();
  },

  onPullDownRefresh() {
    this.load().then(() => wx.stopPullDownRefresh());
  },

  applyPolicy(policy: HandoverPolicy, policyIndex: number) {
    this.setData({
      id: policy.id,
      policyIndex,
      modeIndex: Math.max(0, this.data.modes.indexOf(policy.mode)),
      sourceNode: policy.source_node !== null ? String(policy.source_node) : '',
      targetNode: policy.target_node !== null ? String(policy.target_node) : '',
      targetGroupNo: policy.target_group_no !== null ? String(policy.target_group_no) : '',
      receiver: policy.receiver !== null ? String(policy.receiver) : '',
      timeoutMinutes: String(policy.timeout_minutes),
      updatedByName: policy.updated_by_name
    });
  },

  load(): Promise<void> {
    this.setData({ loading: true, error: false });
    const selectedId = this.data.id;
    return fetchHandoverPolicies().then((res) => {
      const policies = (Array.isArray(res) ? res : (res.results || []))
        .filter((item) => item.kind === 'cutting');
      if (!policies.length) policies.push(emptyPolicy());
      const selectedIndex = policies.findIndex((item) => item.id === selectedId);
      const defaultIndex = policies.findIndex((item) => !item.source_node && !item.target_node);
      const index = selectedIndex >= 0 ? selectedIndex : Math.max(0, defaultIndex);
      this.setData({
        loading: false,
        policies,
        policyLabels: policies.map(policyLabel)
      });
      this.applyPolicy(policies[index], index);
    }).catch(() => this.setData({ loading: false, error: true }));
  },

  onRetry() {
    this.load();
  },

  onPolicyChange(e: WechatMiniprogram.PickerChange) {
    const index = Number(e.detail.value);
    const policy = this.data.policies[index];
    if (policy) this.applyPolicy(policy, index);
  },

  onNewOverride() {
    const policy = emptyPolicy();
    const policies = this.data.policies.concat(policy);
    this.setData({
      policies,
      policyLabels: policies.map((item, index) => index === policies.length - 1
        ? '新节点规则（尚未保存）' : policyLabel(item))
    });
    this.applyPolicy(policy, policies.length - 1);
  },

  onModeChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ modeIndex: Number(e.detail.value) });
  },

  onFieldInput(e: WechatMiniprogram.Input) {
    const field = String(e.currentTarget.dataset.field || '');
    const allowed = ['sourceNode', 'targetNode', 'targetGroupNo', 'receiver', 'timeoutMinutes'];
    if (allowed.indexOf(field) >= 0) this.setData({ [field]: e.detail.value });
  },

  onSave() {
    if (this.data.saving) return;
    const sourceNode = optionalId(this.data.sourceNode);
    const targetNode = optionalId(this.data.targetNode);
    const targetGroupNo = optionalId(this.data.targetGroupNo);
    const receiver = optionalId(this.data.receiver);
    const timeout = Number(this.data.timeoutMinutes);
    if ((this.data.sourceNode.trim() && sourceNode === null) ||
      (this.data.targetNode.trim() && targetNode === null) ||
      (this.data.targetGroupNo.trim() && targetGroupNo === null) ||
      (this.data.receiver.trim() && receiver === null) ||
      !Number.isInteger(timeout) || timeout <= 0) {
      wx.showToast({ title: 'ID须为正整数，超时也须为正整数', icon: 'none' });
      return;
    }
    const payload: HandoverPolicyPayload = {
      kind: 'cutting',
      mode: this.data.modes[this.data.modeIndex] as HandoverPolicy['mode'],
      source_node: sourceNode,
      target_node: targetNode,
      target_group_no: targetGroupNo,
      receiver,
      timeout_minutes: timeout
    };
    this.setData({ saving: true });
    const request = this.data.id
      ? updateHandoverPolicy(this.data.id, payload)
      : createHandoverPolicy(payload);
    request.then((saved) => {
      wx.showToast({ title: '策略已保存', icon: 'success' });
      this.setData({ saving: false, id: saved.id }, () => this.load());
    }).catch(() => this.setData({ saving: false }));
  }
});
