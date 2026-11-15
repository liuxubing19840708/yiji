/**
 * 批量代登（竞品对齐 GAP-2，2026-11-04）：选员工 → 选床次 → 选工序 → 逐扎填件数 → 一次提交。
 *
 * - 员工不选 = 登记本人；选了 = 代登（后端 scan.assist 门禁）；
 * - 逐扎校验走后端 _create_record 同口径（工序集/流转/封顶），单扎失败不影响其余（部分成功）；
 * - 提交结果逐行展示：成功（扎号+金额）/失败（原因），失败行可改数重提。
 * 入口：首页宫格「批量代登」，scan.assist 点门控。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import {
  fetchProductions, fetchProductionBundles, batchRegister
} from '../../../services/production';
import { fetchProcesses } from '../../../services/catalog';
import { fetchEmployees } from '../../../services/auth';
import { Paginated, Process, Employee } from '../../../services/types';

/** 床次选项 VM */
interface BedOpt {
  id: number;
  bedNo: string;
  label: string;
}

/** 菲票行 VM */
interface TicketRow {
  ticketId: number;
  bundleNo: string;
  color: string;
  size: string;
  qty: number;
  registeredQty: number;
  /** 填写的件数（空串=未填，提交时跳过） */
  input: string;
  /** 提交结果态：''=未提交 / ok / fail */
  result: string;
  resultText: string;
}

/** 结果行 VM */
interface ResultRow {
  bundleNo: string;
  ok: boolean;
  text: string;
}

