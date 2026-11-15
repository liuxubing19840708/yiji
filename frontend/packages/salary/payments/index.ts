/**
 * 工资实付：支付事实列表、登记、多结算分摊、只读详情及必填原因作废。
 * 无 salary.view 的员工只请求本人列表；登记/作废按钮按管理权限点显隐。
 */
import { requireLogin } from '../../../utils/auth';
import { hasAnyPerm, hasPerm } from '../../../utils/perm';
import { fmtDate, fmtDateTime, money, monthRange } from '../../../utils/format';
import {
  createSalaryPayment,
  fetchCompanyEmployees,
  fetchSalaryPayment,
  fetchSalaryPayments,
  fetchSettlements,
  SalaryEmployeeCard,
  SalaryPayment,
  SalaryPaymentAllocation,
  SalaryPaymentMethod,
  voidSalaryPayment
} from '../../../services/salary';
import { Settlement } from '../../../services/types';

type Panel = '' | 'create' | 'detail';
type AllocationMode = 'none' | 'settlement';

interface MethodOption {
  value: SalaryPaymentMethod;
  label: string;
}

interface SettlementSource extends Settlement {
  wage_detail?: Record<string, string> | null;
  employeeAmountCents: number;
  paidCents: number;
  outstandingCents: number;
  employeeAmountText: string;
  paidText: string;
  outstandingText: string;
  title: string;
}

interface AllocationDraft {
  settlement_id: number;
  amount: string;
  title: string;
  periodText: string;
  snapshotText: string;
  paidText: string;
  outstandingText: string;
  outstandingCents: number;
}

interface PaymentAllocationVM {
  id: number;
  title: string;
  amountText: string;
  settlementAmountText: string;
  paidAmountText: string;
  outstandingText: string;
}

interface PaymentVM {
  id: number;
  employee: number;
  employeeName: string;
  paidAt: string;
  amountText: string;
  methodText: string;
  transactionRef: string;
  note: string;
  status: 'active' | 'void';
  statusDisplay: string;
  canVoid: boolean;
  allocationSummary: string;
  operatorName: string;
  createdAt: string;
  voidReason: string;
  voidedByName: string;
  voidedAt: string;
  allocations: PaymentAllocationVM[];
}

const METHODS: MethodOption[] = [
  { value: 'bank', label: '银行转账' },
  { value: 'cash', label: '现金' },
  { value: 'wechat', label: '微信' },
  { value: 'alipay', label: '支付宝' },
  { value: 'other', label: '其他' }
];

function methodText(value: string): string {
  const found = METHODS.find((item) => item.value === value);
  return found ? found.label : (value || '其他');
}

/** 金额输入严格限制到分；运算只使用整数分，避免浮点误差。 */
function parseCents(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const text = String(value).trim();
  if (!/^\d+(?:\.\d{1,2})?$/.test(text)) return null;
  const [integer, decimal = ''] = text.split('.');
  const cents = Number(integer) * 100 + Number((decimal + '00').slice(0, 2));
  return Number.isSafeInteger(cents) ? cents : null;
}

function centsText(value: number | null): string {
  return value === null ? '0.00' : (value / 100).toFixed(2);
}

function localDateTime(): string {
  const now = new Date();
  const h = `${now.getHours()}`.padStart(2, '0');
  const m = `${now.getMinutes()}`.padStart(2, '0');
  return `${fmtDate(now)} ${h}:${m}`;
}

function paymentVM(row: SalaryPayment, canManage: boolean): PaymentVM {
  const allocations = (row.allocations || []).map((item: SalaryPaymentAllocation): PaymentAllocationVM => ({
    id: item.id,
    title: `结算单 #${item.settlement}`,
    amountText: money(item.amount),
    settlementAmountText: money(item.settlement_amount),
    paidAmountText: money(item.paid_amount),
    outstandingText: money(item.outstanding)
  }));
  return {
    id: row.id,
    employee: row.employee,
    employeeName: row.employee_name || '—',
    paidAt: fmtDateTime(row.paid_at),
    amountText: money(row.amount),
    methodText: methodText(row.method),
    transactionRef: row.transaction_ref || '—',
    note: row.note || '',
    status: row.status,
    statusDisplay: row.status_display || (row.status === 'active' ? '有效' : '已作废'),
    canVoid: canManage && row.status === 'active',
    allocationSummary: allocations.length ? `${allocations.length} 笔结算分摊` : '无结算支付',
    operatorName: row.operator_name || '—',
    createdAt: fmtDateTime(row.created_at),
    voidReason: row.void_reason || '',
    voidedByName: row.voided_by_name || '—',
    voidedAt: fmtDateTime(row.voided_at || ''),
    allocations
  };
}

