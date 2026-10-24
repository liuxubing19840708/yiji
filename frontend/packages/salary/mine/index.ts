/**
 * 我的薪酬（规格 §10.2 / §10.6）：
 * 两种入参 —— 员工本人 / 管理员查看他人（onLoad options.user_id）。
 * 头部已结/未结卡 + 三 Tab（工资 / 计数 / 固定工资）
 * + 底部「申请结算」（本人）与「结算记录」。金额分位对齐展示。
 */
import { getUser, requireLogin } from '../../../utils/auth';
import { money, monthRange } from '../../../utils/format';
import { logger } from '../../../utils/logger';
import { mediaUrl } from '../../../utils/media';
import {
  fetchEmployeeSalary, fetchEmployeeSalaryCount, fetchSettleApplies,
  createSettleApply, EmployeeSalaryFullExt, EmployeeSalaryCountExt
} from '../../../services/salary';
import { SettleApply, ApplyStatus } from '../../../services/types';

type MainTab = 'pay' | 'count' | 'fixed';
// 子 Tab（2026-10-22 参考竞品改版）：按日期查看 / 按裁床查看 / 汇总查看
type SubTab = 'date' | 'bed' | 'style';

interface DateRowVM { key: string; qty: number; tickets: number; amountText: string; }

interface BedRowVM {
  key: string; bedNo: string; styleNo: string; styleName: string;
  qty: number; bedTotal: number; amountText: string; settledAmount: string; settledText: string;
}

interface StyleRowVM {
  key: string; styleNo: string; styleName: string; imageUrl: string;
  qty: number; avgPrice: string; amountText: string;
}

interface ApplyRowVM {
  id: number;
  periodText: string;
  amountText: string;
  statusDisplay: string;
  statusClass: string;
  /** 待谁审批（后端 reviewer_hint，待审核/待发放时展示） */
  hint: string;
  note: string;
  createdAt: string;
}

/** 结算申请进度卡：申请后「流程到哪了」一眼可见（§10.5 用户体验补强） */
interface ProgressVM {
  periodText: string;
  amountText: string;
  statusDisplay: string;
  statusClass: string;
  /** 步骤到达数：1=已提交 2=审核中 3=待发放/已发放 */
  step: number;
  /** 终态（已发放/已拒绝/已作废）不拦新申请 */
  active: boolean;
  reason: string;
  /** 待谁审批（后端下发）：待 张三 审批 / 已审核，待 X 发放 */
  hint: string;
}

const statusClassMap: Record<ApplyStatus, string> = {
  0: 'badge',
  1: 'badge-green',
  2: 'badge-green',
  3: 'badge-red',
  4: 'badge-gray'
};

