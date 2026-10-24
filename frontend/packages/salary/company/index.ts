/**
 * 薪酬管理（管理员，规格 §10.1）：
 * 日期条 + 汇总卡（已结/未结/计件/固定/总 + 完成工序数/无单价工序数）
 * + 三按钮（工资结算 / 结算记录 / 申请记录）+「选择员工」员工卡列表。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { money, monthRange } from '../../../utils/format';
import {
  fetchSalarySummary, fetchCompanyEmployees, fetchSettlements,
  createSettlement, revokeSettlement, fetchSettleApplies,
  reviewSettleApply, paySettleApply, voidSettleApply,
  SalaryCompanySummaryFull, SalaryEmployeeCard
} from '../../../services/salary';
import { Settlement, SettleApply, ApplyStatus } from '../../../services/types';

type Section = '' | 'settle' | 'settlements' | 'applies';

interface SettleRowVM {
  user_id: number;
  real_name: string;
  amountText: string;
}

interface SettlementVM {
  id: number;
  title: string;
  periodText: string;
  amountText: string;
  employeeCount: number;
  status: string;
  statusDisplay: string;
  revoked: boolean;
  /** 权限点体系二期：撤销结算单按钮显隐（salary.settle_revoke） */
  canRevoke: boolean;
  createdByName: string;
  createdAt: string;
}