Page({
  data: {
    // ---- 选择器 ----
    employees: [] as Employee[],
    employeeNames: ['本人（不代登）'],
    employeeIndex: 0,
    beds: [] as BedOpt[],
    bedLabels: ['选择床次'],
    bedIndex: 0,
    processes: [] as Process[],
    processNames: ['选择工序'],
    processIndex: 0,
    // ---- 菲票列表 ----
    tickets: [] as TicketRow[],
    bedLoaded: false,
    ticketsLoading: false,
    // ---- 提交 ----
    submitting: false,
    resultRows: [] as ResultRow[],
    resultOk: 0,
    resultFail: 0,
    resultShow: false
  },

  onLoad() {
    this.loadOptions();
  },

  loadOptions() {
    Promise.allSettled([
      fetchEmployees(),
      fetchProcesses() as unknown as Promise<Process[] | { results?: Process[] }>,
      fetchProductions({ is_cut_done: false, page_size: 100 } as Parameters<typeof fetchProductions>[0])
    ]).then(([empRes, procRes, bedRes]) => {
      const employees = empRes.status === 'fulfilled' ? (empRes.value || []) : [];
      const procList = procRes.status === 'fulfilled'
        ? (Array.isArray(procRes.value) ? procRes.value : (procRes.value.results || []))
        : [];
      const beds: BedOpt[] = bedRes.status === 'fulfilled'
        ? (((bedRes.value as unknown as Paginated<Record<string, unknown>>).results || []) as Array<Record<string, unknown>>)
          .map((p) => ({
            id: Number(p.id),
            bedNo: String(p.bed_no || ''),
            label: `${p.bed_no || ''} · ${p.style_no || ''} ${p.style_name || ''}`
          }))
        : [];
      this.setData({
        employees,
        employeeNames: ['本人（不代登）'].concat(employees.map((e) => e.name)),
        processes: procList,
        processNames: ['选择工序'].concat(procList.map((p) => p.name)),
        beds,
        bedLabels: ['选择床次'].concat(beds.map((b) => b.label))
      });
    });
  },

  onEmployeeChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ employeeIndex: Number(e.detail.value), resultShow: false });
  },

  onBedChange(e: WechatMiniprogram.PickerChange) {
    const idx = Number(e.detail.value);
    this.setData({ bedIndex: idx, resultShow: false });
    if (idx > 0) this.loadTickets(this.data.beds[idx - 1].id);
  },

  onProcessChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ processIndex: Number(e.detail.value), resultShow: false });
  },

  /** 拉床次菲票列表（/productions/{id}/bundles） */
  loadTickets(bedId: number) {
    this.setData({ ticketsLoading: true, bedLoaded: false, tickets: [] });
    fetchProductionBundles(bedId, { page_size: 200 })
      .then((res) => {
        const rows: TicketRow[] = (res.results || []).map((r: Record<string, unknown>) => ({
          ticketId: Number(r.id),
          bundleNo: String(r.bundle_no || ''),
          color: String(r.color || ''),
          size: String(r.size || ''),
          qty: Number(r.qty || 0),
          registeredQty: Number(r.registered_qty || 0),
          input: '',
          result: '',
          resultText: ''
        }));
        this.setData({ tickets: rows, bedLoaded: true, ticketsLoading: false });
      })
      .catch(() => this.setData({ ticketsLoading: false, bedLoaded: false }));
  },

  onQtyInput(e: WechatMiniprogram.Input) {
    const idx = Number(e.currentTarget.dataset.index);
    this.setData({ [`tickets[${idx}].input`]: e.detail.value, [`tickets[${idx}].result`]: '' });
  },

  /** 一键填满：所有未填行默认「剩余可登数」（封顶前校验仍由后端把关） */
  onFillRemaining() {
    const tickets = this.data.tickets.map((t) => ({
      ...t,
      input: t.input.trim() ? t.input : String(Math.max(t.qty - t.registeredQty, 0))
    }));
    this.setData({ tickets });
  },

  onClearInput() {
    const tickets = this.data.tickets.map((t) => ({
      ...t, input: '', result: '', resultText: ''
    }));
    this.setData({ tickets, resultShow: false });
  },

  async onSubmit() {
    if (this.data.submitting) return;
    const { bedIndex, processIndex, processes, employeeIndex, employees, tickets } = this.data;
    if (bedIndex <= 0) { wx.showToast({ title: '请选择床次', icon: 'none' }); return; }
    if (processIndex <= 0) { wx.showToast({ title: '请选择工序', icon: 'none' }); return; }
    const entries = tickets
      .filter((t) => String(t.input).trim() !== '' && Number(t.input) > 0)
      .map((t) => ({ ticket_id: t.ticketId, qty: Number(t.input) }));
    if (!entries.length) { wx.showToast({ title: '请至少填一行件数', icon: 'none' }); return; }

    const payload: Parameters<typeof batchRegister>[0] = {
      process_id: processes[processIndex - 1].id, entries
    };
    // 代登：employeeIndex-1 对应 employees 下标（0=本人不代登）
    if (employeeIndex > 0) {
      const emp = employees[employeeIndex - 1];
      if (emp && emp.user_id) payload.user_id = emp.user_id;
    }

    const confirm = await wx.showModal({
      title: '批量登记确认',
      content: `共 ${entries.length} 扎，确定提交登记？`,
      confirmText: '提交'
    });
    if (!confirm.confirm) return;

    this.setData({ submitting: true });
    try {
      const res = await batchRegister(payload);
      // 回填每行结果
      const byTicket: Record<number, { ok: boolean; error?: string; amount?: string; bundle_no?: string }> = {};
      (res.results || []).forEach((r) => {
        if (r.ticket_id !== null) byTicket[r.ticket_id] = r;
      });
      const resultRows: ResultRow[] = (res.results || []).map((r) => ({
        bundleNo: r.bundle_no || String(r.ticket_id || '?'),
        ok: !!r.ok,
        text: r.ok
          ? `已登记 ${r.qty ?? ''} 件${r.amount ? ` · ${r.amount} 元` : ''}`
          : (r.error || '登记失败')
      }));
      const tickets = this.data.tickets.map((t) => {
        const r = byTicket[t.ticketId];
        if (!r) return t;
        return { ...t, result: r.ok ? 'ok' : 'fail', resultText: r.ok ? '已登记' : (r.error || '失败') };
      });
      this.setData({
        tickets,
        resultRows,
        resultOk: res.ok || 0,
        resultFail: res.fail || 0,
        resultShow: true,
        submitting: false
      });
      wx.showToast({ title: `成功 ${res.ok} 扎${res.fail ? `，失败 ${res.fail} 扎` : ''}`, icon: 'none' });
    } catch (e) {
      // 网络/参数错误由请求层 toast
      this.setData({ submitting: false });
    }
  }
});