Page({
  data: {
    uid: 0,
    isSelf: true,
    otherName: '',
    start: '',
    end: '',
    quick: '',
    loading: true,
    error: '',
    mainTab: 'pay' as MainTab,
    subTab: 'date' as SubTab,
    // 头部：已结/未结（全历史）+ 计件/固定/总（期间）+ 工序数
    settledText: '0.00',
    unsettledText: '0.00',
    pieceText: '0.00',
    fixedText: '0.00',
    totalWageText: '0.00',
    processCount: 0,
    noPriceCount: 0,
    // 三视图行
    dateRows: [] as DateRowVM[],
    bedRows: [] as BedRowVM[],
    styleRows: [] as StyleRowVM[],
    summaryQty: 0,
    summaryTickets: 0,
    summaryAmountText: '0.00',
    // 固定工资
    fixed: null as EmployeeSalaryFullExt['fixed'] | null,
    // 申请结算面板（2026-10-23：未结工资为 0 不可申请，前后端同规则）
    canApply: true,
    salaryTypeLabel: '',                // 工资计算方式中文名（头部标签展示）
    showApply: false,
    applyNote: '',
    applyAmountHint: '0.00',
    submitting: false,
    // 结算记录
    showRecords: false,
    records: [] as ApplyRowVM[],
    recordPage: 1,
    recordHasMore: false,
    recordLoading: false,
    // 申请进度卡（isSelf 常驻展示；null=无申请记录不显示）
    progress: null as ProgressVM | null
  },

  onLoad(options: Record<string, string | undefined>) {
    const uid = options.user_id ? Number(options.user_id) : (getUser() || { id: 0 }).id;
    const otherName = options.name ? decodeURIComponent(options.name) : '';
    const isSelf = uid === (getUser() || { id: -1 }).id;
    const r = monthRange();
    this.setData({
      uid,
      isSelf,
      otherName,
      start: r.start,
      end: r.end,
      quick: 'month'
    });
    if (!isSelf) {
      wx.setNavigationBarTitle({ title: otherName || '员工薪酬详情' });
    }
  },

  onShow() {
    if (!requireLogin()) return;
    this.reload();
  },

  onPullDownRefresh() {
    this.reload().then(() => wx.stopPullDownRefresh());
  },

  reload(): Promise<void> {
    this.setData({ loading: true, error: '' });
    const params = { date_from: this.data.start, date_to: this.data.end };
    let task: Promise<void>;
    if (this.data.mainTab === 'count') {
      task = this.loadCount(params);
    } else {
      task = this.loadPay(params);
    }
    return task
      .then(() => {
        this.setData({ loading: false });
        // 本人视角常驻刷新申请进度（首次进入不用先点「结算记录」才知道流程到哪了）
        if (this.data.isSelf) this.loadRecords(1);
      })
      .catch(() => this.setData({ loading: false, error: '加载失败，请重试' }));
  },

  applyHead(d: EmployeeSalaryFullExt | (EmployeeSalaryCountExt & { settled?: string; unsettled?: string })) {
    const full = d as EmployeeSalaryFullExt;
    if (typeof full.settled === 'string' && typeof full.unsettled === 'string') {
      this.setData({
        settledText: money(full.settled),
        unsettledText: money(full.unsettled),
        applyAmountHint: money(full.unsettled),
        canApply: Number(full.unsettled) > 0,
        salaryTypeLabel: full.salary_type_display || ''
      });
    }
  },

  /** 头部计件/固定/总 + 工序数（count Tab 无金额，金额位填 0） */
  applySummary(d: EmployeeSalaryFullExt | EmployeeSalaryCountExt, isCount: boolean) {
    const raw = d as unknown as {
      summary: { amount?: string; processes?: number; no_price_count?: number };
      fixed?: { amount?: string };
    };
    const piece = isCount ? 0 : Number(raw.summary.amount || 0);
    const fixed = Number((raw.fixed && raw.fixed.amount) || 0);
    this.setData({
      pieceText: isCount ? '0.00' : money(raw.summary.amount || '0'),
      fixedText: money(fixed),
      totalWageText: money(piece + fixed),
      processCount: raw.summary.processes || 0,
      noPriceCount: raw.summary.no_price_count || 0
    });
  },

  /** 三视图行一次建齐（按日期/按裁床/汇总查看共用一次接口数据，切子 Tab 不重拉） */
  buildRows(d: EmployeeSalaryFullExt | EmployeeSalaryCountExt, isCount: boolean) {
    const dateRows: DateRowVM[] = d.by_date.map((x) => ({
      key: x.date, qty: x.qty, tickets: x.tickets || 0,
      amountText: isCount ? '' : money((x as { amount?: string }).amount || '0')
    }));
    const bedRows: BedRowVM[] = d.by_bed.map((x) => {
      const settledAmount = isCount ? '0' : String((x as { settled?: string }).settled || '0');
      return {
        key: `b${x.production_id}`,
        bedNo: x.bed_no,
        styleNo: x.style_no,
        styleName: x.style_name,
        qty: x.qty,
        bedTotal: (x as { bed_total?: number }).bed_total || 0,
        amountText: isCount ? '' : money((x as { amount?: string }).amount || '0'),
        settledAmount,
        settledText: Number(settledAmount) > 0 ? '已结算' : '未结算'
      };
    });
    const styleRows: StyleRowVM[] = (d.by_style || []).map((x) => ({
      key: `s${x.style_id}`,
      styleNo: x.style_no,
      styleName: x.style_name,
      imageUrl: mediaUrl(x.image || ''),
      qty: x.qty,
      avgPrice: x.avg_price || '0.00',
      amountText: isCount ? '' : money(x.amount || '0')
    }));
    this.setData({
      dateRows,
      bedRows,
      styleRows,
      summaryQty: d.summary.qty,
      summaryTickets: d.summary.ticket_count,
      summaryAmountText: isCount ? '' : money((d.summary as { amount?: string }).amount || '0')
    });
  },

  loadPay(params: { date_from?: string; date_to?: string }): Promise<void> {
    return fetchEmployeeSalary(this.data.uid, params).then((d) => {
      this.applyHead(d);
      this.setData({ fixed: d.fixed || null });
      this.applySummary(d, false);
      this.buildRows(d, false);
    });
  },

  loadCount(params: { date_from?: string; date_to?: string }): Promise<void> {
    return fetchEmployeeSalaryCount(this.data.uid, params).then((d) => {
      this.applyHead(d as EmployeeSalaryCountExt & { settled?: string; unsettled?: string });
      this.applySummary(d, true);
      this.buildRows(d, true);
    });
  },

  onRangeChange(e: WechatMiniprogram.CustomEvent<{ start: string; end: string; quick: string }>) {
    this.setData({ start: e.detail.start, end: e.detail.end, quick: e.detail.quick });
    this.reload();
  },

  onMainTab(e: WechatMiniprogram.TouchEvent) {
    const tab = e.currentTarget.dataset.tab as MainTab;
    if (tab === this.data.mainTab) return;
    this.setData({ mainTab: tab, rows: [] });
    this.reload();
  },

  onSubTab(e: WechatMiniprogram.TouchEvent) {
    const tab = e.currentTarget.dataset.tab as SubTab;
    if (tab === this.data.subTab) return;
    // 三视图共用同一次接口数据，切换不重拉
    this.setData({ subTab: tab });
  },

  /** 汇总查看款式图预览 */
  onPreviewStyle(e: WechatMiniprogram.TouchEvent) {
    const url = String(e.currentTarget.dataset.url || '');
    if (!url) return;
    wx.previewImage({ current: url, urls: [url] });
  },

  // ---- 申请结算（本人，§10.5） ----
  onToggleApply() {
    if (!this.data.canApply) {
      // 未结工资为 0（后端同规则兜底拦截）
      wx.showToast({ title: '未结工资为 0，无需申请结算', icon: 'none', duration: 2400 });
      return;
    }
    const p = this.data.progress;
    if (p && p.active) {
      // 防重复申请（后端同规则兜底拦截）：待审核/待发放期间不再收新单
      wx.showToast({ title: `已有申请${p.statusDisplay}，处理完成后再申请`, icon: 'none', duration: 2400 });
      return;
    }
    this.setData({ showApply: !this.data.showApply, showRecords: false });
  },

  onNoteInput(e: WechatMiniprogram.Input) {
    this.setData({ applyNote: e.detail.value });
  },

  onSubmitApply() {
    if (this.data.submitting) return;
    const p = this.data.progress;
    if (p && p.active) {
      wx.showToast({ title: `已有申请${p.statusDisplay}，处理完成后再申请`, icon: 'none', duration: 2400 });
      return;
    }
    const r = monthRange();
    this.setData({ submitting: true });
    createSettleApply(r.start, r.end, this.data.applyNote)
      .then(() => {
        logger.info('发起结算申请', `${r.start}~${r.end}`);
        wx.showToast({ title: '已提交申请', icon: 'success' });
        this.setData({ showApply: false, applyNote: '', submitting: false });
        this.loadRecords(1);
      })
      .catch(() => this.setData({ submitting: false }));
  },

  // ---- 结算记录（本人 / 管理员查他人） ----
  onToggleRecords() {
    const show = !this.data.showRecords;
    this.setData({ showRecords: show, showApply: false });
    if (show && !this.data.records.length) this.loadRecords(1);
  },

  loadRecords(page: number): Promise<void> {
    this.setData({ recordLoading: true });
    return fetchSettleApplies({ user_id: this.data.uid, page, page_size: 20 })
      .then((res) => {
        const list = res.results.map((a: SettleApply): ApplyRowVM => ({
          id: a.id,
          periodText: `${a.period_start} ~ ${a.period_end}`,
          amountText: money(a.amount),
          statusDisplay: a.status_display,
          statusClass: statusClassMap[a.status] || 'badge',
          hint: (a as unknown as { reviewer_hint?: string }).reviewer_hint || '',
          note: a.note,
          createdAt: (a.created_at || '').slice(0, 10)
        }));
        const merged = page === 1 ? list : this.data.records.concat(list);
        this.setData({
          records: merged,
          recordPage: page,
          recordHasMore: !!res.next,
          recordLoading: false
        });
        this.computeProgress(res.results);
      })
      .catch(() => this.setData({ recordLoading: false }));
  },

  /** 申请进度推导：处理中的申请优先展示（待审核/待发放）；否则展示最近一笔的终态 */
  computeProgress(applies: SettleApply[]) {
    if (!this.data.isSelf || !applies.length) {
      this.setData({ progress: null });
      return;
    }
    const a = applies.find((x) => x.status === 0 || x.status === 1) || applies[0];
    const stepMap: Record<ApplyStatus, number> = { 0: 2, 1: 3, 2: 3, 3: 2, 4: 2 };
    this.setData({
      progress: {
        periodText: `${a.period_start} ~ ${a.period_end}`,
        amountText: money(a.amount),
        statusDisplay: a.status_display,
        statusClass: statusClassMap[a.status] || 'badge',
        step: stepMap[a.status] || 1,
        active: a.status === 0 || a.status === 1,
        reason: a.status === 3 ? (a.reject_reason || '未填写') : (a.status === 4 ? (a.void_reason || '未填写') : ''),
        hint: a.reviewer_hint || ''
      }
    });
  },

  onRecordMore() {
    if (this.data.recordLoading || !this.data.recordHasMore) return;
    this.loadRecords(this.data.recordPage + 1);
  }
});
