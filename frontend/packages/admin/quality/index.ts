/**
 * 质检管理页（竞品对齐 GAP-1，2026-11-04）。
 *
 * - 列表按状态 tab（全部/返修中/已收回/已完成），行内动作：收货/完成/删除；
 * - 「登记返修」：床次号+扎号查菲票（手输码兜底同款 lookup）→ 填返修数量/原因/工序；
 * - 导出 xlsx（quality.view 点即可），动作需 quality.manage 点。
 * 入口：首页宫格「质检管理」。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import {
  QualityRecordRow, QualityStatus,
  fetchQualityRecords, createQualityRecord,
  qualityRecordAction, deleteQualityRecord, downloadQualityExport, qualityExportUrl
} from '../../../services/quality';
import { lookupTickets } from '../../../services/production';
import { fetchProcesses } from '../../../services/catalog';
import { fetchEmployees } from '../../../services/auth';
import { Process, Employee } from '../../../services/types';

/** 状态 tab */
const STATUS_TABS: Array<{ key: string; label: string }> = [
  { key: '', label: '全部' },
  { key: 'rework', label: '返修中' },
  { key: 'received', label: '已收回' },
  { key: 'done', label: '已完成' }
];

/** 菲票候选 VM（lookup 结果） */
interface TicketOpt {
  id: number;
  label: string;
  qty: number;
}