Page({
  data: {
    loading: true,
    error: '',
    start: '',
    end: '',
    quick: '',
    canManage: false,
    canViewAll: false,
    employees: [] as SalaryEmployeeCard[],
    employeeOptions: [] as string[],
    employeeFilterIndex: 0,
    statusOptions: ['全部状态', '有效', '已作废'],
    statusIndex: 0,
    list: [] as PaymentVM[],
    page: 1,
    hasMore: false,
    loadingMore: false,

    panel: '' as Panel,
    saving: false,
    formError: '',
    sourcesLoading: false,
    sourcesError: '',
    employeeIndex: 0,
    selectedEmployeeId: 0,
    amount: '',
    paidAt: '',
    paidDate: '',
    paidTime: '',
    methodIndex: 0,
    methodOptions: METHODS.map((item) => item.label),
    transactionRef: '',
    note: '',
    allocationMode: 'none' as AllocationMode,
    settlements: [] as SettlementSource[],
    selectableSettlements: [] as SettlementSource[],
    settlementOptions: [] as string[],
    settlementIndex: 0,
    allocations: [] as AllocationDraft[],
    allocationTotalText: '0.00',
    allocationDifferenceText: '0.00',
    allocationDifference: 0,
    allocationMatched: false,

    detailLoading: false,
    detail: null as PaymentVM | null,
    voidTargetId: 0,
    voidReason: '',
    voidError: '',
    voiding: false
  },

  onLoad() {
    const range = monthRange();
    const paidAt = localDateTime();
    this.setData({
      start: range.start,
      end: range.end,
      quick: 'month',
      canManage: hasAnyPerm(['salary.manage', 'salary.review']),
      canViewAll: hasPerm('salary.view'),
      paidAt,
      paidDate: paidAt.slice(0, 10),
      paidTime: paidAt.slice(11, 16)
    });
  },

  onShow() {
    if (!requireLogin()) return;
    this.setData({
      canManage: hasAnyPerm(['salary.manage', 'salary.review']),
      canViewAll: hasPerm('salary.view')
    });
    this.loadEmployees();
    this.loadList(1);
  },

  onPullDownRefresh() {
    this.loadList(1).then(() => wx.stopPullDownRefresh());
  },

  onReachBottom() {
    if (!this.data.loadingMore && this.data.hasMore && !this.data.panel) {
      this.loadList(this.data.page + 1);
    }
  },

  onRangeChange(e: WechatMiniprogram.CustomEvent<{ start: string; end: string; quick: string }>) {
    const { start, end, quick } = e.detail;
    this.setData({ start, end, quick });
    this.loadList(1);
  },

  listParams(page: number): Record<string, string | number> {
    const params: Record<string, string | number> = { page, page_size: 20 };
    if (this.data.start) params.date_from = this.data.start;
    if (this.data.end) params.date_to = this.data.end;
    if (this.data.canViewAll) {
      const employee = this.data.employees[this.data.employeeFilterIndex - 1];
      if (employee) params.employee_id = employee.user_id;
    }
    if (this.data.statusIndex === 1) params.status = 'active';
    if (this.data.statusIndex === 2) params.status = 'void';
    return params;
  },

  loadList(page: number): Promise<void> {
    this.setData(page === 1
      ? { loading: true, error: '', list: [], hasMore: false }
      : { loadingMore: true });
    return fetchSalaryPayments(this.listParams(page))
      .then((res) => {
        const rows = (res.results || []).map((row) => paymentVM(row, this.data.canManage));
        this.setData({
          loading: false,
          loadingMore: false,
          list: page === 1 ? rows : this.data.list.concat(rows),
          page,
          hasMore: !!res.next
        });
      })
      .catch(() => this.setData({
        loading: false,
        loadingMore: false,
        error: page === 1 ? '加载失败，请重试' : ''
      }));
  },

  loadEmployees(): Promise<void> {
    // 员工卡和结算单均属于全员薪酬查询域，不能由发放/复核权限隐式放大。
    if (!this.data.canManage || !this.data.canViewAll) return Promise.resolve();
    return fetchCompanyEmployees({ date_from: this.data.start, date_to: this.data.end })
      .then((list) => this.setData({
        employees: list,
        employeeOptions: ['全部可见员工'].concat(list.map((item) => (
          `${item.real_name}${item.status_display ? `（${item.status_display}）` : ''}`
        )))
      }))
      .catch(() => undefined);
  },

  onEmployeeFilterChange(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ employeeFilterIndex: Number(e.detail.value) });
    this.loadList(1);
  },

  onStatusFilterChange(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ statusIndex: Number(e.detail.value) });
    this.loadList(1);
  },

  onOpenCreate() {
    if (!this.data.canManage || !this.data.canViewAll) return;
    const paidAt = localDateTime();
    this.setData({
      panel: 'create',
      saving: false,
      formError: '',
      sourcesError: '',
      employeeIndex: 0,
      selectedEmployeeId: 0,
      amount: '',
      paidAt,
      paidDate: paidAt.slice(0, 10),
      paidTime: paidAt.slice(11, 16),
      methodIndex: 0,
      transactionRef: '',
      note: '',
      allocationMode: 'none',
      allocations: [],
      allocationTotalText: '0.00',
      allocationDifferenceText: '0.00',
      allocationDifference: 0,
      allocationMatched: false
    });
    this.loadSources();
  },

  onClosePanel() {
    if (!this.data.saving) this.setData({ panel: '', formError: '', sourcesError: '' });
  },

  onCreateEmployeeChange(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    const index = Number(e.detail.value);
    const employee = this.data.employees[index];
    this.setData({
      employeeIndex: index,
      selectedEmployeeId: employee ? employee.user_id : 0,
      allocations: [],
      settlementIndex: 0,
      formError: ''
    });
    this.rebuildSources();
  },

  onAmountInput(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ amount: e.detail.value, formError: '' });
    this.recalculateAllocations();
  },

  onPaidDateChange(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({
      paidDate: e.detail.value,
      paidAt: `${e.detail.value} ${this.data.paidTime}`,
      formError: ''
    });
  },

  onPaidTimeChange(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({
      paidTime: e.detail.value,
      paidAt: `${this.data.paidDate} ${e.detail.value}`,
      formError: ''
    });
  },

  onMethodChange(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ methodIndex: Number(e.detail.value) });
  },

  onTransactionInput(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ transactionRef: e.detail.value, formError: '' });
  },

  onNoteInput(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ note: e.detail.value });
  },

  onAllocationModeChange(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ allocationMode: e.detail.value as AllocationMode, formError: '' });
    this.recalculateAllocations();
  },

  fetchAllSettlements(page: number, accumulated: Settlement[]): Promise<Settlement[]> {
    return fetchSettlements({ page, page_size: 100 }).then((res) => {
      const rows = accumulated.concat(res.results || []);
      if (res.next && page < 100) return this.fetchAllSettlements(page + 1, rows);
      return rows;
    });
  },

  fetchAllPayments(page: number, accumulated: SalaryPayment[]): Promise<SalaryPayment[]> {
    return fetchSalaryPayments({ page, page_size: 100 }).then((res) => {
      const rows = accumulated.concat(res.results || []);
      if (res.next && page < 100) return this.fetchAllPayments(page + 1, rows);
      return rows;
    });
  },

  loadSources(): Promise<void> {
    this.setData({ sourcesLoading: true, sourcesError: '' });
    return Promise.all([
      this.fetchAllSettlements(1, []),
      this.fetchAllPayments(1, [])
    ]).then(([settlements, payments]) => {
      const raw = settlements.map((item) => ({
        item,
        employeeAmountCents: 0,
        paidCents: 0
      }));
      const byEmployee = new Map<number, Array<{ item: Settlement; employeeAmountCents: number; paidCents: number }>>();
      raw.forEach((source) => {
        const item = source.item as SettlementSource;
        if (item.status !== 'settled') return;
        if (item.user !== null && item.user !== undefined) {
          source.employeeAmountCents = parseCents(item.amount) || 0;
          const sources = byEmployee.get(item.user) || [];
          sources.push(source);
          byEmployee.set(item.user, sources);
          return;
        }
        const detail = item.wage_detail || {};
        Object.keys(detail).forEach((uid) => {
          const userId = Number(uid);
          const sources = byEmployee.get(userId) || [];
          sources.push({ ...source, employeeAmountCents: parseCents(detail[uid]) || 0 });
          byEmployee.set(userId, sources);
        });
      });
      payments.filter((payment) => payment.status === 'active').forEach((payment) => {
        const sources = byEmployee.get(payment.employee) || [];
        (payment.allocations || []).forEach((allocation) => {
          const source = sources.find((candidate) => candidate.item.id === allocation.settlement);
          if (source) source.paidCents += parseCents(allocation.amount) || 0;
        });
      });
      const computed: SettlementSource[] = [];
      byEmployee.forEach((sources) => sources.forEach((source) => {
        const outstandingCents = Math.max(0, source.employeeAmountCents - source.paidCents);
        if (outstandingCents <= 0) return;
        computed.push(Object.assign({}, source.item, {
          employeeAmountCents: source.employeeAmountCents,
          paidCents: source.paidCents,
          outstandingCents,
          employeeAmountText: money(centsText(source.employeeAmountCents)),
          paidText: money(centsText(source.paidCents)),
          outstandingText: money(centsText(outstandingCents)),
          title: `#${source.item.id} · ${source.item.period_start} 至 ${source.item.period_end} · 可付 ¥${money(centsText(outstandingCents))}`
        }));
      }));
      this.setData({ settlements: computed, sourcesLoading: false });
      this.rebuildSources();
    }).catch((err: Error) => this.setData({
      sourcesLoading: false,
      sourcesError: `${err.message}。仍可选择无结算支付，或关闭后重试。`,
      settlements: [],
      selectableSettlements: [],
      settlementOptions: []
    }));
  },

  rebuildSources() {
    const uid = this.data.selectedEmployeeId;
    const selectedIds = this.data.allocations.map((item) => item.settlement_id);
    const selectable = this.data.settlements.filter((item) => {
      const belongs = item.user === uid || (item.user === null && (item.wage_detail || {})[String(uid)] !== undefined);
      return belongs && selectedIds.indexOf(item.id) < 0;
    });
    this.setData({
      selectableSettlements: selectable,
      settlementOptions: selectable.map((item) => item.title),
      settlementIndex: 0
    });
    this.recalculateAllocations();
  },

  onSettlementChange(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ settlementIndex: Number(e.detail.value) });
  },

  onAddAllocation() {
    if (!this.data.selectedEmployeeId) {
      this.setData({ formError: '请先选择员工' });
      return;
    }
    const settlement = this.data.selectableSettlements[this.data.settlementIndex];
    if (!settlement) return;
    const row: AllocationDraft = {
      settlement_id: settlement.id,
      amount: centsText(settlement.outstandingCents),
      title: `结算单 #${settlement.id}`,
      periodText: `${settlement.period_start} 至 ${settlement.period_end}`,
      snapshotText: settlement.employeeAmountText,
      paidText: settlement.paidText,
      outstandingText: settlement.outstandingText,
      outstandingCents: settlement.outstandingCents
    };
    this.setData({ allocations: this.data.allocations.concat(row), formError: '' });
    this.rebuildSources();
  },

  onAllocationInput(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    const id = Number(e.currentTarget.dataset.id);
    const allocations = this.data.allocations.map((item) => (
      item.settlement_id === id ? Object.assign({}, item, { amount: e.detail.value }) : item
    ));
    this.setData({ allocations, formError: '' });
    this.recalculateAllocations();
  },

  onRemoveAllocation(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    this.setData({ allocations: this.data.allocations.filter((item) => item.settlement_id !== id) });
    this.rebuildSources();
  },

  recalculateAllocations() {
    const total = this.data.allocations.reduce((sum, item) => sum + (parseCents(item.amount) || 0), 0);
    const amount = parseCents(this.data.amount);
    const difference = amount === null ? -total : amount - total;
    this.setData({
      allocationTotalText: money(centsText(total)),
      allocationDifferenceText: money(centsText(Math.abs(difference))),
      allocationDifference: difference,
      allocationMatched: difference === 0
    });
  },

  validatePayment(): string {
    if (!this.data.selectedEmployeeId) return '请选择员工';
    if (!this.data.paidAt) return '请选择实付时间';
    if (!this.data.transactionRef.trim()) return '请输入外部流水号';
    const amount = parseCents(this.data.amount);
    if (amount === null || amount <= 0) return '实付金额必须为大于 0 的两位以内小数';
    if (this.data.allocationMode === 'none') return '';
    if (!this.data.allocations.length) return '请添加结算分摊，或选择无结算支付';
    for (const row of this.data.allocations) {
      const cents = parseCents(row.amount);
      if (cents === null || cents <= 0) return `结算单 #${row.settlement_id} 的分摊金额必须大于 0`;
      if (cents > row.outstandingCents) return `结算单 #${row.settlement_id} 的分摊不能超过当前可付余额`;
    }
    if (this.data.allocationDifference !== 0) return '分摊金额合计必须等于实付金额';
    return '';
  },

  onSubmitPayment() {
    if (this.data.saving) return;
    const error = this.validatePayment();
    if (error) {
      this.setData({ formError: error });
      return;
    }
    const paidAt = new Date(this.data.paidAt.replace(' ', 'T'));
    if (Number.isNaN(paidAt.getTime())) {
      this.setData({ formError: '实付时间格式不正确' });
      return;
    }
    this.setData({ saving: true, formError: '' });
    createSalaryPayment({
      employee_id: this.data.selectedEmployeeId,
      paid_at: paidAt.toISOString(),
      method: METHODS[this.data.methodIndex].value,
      transaction_ref: this.data.transactionRef.trim(),
      note: this.data.note,
      amount: centsText(parseCents(this.data.amount)),
      allocations: this.data.allocationMode === 'settlement'
        ? this.data.allocations.map((item) => ({
          settlement_id: item.settlement_id,
          amount: centsText(parseCents(item.amount))
        }))
        : []
    }).then(() => {
      wx.showToast({ title: '实付工资已登记', icon: 'success' });
      this.setData({ panel: '', saving: false });
      this.loadList(1);
    }).catch((err: Error) => this.setData({ saving: false, formError: err.message }));
  },

  onOpenDetail(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    this.setData({ panel: 'detail', detail: null, detailLoading: true, formError: '' });
    fetchSalaryPayment(id)
      .then((row) => this.setData({ detail: paymentVM(row, this.data.canManage), detailLoading: false }))
      .catch((err: Error) => this.setData({ detailLoading: false, formError: err.message }));
  },

  onOpenVoid(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canManage) return;
    this.setData({
      voidTargetId: Number(e.currentTarget.dataset.id),
      voidReason: '',
      voidError: ''
    });
  },

  onCloseVoid() {
    if (!this.data.voiding) this.setData({ voidTargetId: 0, voidReason: '', voidError: '' });
  },

  onVoidReasonInput(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ voidReason: e.detail.value, voidError: '' });
  },

  onSubmitVoid() {
    if (this.data.voiding) return;
    const reason = this.data.voidReason.trim();
    if (!reason) {
      this.setData({ voidError: '请填写作废原因' });
      return;
    }
    const id = this.data.voidTargetId;
    this.setData({ voiding: true, voidError: '' });
    voidSalaryPayment(id, reason).then((row) => {
      wx.showToast({ title: '支付已作废', icon: 'success' });
      this.setData({
        voiding: false,
        voidTargetId: 0,
        detail: this.data.detail && this.data.detail.id === id
          ? paymentVM(row, this.data.canManage)
          : this.data.detail
      });
      this.loadList(1);
    }).catch((err: Error) => this.setData({ voiding: false, voidError: err.message }));
  },

  noop() {}
});
