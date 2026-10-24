/**
 * 登记记录列表（管理员视角）：
 * 按员工/日期区间/工序筛选 + 触底分页；撤销规则与「工作中」一致
 * （本人当日、管理员当月内、未结算；已结算/超期由后端最终校验）。
 */
import { requireLogin, getUser } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { fetchRecords, deleteRecord } from '../../../services/production';
import { fetchProcesses } from '../../../services/catalog';
import { fetchEmployees } from '../../../services/auth';
import { RecordItem, Process, Employee } from '../../../services/types';
import { money, fmtDateTime, today, monthRange } from '../../../utils/format';

type RecordRow = RecordItem & {
  bed_no?: string;
  bundle_no?: string;
  color?: string;
  size?: string;
  style_no?: string;
  style_name?: string;
  process_name?: string;
  user_name?: string;
  operator_name?: string;
  amount?: string;
  /** 视图字段 */
  amountText?: string;
  timeText?: string;
  canRevoke?: boolean;
};

const PAGE_SIZE = 20;

Page({
  data: {
    loading: true,
    loadingMore: false,
    loadError: false,
    list: [] as RecordRow[],
    page: 1,
    hasMore: false,
    count: 0,
    /** 筛选 */
    employees: [] as Employee[],
    employeeNames: ['全部员工'],
    employeeIndex: 0,
    processes: [] as Process[],
    processNames: ['全部工序'],
    processIndex: 0,
    startDate: today(),
    endDate: today(),
    quick: 'today',
    isAdmin: false,
    /** 菲票预过滤（§2.9 P0 下钻第三级）：床次详情点菲票 → ?ticketId= 带参进入 */
    ticketId: 0,
    /** 撤销请求进行中的记录 id */
    revokingId: 0
  },

  /**
   * 带参进入：?ticketId=<菲票id> → 只看该扎登记明细（按菲票过滤时忽略日期区间，
   * 因为登记可能发生在任意日期）。
   */
  onLoad(options: Record<string, string>) {
    const ticketId = Number(options.ticketId) || 0;
    if (ticketId) {
      this.setData({ ticketId });
    }
  },

  /** 解除菲票过滤：恢复默认（今日区间）并重查 */
  onClearTicket() {
    this.setData({ ticketId: 0, startDate: today(), endDate: today(), quick: 'today' });
    this.reload();
  },

  onShow() {
    if (!requireLogin()) return;
    const app = getApp<IAppOption>();
    this.setData({ isAdmin: !!app.globalData.isAdmin });
    this.loadFilters();
    this.reload();
  },

  // ============ 筛选项 ============

  async loadFilters() {
    const [empRes, procRes] = await Promise.allSettled([
      fetchEmployees(),
      fetchProcesses() as unknown as Promise<Process[] | { results?: Process[] }>
    ]);
    const employees = empRes.status === 'fulfilled' ? (empRes.value || []) : [];
    const procList = procRes.status === 'fulfilled'
      ? (Array.isArray(procRes.value) ? procRes.value : (procRes.value.results || []))
      : [];
    procList.sort((a, b) => (a.sort || 0) - (b.sort || 0) || a.id - b.id);
    this.setData({
      employees,
      employeeNames: ['全部员工'].concat(employees.map((e) => e.name)),
      processes: procList,
      processNames: ['全部工序'].concat(procList.map((p) => p.name))
    });
  },

  onEmployeeChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ employeeIndex: Number(e.detail.value) });
    this.reload();
  },

  onProcessChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ processIndex: Number(e.detail.value) });
    this.reload();
  },

  onRangeChange(e: WechatMiniprogram.CustomEvent<{ start: string; end: string; quick: string }>) {
    this.setData({
      startDate: e.detail.start,
      endDate: e.detail.end,
      quick: e.detail.quick || ''
    });
    this.reload();
  },

  onResetRange() {
    const r = monthRange();
    this.setData({ startDate: r.start, endDate: r.end, quick: 'month' });
    this.reload();
  },

  // ============ 列表 ============

  selectedUserId(): number {
    const idx = this.data.employeeIndex;
    return idx > 0 && this.data.employees[idx - 1] ? this.data.employees[idx - 1].user_id : 0;
  },

  selectedProcessId(): number {
    const idx = this.data.processIndex;
    return idx > 0 && this.data.processes[idx - 1] ? this.data.processes[idx - 1].id : 0;
  },

  reload() {
    this.setData({ page: 1, hasMore: false });
    return this.loadPage(true);
  },

  async loadPage(reset: boolean) {
    if (!reset && (this.data.loadingMore || !this.data.hasMore)) return;
    const page = reset ? 1 : this.data.page + 1;
    this.setData(reset ? { loading: true, loadError: false } : { loadingMore: true });
    try {
      // 仅带有效筛选参数（避免 undefined 被 wx.request 序列化成字面量）
      const params: Parameters<typeof fetchRecords>[0] = {
        page,
        page_size: PAGE_SIZE
      };
      if (this.data.ticketId) {
        // 菲票过滤：登记可能发生在任意日期，忽略日期区间只按 ticket_id 查
        params.ticket_id = this.data.ticketId;
      } else {
        params.date_from = this.data.startDate;
        params.date_to = this.data.endDate;
      }
      const uidFilter = this.selectedUserId();
      const pidFilter = this.selectedProcessId();
      if (uidFilter) params.user_id = uidFilter;
      if (pidFilter) params.process_id = pidFilter;
      const res = await fetchRecords(params);
      const uid = (getUser() || { id: -1 }).id;
      const rows = (res.results || []).map((r) => this.decorate(r as RecordRow, uid));
      // 服务层 user/process 参数名与后端不一致时筛选会被忽略，客户端兜底过滤（见合并报告）
      const filtered = rows.filter((r) =>
        (!uidFilter || r.user === uidFilter) && (!pidFilter || r.process === pidFilter)
      );
      this.setData({
        list: reset ? filtered : this.data.list.concat(filtered),
        page,
        hasMore: !!res.next,
        count: res.count || 0,
        loading: false,
        loadingMore: false
      });
    } catch (e) {
      this.setData({ loading: false, loadingMore: false, loadError: reset });
    }
  },

  decorate(r: RecordRow, uid: number): RecordRow {
    return {
      ...r,
      amountText: money(r.amount || r.price),
      timeText: fmtDateTime(r.registered_at),
      canRevoke: this.checkRevoke(r, uid)
    };
  },

  /**
   * 可撤销：未结算 + （本人当日不限 / 管理员当月内撤销他人需 records.revoke 点），
   * 后端为最终校验。权限点体系二期：撤销他人从 isAdmin 收窄为 records.revoke 点。
   */
  checkRevoke(r: RecordRow, uid: number): boolean {
    if (r.settlement) return false;
    const regDate = (r.registered_at || '').slice(0, 10);
    if (r.user === uid) {
      return regDate === today();
    }
    if (!this.data.isAdmin) return false;
    if (!hasPerm('records.revoke')) return false;
    return regDate.slice(0, 7) === today().slice(0, 7);
  },

  onReachBottom() {
    this.loadPage(false);
  },

  onRetry() {
    this.reload();
  },

  // ============ 撤销 ============

  onRevoke(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    if (!id || this.data.revokingId) return;
    wx.showModal({
      title: '撤销登记',
      content: '撤销后该笔登记的工资将不再计入，确认撤销？',
      confirmColor: '#F53F3F',
      success: (res) => {
        if (res.confirm) this.doRevoke(id);
      }
    });
  },

  async doRevoke(id: number) {
    this.setData({ revokingId: id });
    try {
      await deleteRecord(id);
      wx.showToast({ title: '已撤销', icon: 'success' });
      this.reload();
    } catch (e) {
      // 已结算/超期等原因：请求层已 toast
    } finally {
      this.setData({ revokingId: 0 });
    }
  }
});