Page({
  data: {
    tabs: STATUS_TABS,
    statusIndex: 0,
    canManage: false,
    canExport: false,
    exporting: false,
    loading: false,
    list: [] as QualityRecordRow[],
    // ---- 登记返修表单 ----
    formOpen: false,
    formSaving: false,
    bedNo: '',
    bundleNo: '',
    ticketOpts: [] as TicketOpt[],
    ticketLabels: ['选择菲票'],
    ticketIndex: 0,
    defectQty: '',
    reason: '',
    processes: [] as Process[],
    processNames: ['返修工序（可选）'],
    processIndex: 0,
    employees: [] as Employee[],
    producerNames: ['返修工人（可选）'],
    producerIndex: 0
  },

  onShow() {
    requireLogin();
    const app = getApp<IAppOption>();
    this.setData({
      canManage: !!app.globalData.isAdmin || hasPerm('quality.manage'),
      canExport: !!app.globalData.isAdmin || hasPerm('quality.view')
    });
    this.reload();
  },

  onStatusTap(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    this.setData({ statusIndex: idx });
    this.reload();
  },

  reload() {
    this.setData({ loading: true });
    const status = (STATUS_TABS[this.data.statusIndex] || {}).key || undefined;
    fetchQualityRecords({ status: status as QualityStatus | undefined, page_size: 100 })
      .then((res) => this.setData({ list: res.results || [], loading: false }))
      .catch(() => this.setData({ loading: false }));
  },

  // ================= 登记返修 =================
  onOpenForm() {
    if (!this.data.processes.length) {
      fetchProcesses().then((ps) => this.setData({
        processes: ps,
        processNames: ['返修工序（可选）'].concat(ps.map((p) => p.name))
      })).catch(() => undefined);
      fetchEmployees().then((es) => this.setData({
        employees: es,
        producerNames: ['返修工人（可选）'].concat(es.map((x) => x.name))
      })).catch(() => undefined);
    }
    this.setData({ formOpen: true });
  },

  onCloseForm() {
    this.setData({
      formOpen: false, bedNo: '', bundleNo: '', ticketOpts: [], ticketLabels: ['选择菲票'],
      ticketIndex: 0, defectQty: '', reason: '', processIndex: 0, producerIndex: 0
    });
  },

  onBedInput(e: WechatMiniprogram.Input) { this.setData({ bedNo: e.detail.value }); },
  onBundleInput(e: WechatMiniprogram.Input) { this.setData({ bundleNo: e.detail.value }); },

  onLookupTicket() {
    const { bedNo, bundleNo } = this.data;
    if (!bedNo.trim() && !bundleNo.trim()) {
      wx.showToast({ title: '床次号/扎号至少填一项', icon: 'none' }); return;
    }
    lookupTickets(bedNo.trim(), bundleNo.trim())
      .then((rows) => {
        if (!rows.length) {
          wx.showToast({ title: '未找到匹配菲票', icon: 'none' }); return;
        }
        const opts: TicketOpt[] = rows.map((r) => ({
          id: r.id, qty: r.qty || 0,
          label: `${r.bed_no} 第${r.bundle_no}扎 ${r.style_no || ''} ${r.color || ''}/${r.size || ''} x${r.qty}`
        }));
        this.setData({ ticketOpts: opts, ticketLabels: ['选择菲票'].concat(opts.map((o) => o.label)), ticketIndex: 0 });
      })
      .catch(() => wx.showToast({ title: '查询失败', icon: 'none' }));
  },

  onTicketChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ ticketIndex: Number(e.detail.value) });
  },
  onProcessChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ processIndex: Number(e.detail.value) });
  },
  onProducerChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ producerIndex: Number(e.detail.value) });
  },
  onQtyInput(e: WechatMiniprogram.Input) { this.setData({ defectQty: e.detail.value }); },
  onReasonInput(e: WechatMiniprogram.Input) { this.setData({ reason: e.detail.value }); },

  async onSubmitForm() {
    if (this.data.formSaving) return;
    const { ticketOpts, ticketIndex, defectQty, reason, processes, processIndex, employees, producerIndex } = this.data;
    const opt = ticketOpts[ticketIndex - 1];
    if (!opt) { wx.showToast({ title: '请先查询并选择菲票', icon: 'none' }); return; }
    const qty = Number(defectQty);
    if (!Number.isInteger(qty) || qty <= 0) { wx.showToast({ title: '返修数量须为正整数', icon: 'none' }); return; }
    if (qty > opt.qty) { wx.showToast({ title: `不能超过扎数 ${opt.qty}`, icon: 'none' }); return; }
    if (!reason.trim()) { wx.showToast({ title: '请填写返修原因', icon: 'none' }); return; }

    const payload: Parameters<typeof createQualityRecord>[0] = {
      ticket: opt.id, defect_qty: qty, reason: reason.trim()
    };
    if (processIndex > 0) payload.process = processes[processIndex - 1].id;
    if (producerIndex > 0) {
      const emp = employees[producerIndex - 1];
      if (emp && emp.user_id) payload.producer = emp.user_id;
    }
    this.setData({ formSaving: true });
    try {
      await createQualityRecord(payload);
      wx.showToast({ title: '已登记返修', icon: 'success' });
      this.onCloseForm();
      this.reload();
    } catch (e) {
      // 错误由请求层 toast
    } finally {
      this.setData({ formSaving: false });
    }
  },

  // ================= 行内动作 =================
  async onReceive(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const row = this.data.list.find((r) => r.id === id);
    if (!row) return;
    const res = await wx.showModal({
      title: '返修收货',
      editable: true,
      placeholderText: `收回数量（≤${row.defect_qty}）`,
      content: String(row.defect_qty)
    });
    if (!res.confirm) return;
    const qty = Number(res.content);
    if (!Number.isInteger(qty) || qty <= 0 || qty > row.defect_qty) {
      wx.showToast({ title: `收回数量须为 1~${row.defect_qty}`, icon: 'none' }); return;
    }
    try {
      await qualityRecordAction(id, { action: 'receive', received_qty: qty });
      wx.showToast({ title: '已收货', icon: 'success' });
      this.reload();
    } catch (err) { /* 请求层已提示 */ }
  },

  async onFinish(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const res = await wx.showModal({ title: '返修完成', content: '确认该返修已完成？' });
    if (!res.confirm) return;
    try {
      await qualityRecordAction(id, { action: 'finish' });
      wx.showToast({ title: '已完成', icon: 'success' });
      this.reload();
    } catch (err) { /* 请求层已提示 */ }
  },

  async onDelete(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const res = await wx.showModal({ title: '删除质检记录', content: '删除后不可恢复，确认？' });
    if (!res.confirm) return;
    try {
      await deleteQualityRecord(id);
      wx.showToast({ title: '已删除', icon: 'success' });
      this.reload();
    } catch (err) { /* 请求层已提示 */ }
  },

  // ================= 导出 =================
  async onExport() {
    if (this.data.exporting) return;
    this.setData({ exporting: true });
    try {
      const status = (STATUS_TABS[this.data.statusIndex] || {}).key || undefined;
      await downloadQualityExport(qualityExportUrl({ status: status as QualityStatus | undefined }));
    } catch (e) {
      wx.showToast({ title: '导出失败', icon: 'none' });
    } finally {
      this.setData({ exporting: false });
    }
  }
});