interface ApplyVM {
  id: number;
  userName: string;
  periodText: string;
  amountText: string;
  note: string;
  status: ApplyStatus;
  statusDisplay: string;
  statusClass: string;
  canReview: boolean; // 待审核 → 通过/拒绝
  canPay: boolean; // 待发放 → 发放
  canVoid: boolean;
  createdAt: string;
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
    loading: true,
    error: '',
    start: '',
    end: '',
    quick: '',
    // 汇总卡
    settledText: '0.00',
    unsettledText: '0.00',
    pieceText: '0.00',
    fixedText: '0.00',
    totalText: '0.00',
    processCount: 0,
    noPriceCount: 0,
    // 三按钮区
    section: '' as Section,
    /** 权限点体系二期：工资结算/审核发放/撤销结算按钮显隐 */
    canSettle: false,
    canReview: false,
    canRevokeSettlement: false,
    // 工资结算确认
    settleRows: [] as SettleRowVM[],
    settling: false,
    // 结算记录
    settlements: [] as SettlementVM[],
    settlePage: 1,
    settleHasMore: false,
    settleLoading: false,
    expandIdx: -1,
    // 申请记录
    applyTab: 0, // 0 审核记录 1 发放记录
    applies: [] as ApplyVM[],
    applyPage: 1,
    applyHasMore: false,
    applyLoading: false,
    // 员工卡列表
    employees: [] as SalaryEmployeeCard[],
    empLoaded: false
  },

  onLoad() {
    const r = monthRange();
    this.setData({ start: r.start, end: r.end, quick: 'month' });
  },

  onShow() {
    if (!requireLogin()) return;
    // 权限点体系二期：高危按钮按点显隐（未初始化权限时 hasPerm 回退全量，越权由后端 403 兜底）
    this.setData({
      canSettle: hasPerm('salary.settle'),
      canReview: hasPerm('salary.review'),
      canRevokeSettlement: hasPerm('salary.settle_revoke')
    });
    this.reload();
  },

  onPullDownRefresh() {
    this.reload().then(() => wx.stopPullDownRefresh());
  },

  reload(): Promise<void> {
    this.setData({ loading: true, error: '' });
    return Promise.all([this.loadSummary(), this.loadEmployees()])
      .then(() => this.setData({ loading: false }))
      .catch(() => this.setData({
        loading: false,
        error: '加载失败（需要管理员权限），请重试'
      }));
  },

  rangeParams(): { date_from?: string; date_to?: string } {
    const { start, end } = this.data;
    const params: { date_from?: string; date_to?: string } = {};
    if (start) params.date_from = start;
    if (end) params.date_to = end;
    return params;
  },

  loadSummary(): Promise<void> {
    return fetchSalarySummary(this.rangeParams()).then((s: SalaryCompanySummaryFull) => {
      this.setData({
        settledText: money(s.settled),
        unsettledText: money(s.unsettled.amount),
        pieceText: money(s.unsettled.amount),
        fixedText: money(s.fixed_total),
        totalText: money(s.total),
        processCount: s.unsettled.processes,
        noPriceCount: s.unsettled.no_price_processes
      });
    });
  },

  loadEmployees(): Promise<void> {
    return fetchCompanyEmployees(this.rangeParams()).then((list) => {
      this.setData({ employees: list, empLoaded: true });
    });
  },

  onRangeChange(e: WechatMiniprogram.CustomEvent<{ start: string; end: string; quick: string }>) {
    const { start, end, quick } = e.detail;
    this.setData({ start, end, quick, section: '', expandIdx: -1 });
    this.reload();
  },

  // ---- 工资结算（§10.3 确认页：逐员工金额网格） ----
  onGoSettle() {
    const rows: SettleRowVM[] = this.data.employees
      .filter((x) => Number(x.period_amount) > 0)
      .map((x) => ({
        user_id: x.user_id,
        real_name: x.real_name,
        amountText: money(x.period_amount)
      }));
    this.setData({ section: 'settle', settleRows: rows });
  },

  onCancelSettle() {
    this.setData({ section: '' });
  },

  onConfirmSettle() {
    if (this.data.settling) return;
    const { start, end } = this.data;
    if (!start || !end) {
      wx.showToast({ title: '请先选择结算期间', icon: 'none' });
      return;
    }
    if (!this.data.settleRows.length) {
      wx.showToast({ title: '该期间没有可结算的登记', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '确认结算',
      content: `将结算 ${start} 至 ${end} 期间全部未结登记，确认执行？`,
      success: (res) => {
        if (!res.confirm) return;
        this.setData({ settling: true });
        createSettlement(start, end)
          .then(() => {
            wx.showToast({ title: '结算成功', icon: 'success' });
            this.setData({ section: '', settling: false });
            this.reload();
          })
          .catch(() => this.setData({ settling: false }));
      }
    });
  },

  // ---- 结算记录（§10.4：卡片 + 展开明细 + 撤销） ----
  onGoSettlements() {
    this.setData({ section: 'settlements', settlements: [], settlePage: 1, settleHasMore: false, expandIdx: -1 });
    this.loadSettlements(1);
  },

  loadSettlements(page: number): Promise<void> {
    this.setData({ settleLoading: true });
    return fetchSettlements({ ...this.rangeParams(), page, page_size: 20 })
      .then((res) => {
        const list = res.results.map((s: Settlement): SettlementVM => ({
          id: s.id,
          title: `${s.employee_count}笔`,
          periodText: s.period_start === s.period_end
            ? s.period_start
            : `${s.period_start} ~ ${s.period_end}`,
          amountText: money(s.amount),
          employeeCount: s.employee_count,
          status: s.status,
          statusDisplay: s.status_display,
          revoked: s.status === 'revoked',
          canRevoke: s.status !== 'revoked' && this.data.canRevokeSettlement,
          createdByName: s.created_by_name,
          createdAt: (s.created_at || '').slice(0, 10)
        }));
        this.setData({
          settlements: page === 1 ? list : this.data.settlements.concat(list),
          settlePage: page,
          settleHasMore: !!res.next,
          settleLoading: false
        });
      })
      .catch(() => this.setData({ settleLoading: false }));
  },

  onSettleMore() {
    if (this.data.settleLoading || !this.data.settleHasMore) return;
    this.loadSettlements(this.data.settlePage + 1);
  },

  onExpandSettlement(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    this.setData({ expandIdx: this.data.expandIdx === idx ? -1 : idx });
  },

  onRevokeSettlement(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    wx.showModal({
      title: '撤销结算',
      content: '撤销后该结算单对应的登记记录将恢复为未结，确认撤销？',
      success: (res) => {
        if (!res.confirm) return;
        revokeSettlement(id)
          .then(() => {
            wx.showToast({ title: '已撤销', icon: 'success' });
            this.loadSettlements(1);
            this.loadSummary();
            this.loadEmployees();
          })
          .catch(() => undefined);
      }
    });
  },

  // ---- 申请记录（§10.5：审核记录 | 发放记录 双 Tab） ----
  onGoApplies() {
    this.setData({ section: 'applies', applies: [], applyPage: 1, applyHasMore: false });
    this.loadApplies(1);
  },

  onApplyTab(e: WechatMiniprogram.TouchEvent) {
    const tab = Number(e.currentTarget.dataset.tab);
    if (tab === this.data.applyTab) return;
    this.setData({ applyTab: tab, applies: [], applyPage: 1, applyHasMore: false });
    this.loadApplies(1);
  },

  loadApplies(page: number): Promise<void> {
    this.setData({ applyLoading: true });
    const params: Record<string, unknown> = { ...this.rangeParams(), page, page_size: 20 };
    if (this.data.applyTab === 1) params.status = 2; // 发放记录 = 已发放
    return fetchSettleApplies(params)
      .then((res) => {
        const list = res.results.map((a: SettleApply): ApplyVM => ({
          id: a.id,
          userName: a.user_name,
          periodText: `${a.period_start} ~ ${a.period_end}`,
          amountText: money(a.amount),
          note: a.note,
          status: a.status,
          statusDisplay: a.status_display,
          statusClass: statusClassMap[a.status] || 'badge',
          // 审核/发放按 salary.review 点显隐（作废暂无独立权限点，维持原状）
          canReview: a.status === 0 && this.data.canReview,
          canPay: a.status === 1 && this.data.canReview,
          canVoid: a.status === 0 || a.status === 1,
          createdAt: (a.created_at || '').slice(0, 10)
        }));
        this.setData({
          applies: page === 1 ? list : this.data.applies.concat(list),
          applyPage: page,
          applyHasMore: !!res.next,
          applyLoading: false
        });
      })
      .catch(() => this.setData({ applyLoading: false }));
  },

  onApplyMore() {
    if (this.data.applyLoading || !this.data.applyHasMore) return;
    this.loadApplies(this.data.applyPage + 1);
  },

  onApproveApply(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    reviewSettleApply(id, 'approve')
      .then(() => {
        wx.showToast({ title: '已通过', icon: 'success' });
        this.loadApplies(1);
      })
      .catch(() => undefined);
  },

  onRejectApply(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    wx.showModal({
      title: '拒绝申请',
      editable: true,
      placeholderText: '请输入拒绝理由（可空）',
      success: (res) => {
        if (!res.confirm) return;
        reviewSettleApply(id, 'reject', res.content || '')
          .then(() => {
            wx.showToast({ title: '已拒绝', icon: 'success' });
            this.loadApplies(1);
          })
          .catch(() => undefined);
      }
    });
  },

  onPayApply(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    wx.showModal({
      title: '确认发放',
      content: '发放将生成结算单并锁定该员工期间未结登记，确认已线下打款？',
      success: (res) => {
        if (!res.confirm) return;
        paySettleApply(id)
          .then(() => {
            wx.showToast({ title: '已发放', icon: 'success' });
            this.loadApplies(1);
            this.loadSummary();
            this.loadEmployees();
          })
          .catch(() => undefined);
      }
    });
  },

  onVoidApply(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    wx.showModal({
      title: '作废申请',
      editable: true,
      placeholderText: '请输入作废理由（可空）',
      success: (res) => {
        if (!res.confirm) return;
        voidSettleApply(id, res.content || '')
          .then(() => {
            wx.showToast({ title: '已作废', icon: 'success' });
            this.loadApplies(1);
          })
          .catch(() => undefined);
      }
    });
  },

  // ---- 选择员工 → ⚙ 进员工薪酬详情 ----
  onEmployeeDetail(e: WechatMiniprogram.TouchEvent) {
    const uid = Number(e.currentTarget.dataset.uid);
    const emp = this.data.employees.find((x) => x.user_id === uid);
    const name = emp ? emp.real_name : '';
    wx.navigateTo({
      url: `/packages/salary/mine/index?user_id=${uid}&name=${encodeURIComponent(name)}`
    });
  },

  /** 完成工序数 › → 完成情况汇总（款式→工序→菲票三级钻取） */
  onGoCompletion() {
    wx.navigateTo({ url: '/packages/salary/completion/index' });
  }
});
