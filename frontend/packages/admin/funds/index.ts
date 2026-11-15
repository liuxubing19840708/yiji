/** 采购应付、供应商付款、其他收支与统一经营现金流。 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { monthRange } from '../../../utils/format';
import {
  fetchPayables, createPayable, voidPayable,
  fetchSupplierPayments, createSupplierPayment, voidSupplierPayment,
  fetchCashEntries, createCashEntry, voidCashEntry, fetchCashFlow,
  Payable, SupplierPayment, CashEntry, CashFlowRow
} from '../../../services/finance';
import { fetchSuppliers, Supplier } from '../../../services/warehouse';

const TABS = [
  { key: 'payables', label: '采购应付' },
  { key: 'payments', label: '供应商付款' },
  { key: 'entries', label: '其他收支' },
  { key: 'cashflow', label: '经营现金流' }
];
const METHODS = [
  { key: 'bank', label: '银行转账' },
  { key: 'cash', label: '现金' },
  { key: 'acceptance', label: '承兑' },
  { key: 'other', label: '其他' }
];

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function dateTimeIso(date: string, time: string): string {
  const [y, m, d] = date.split('-').map(Number);
  const [hour = 0, minute = 0] = time.split(':').map(Number);
  return new Date(y, m - 1, d, hour, minute).toISOString();
}

function positive(value: string): boolean {
  return value.trim() !== '' && Number.isFinite(Number(value)) && Number(value) > 0;
}

Page({
  data: {
    denied: false,
    canManage: false,
    canCashFlow: false,
    tabs: TABS,
    visibleTabs: TABS.slice(0, 3),
    tab: 'payables',
    loading: false,
    loaded: false,
    error: false,
    rows: [] as Array<Payable | SupplierPayment | CashEntry>,
    page: 1,
    hasMore: false,
    dateFrom: '',
    dateTo: '',
    groupBy: 'month',
    flowRows: [] as CashFlowRow[],
    flowTotals: null as CashFlowRow | null,
    flowNote: '',
    suppliers: [] as Supplier[],
    supplierNames: ['选择供应商档案'],
    supplierIndex: 0,
    openPayables: [] as Payable[],
    payableNames: ['不关联应付单'],
    payableIndex: 0,
    methodNames: METHODS.map((m) => m.label),
    methodIndex: 0,
    modal: '',
    saving: false,
    amount: '',
    payableAt: '',
    dueAt: '',
    paidAt: '',
    paidTime: '',
    occurredAt: '',
    occurredTime: '',
    supplierName: '',
    transactionRef: '',
    category: '',
    counterparty: '',
    referenceNo: '',
    direction: 'expense',
    remark: ''
  },

  onShow() {
    if (!requireLogin()) return;
    const denied = !hasPerm('finance.view');
    const canCashFlow = hasPerm('finance.manage') && hasPerm('salary.view');
    const range = monthRange();
    this.setData({
      denied,
      canManage: hasPerm('finance.manage'),
      canCashFlow,
      visibleTabs: canCashFlow ? TABS : TABS.slice(0, 3),
      dateFrom: this.data.dateFrom || range.start,
      dateTo: this.data.dateTo || range.end
    });
    if (!denied) {
      this.loadOptions();
      this.reload();
    }
  },

  onPullDownRefresh() {
    this.reload();
    wx.stopPullDownRefresh();
  },

  onReachBottom() {
    if (this.data.tab !== 'cashflow' && this.data.hasMore && !this.data.loading) {
      this.loadPage(this.data.page + 1);
    }
  },

  loadOptions() {
    if (!this.data.suppliers.length) {
      fetchSuppliers({ page: 1, page_size: 200 }).then((res) => {
        const suppliers = res.results || [];
        this.setData({ suppliers, supplierNames: ['选择供应商档案'].concat(suppliers.map((s) => s.name)) });
      }).catch(() => undefined);
    }
    fetchPayables({ page: 1, page_size: 200 }).then((res) => {
      const openPayables = (res.results || []).filter((p) => p.status !== 'settled' && Number(p.outstanding) > 0);
      this.setData({
        openPayables,
        payableNames: ['不关联应付单'].concat(openPayables.map((p) => `${p.payable_no}｜${p.supplier_name}｜余${p.outstanding}`))
      });
    }).catch(() => undefined);
  },

  onTab(e: WechatMiniprogram.TouchEvent) {
    const tab = String(e.currentTarget.dataset.key || 'payables');
    this.setData({ tab, rows: [], loaded: false, error: false });
    this.reload();
  },

  onDate(e: WechatMiniprogram.PickerChange) {
    const field = String(e.currentTarget.dataset.field || '');
    if (['dateFrom', 'dateTo', 'payableAt', 'dueAt', 'paidAt', 'occurredAt'].indexOf(field) < 0) return;
    this.setData({ [field]: e.detail.value });
  },

  onTime(e: WechatMiniprogram.PickerChange) {
    const field = String(e.currentTarget.dataset.field || '');
    if (['paidTime', 'occurredTime'].indexOf(field) < 0) return;
    this.setData({ [field]: e.detail.value });
  },

  onInput(e: WechatMiniprogram.Input) {
    const field = String(e.currentTarget.dataset.field || '');
    const allowed = ['amount', 'supplierName', 'transactionRef', 'category', 'counterparty', 'referenceNo', 'remark'];
    if (allowed.indexOf(field) >= 0) this.setData({ [field]: e.detail.value });
  },

  onSupplier(e: WechatMiniprogram.PickerChange) {
    this.setData({ supplierIndex: Number(e.detail.value) });
  },

  onPayable(e: WechatMiniprogram.PickerChange) {
    const payableIndex = Number(e.detail.value);
    const payable = payableIndex > 0 ? this.data.openPayables[payableIndex - 1] : null;
    this.setData({ payableIndex, amount: payable ? payable.outstanding : this.data.amount });
  },

  onMethod(e: WechatMiniprogram.PickerChange) {
    this.setData({ methodIndex: Number(e.detail.value) });
  },

  onDirection(e: WechatMiniprogram.TouchEvent) {
    this.setData({ direction: String(e.currentTarget.dataset.key || 'expense') });
  },

  onGroup(e: WechatMiniprogram.TouchEvent) {
    this.setData({ groupBy: String(e.currentTarget.dataset.key || 'month') });
    this.loadCashFlow();
  },

  reload() {
    if (this.data.tab === 'cashflow') this.loadCashFlow();
    else this.loadPage(1);
  },

  loadPage(page: number) {
    this.setData({ loading: true, error: false });
    const params = { page, page_size: 20 };
    const request = this.data.tab === 'payments'
      ? fetchSupplierPayments(params)
      : this.data.tab === 'entries' ? fetchCashEntries(params) : fetchPayables(params);
    request.then((res) => {
      this.setData({
        loading: false,
        loaded: true,
        rows: page === 1 ? (res.results || []) : this.data.rows.concat(res.results || []),
        page,
        hasMore: !!res.next
      });
    }).catch(() => this.setData({ loading: false, loaded: true, error: true }));
  },

  loadCashFlow() {
    const { dateFrom, dateTo, groupBy } = this.data;
    if (!dateFrom || !dateTo || dateFrom > dateTo) {
      wx.showToast({ title: '请选择有效日期范围', icon: 'none' });
      return;
    }
    this.setData({ loading: true, error: false });
    fetchCashFlow({ date_from: dateFrom, date_to: dateTo, group_by: groupBy as 'day' | 'month' })
      .then((res) => this.setData({
        loading: false, loaded: true, flowRows: res.rows || [], flowTotals: res.totals, flowNote: res.note
      }))
      .catch(() => this.setData({ loading: false, loaded: true, error: true }));
  },

  openForm(e: WechatMiniprogram.TouchEvent) {
    const modal = String(e.currentTarget.dataset.kind || '');
    const date = today();
    this.setData({
      modal, saving: false, amount: '', supplierIndex: 0, payableIndex: 0,
      methodIndex: 0, supplierName: '', transactionRef: '', category: '',
      counterparty: '', referenceNo: '', direction: 'expense', remark: '',
      payableAt: date, dueAt: '', paidAt: date, paidTime: '',
      occurredAt: date, occurredTime: ''
    });
  },

  closeForm() {
    if (!this.data.saving) this.setData({ modal: '' });
  },

  noop() { /* 阻止关闭弹层冒泡 */ },

  supplierBody(): Record<string, unknown> {
    const selected = this.data.supplierIndex > 0 ? this.data.suppliers[this.data.supplierIndex - 1] : null;
    if (selected) return { supplier: selected.id };
    return { supplier_name: this.data.supplierName.trim() };
  },

  savePayable() {
    const supplier = this.supplierBody();
    if (!supplier.supplier && !supplier.supplier_name) {
      wx.showToast({ title: '请选择或填写供应商', icon: 'none' });
      return;
    }
    if (!positive(this.data.amount)) {
      wx.showToast({ title: '应付金额必须大于0', icon: 'none' });
      return;
    }
    if (this.data.dueAt && this.data.dueAt < this.data.payableAt) {
      wx.showToast({ title: '到期日不能早于应付日', icon: 'none' });
      return;
    }
    this.setData({ saving: true });
    createPayable({
      ...supplier, amount: this.data.amount, payable_at: this.data.payableAt,
      due_at: this.data.dueAt || null, remark: this.data.remark.trim()
    }).then(() => this.saved('应付已建立')).catch(() => this.setData({ saving: false }));
  },

  savePayment() {
    const payable = this.data.payableIndex > 0 ? this.data.openPayables[this.data.payableIndex - 1] : null;
    if (!positive(this.data.amount)) {
      wx.showToast({ title: '付款金额必须大于0', icon: 'none' });
      return;
    }
    if (payable && Number(this.data.amount) > Number(payable.outstanding)) {
      wx.showToast({ title: '付款不能超过未付余额', icon: 'none' });
      return;
    }
    const supplier = payable ? { supplier_name: payable.supplier_name } : this.supplierBody();
    if (!payable && !supplier.supplier && !supplier.supplier_name) {
      wx.showToast({ title: '请选择应付单或供应商', icon: 'none' });
      return;
    }
    this.setData({ saving: true });
    createSupplierPayment({
      ...supplier, payable: payable ? payable.id : null, amount: this.data.amount,
      paid_at: dateTimeIso(this.data.paidAt, this.data.paidTime),
      method: METHODS[this.data.methodIndex].key,
      transaction_ref: this.data.transactionRef.trim(), remark: this.data.remark.trim()
    }).then(() => this.saved('付款已登记')).catch(() => this.setData({ saving: false }));
  },

  saveEntry() {
    if (!positive(this.data.amount) || !this.data.category.trim()) {
      wx.showToast({ title: '请填写分类和有效金额', icon: 'none' });
      return;
    }
    this.setData({ saving: true });
    createCashEntry({
      direction: this.data.direction, category: this.data.category.trim(), amount: this.data.amount,
      occurred_at: dateTimeIso(this.data.occurredAt, this.data.occurredTime),
      counterparty: this.data.counterparty.trim(),
      method: METHODS[this.data.methodIndex].key, reference_no: this.data.referenceNo.trim(),
      remark: this.data.remark.trim()
    }).then(() => this.saved('收支已登记')).catch(() => this.setData({ saving: false }));
  },

  saved(message: string) {
    this.setData({ saving: false, modal: '' });
    wx.showToast({ title: message, icon: 'success' });
    this.loadOptions();
    this.reload();
  },

  voidPayable(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    wx.showModal({
      title: '作废应付', content: '存在有效付款时后端会拒绝。作废后不再计入未付余额，确认？',
      success: (res) => {
        if (!res.confirm) return;
        voidPayable(id).then(() => this.saved('应付已作废')).catch(() => undefined);
      }
    });
  },

  voidWithReason(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const kind = String(e.currentTarget.dataset.kind || '');
    wx.showModal({
      title: kind === 'payment' ? '作废供应商付款' : '作废收支记录',
      content: '请输入作废原因（必填）。作废保留审计事实，不删除流水。',
      editable: true,
      placeholderText: '作废原因',
      success: (res) => {
        if (!res.confirm) return;
        const reason = (res.content || '').trim();
        if (!reason) {
          wx.showToast({ title: '作废原因不能为空', icon: 'none' });
          return;
        }
        const request = kind === 'payment' ? voidSupplierPayment(id, reason) : voidCashEntry(id, reason);
        request.then(() => this.saved('流水已作废')).catch(() => undefined);
      }
    });
  }
});
