/**
 * 应收列表页（finance 域 P2）：
 * - 状态 chip（全部/unpaid 未收/partial 部分收款/settled 已结清）+ 客户筛选 +
 *   keyword（应收单号/客户名/订单号，后端 Q 口径）；
 * - 剩余金额排序：本地排序切换（后端无排序参数）默认 → 剩余↓ → 剩余↑；
 * - 每行 应收/已收/剩余 三列（金额字符串透传）；
 * - 新建应收弹层（finance.manage）：customer 必填、amount>0、单号后端自动 AR；
 * - 批量收款弹层（评审 Top10 #5，finance.manage）：客户一笔款付多单，
 *   后端 FIFO（最早应收先收）自动分配，溢收款 400 硬拦；
 * - 库存估值入口（finance.manage，后端 stock-valuation 挂 manage）。
 * 权限：页面挂 finance.view；管理动作按 finance.manage 显隐。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { buildCsv, shareCsvFile } from '../../../utils/csv';
import {
  fetchReceivables, createReceivable, batchReceiptByCustomer,
  Receivable, ReceivableStatus, ReceiptMethod
} from '../../../services/finance';
import { fetchCustomers } from '../../../services/catalog';

const STATUS_CHIPS: Array<{ key: '' | ReceivableStatus; label: string }> = [
  { key: '', label: '全部' },
  { key: 'unpaid', label: '未收' },
  { key: 'partial', label: '部分收款' },
  { key: 'settled', label: '已结清' }
];

/** 排序模式循环：0 默认（后端 -id）→ 1 剩余↓ → 2 剩余↑ */
const SORT_LABELS = ['排序:默认', '排序:剩余↓', '排序:剩余↑'];

/** 批量收款方式（口径同单笔核销 ReceiptLine.Method） */
const BATCH_METHODS: Array<{ key: ReceiptMethod; label: string }> = [
  { key: 'cash', label: '现金' },
  { key: 'monthly', label: '月结' },
  { key: 'acceptance', label: '承兑' },
  { key: 'other', label: '其他' }
];

const STATUS_CLASS: Record<ReceivableStatus, string> = {
  unpaid: 'badge-red',
  partial: 'badge-blue',
  settled: 'badge-green'
};

interface RecVM {
  id: number;
  receivableNo: string;
  customerName: string;
  orderNo: string;
  amount: string;
  receivedAmount: string;
  outstanding: string;
  status: ReceivableStatus;
  statusText: string;
  statusClass: string;
  createdAt: string;
  remark: string;
}

function toVM(r: Receivable): RecVM {
  return {
    id: r.id,
    receivableNo: r.receivable_no,
    customerName: r.customer_name || '',
    orderNo: r.order_no || '',
    amount: r.amount,
    receivedAmount: r.received_amount,
    outstanding: r.outstanding,
    status: r.status,
    statusText: r.status_display || r.status,
    statusClass: STATUS_CLASS[r.status] || 'badge-gray',
    createdAt: (r.created_at || '').replace('T', ' ').slice(0, 16),
    remark: r.remark || ''
  };
}

Page({
  data: {
    denied: false,
    canManage: false,
    statusChips: STATUS_CHIPS,
    statusIndex: 0,
    sortLabels: SORT_LABELS,
    sortMode: 0,
    keyword: '',
    customers: [] as Array<{ id: number; label: string }>,
    customerNames: ['全部客户'],
    customerIndex: 0,
    rows: [] as RecVM[],
    loading: false,
    error: false,
    page: 1,
    hasMore: false,
    loaded: false,
    // ---- 新建应收弹层（finance.manage） ----
    createVisible: false,
    createSaving: false,
    createCustomerIndex: 0,
    createAmount: '',
    createRemark: '',
    // ---- 批量收款弹层（评审 Top10 #5，finance.manage） ----
    batchVisible: false,
    batchSaving: false,
    batchCustomerIndex: 0,
    batchTotal: '',
    batchMethodIndex: 0,
    batchMethodLabels: BATCH_METHODS.map((m) => m.label),
    batchReceivedAt: '',
    batchHandler: '',
    batchRemark: ''
  },

  onShow() {
    if (!requireLogin()) return;
    const denied = !hasPerm('finance.view');
    this.setData({
      denied,
      canManage: hasPerm('finance.manage')
    });
    if (!denied) {
      if (!this.data.customers.length) {
        fetchCustomers({ page: 1, page_size: 200 })
          .then((res) => {
            const customers = (res.results || []).map((c) => ({ id: c.id, label: c.name }));
            this.setData({
              customers,
              customerNames: ['全部客户'].concat(customers.map((c) => c.label))
            });
          })
          .catch(() => undefined);
      }
      this.reload();
    }
  },

  onPullDownRefresh() {
    this.reload();
    wx.stopPullDownRefresh();
  },

  onReachBottom() {
    if (this.data.hasMore && !this.data.loading) this.loadPage(this.data.page + 1);
  },

  // ============ 过滤 ============

  onStatusChip(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    if (idx === this.data.statusIndex) return;
    this.setData({ statusIndex: idx });
    this.reload();
  },

  onCustomerChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ customerIndex: Number(e.detail.value) });
    this.reload();
  },

  onKeyword(e: WechatMiniprogram.Input) {
    this.setData({ keyword: e.detail.value });
  },

  onSearch() {
    this.reload();
  },

  onToggleSort() {
    this.setData({ sortMode: (this.data.sortMode + 1) % 3 });
    this.applyLocalSort();
  },

  /** 剩余金额本地排序（按 outstanding 数值；后端无排序参数，仅影响当前页渲染序） */
  applyLocalSort() {
    if (this.data.sortMode === 0) {
      // 恢复后端序：按 id 降序（后端 ordering=-id）
      this.setData({
        rows: this.data.rows.slice().sort((a, b) => b.id - a.id)
      });
      return;
    }
    const dir = this.data.sortMode === 1 ? -1 : 1;
    this.setData({
      rows: this.data.rows.slice().sort((a, b) =>
        (Number(a.outstanding) - Number(b.outstanding)) * dir)
    });
  },

  // ============ 列表 ============

  reload() {
    this.loadPage(1);
  },

  loadPage(page: number) {
    const chip = STATUS_CHIPS[this.data.statusIndex];
    const ci = this.data.customerIndex;
    this.setData({ loading: true, error: false });
    fetchReceivables({
      page,
      page_size: 20,
      status: chip.key || undefined,
      customer_id: ci > 0 && this.data.customers[ci - 1]
        ? this.data.customers[ci - 1].id : undefined,
      keyword: this.data.keyword.trim() || undefined
    })
      .then((res) => {
        const vms = (res.results || []).map(toVM);
        this.setData({
          loading: false,
          loaded: true,
          rows: page === 1 ? vms : this.data.rows.concat(vms),
          page,
          hasMore: !!res.next
        }, () => { if (page === 1) this.applyLocalSort(); });
      })
      .catch(() => this.setData({ loading: false, error: true, loaded: true }));
  },

  onOpenDetail(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    if (!id) return;
    wx.navigateTo({ url: `/packages/admin/receivable-detail/index?id=${id}` });
  },

  // ============ 新建应收（finance.manage） ============

  onOpenCreate() {
    this.setData({
      createVisible: true,
      createCustomerIndex: 0,
      createAmount: '',
      createRemark: ''
    });
  },

  onCreateCustomerChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ createCustomerIndex: Number(e.detail.value) });
  },

  onCreateAmount(e: WechatMiniprogram.Input) {
    this.setData({ createAmount: e.detail.value });
  },

  onCreateRemark(e: WechatMiniprogram.Input) {
    this.setData({ createRemark: e.detail.value });
  },

  onCloseCreate() {
    this.setData({ createVisible: false });
  },

  noop() { /* 阻止冒泡 */ },

  onSaveCreate() {
    const { createCustomerIndex, customers, createAmount, createRemark } = this.data;
    const cust = createCustomerIndex > 0 ? customers[createCustomerIndex - 1] : null;
    if (!cust) {
      wx.showToast({ title: '请选择客户', icon: 'none' });
      return;
    }
    const num = Number(createAmount);
    if (createAmount === '' || !Number.isFinite(num) || num <= 0) {
      wx.showToast({ title: '应收金额必须大于 0', icon: 'none' });
      return;
    }
    this.setData({ createSaving: true });
    createReceivable({
      customer: cust.id,
      amount: createAmount,
      remark: createRemark.trim() || ''
    })
      .then(() => {
        this.setData({ createSaving: false, createVisible: false });
        wx.showToast({ title: '应收已建立', icon: 'success' });
        this.reload();
      })
      .catch(() => this.setData({ createSaving: false }));
  },

  onGoStockValuation() {
    wx.navigateTo({ url: '/packages/admin/stock-valuation/index' });
  },

  // ============ 批量收款（评审 Top10 #5，finance.manage） ============

  onOpenBatch() {
    this.setData({
      batchVisible: true,
      batchCustomerIndex: 0,
      batchTotal: '',
      batchMethodIndex: 0,
      batchReceivedAt: '',
      batchHandler: '',
      batchRemark: ''
    });
  },

  onBatchCustomerChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ batchCustomerIndex: Number(e.detail.value) });
  },

  onBatchTotal(e: WechatMiniprogram.Input) {
    this.setData({ batchTotal: e.detail.value });
  },

  onBatchMethodChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ batchMethodIndex: Number(e.detail.value) });
  },

  onBatchReceivedAtChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ batchReceivedAt: String(e.detail.value || '') });
  },

  onBatchHandler(e: WechatMiniprogram.Input) {
    this.setData({ batchHandler: e.detail.value });
  },

  onBatchRemark(e: WechatMiniprogram.Input) {
    this.setData({ batchRemark: e.detail.value });
  },

  onCloseBatch() {
    this.setData({ batchVisible: false });
  },

  onSaveBatch() {
    const { batchCustomerIndex, customers, batchTotal, batchMethodIndex,
      batchReceivedAt, batchHandler, batchRemark } = this.data;
    const cust = batchCustomerIndex > 0 ? customers[batchCustomerIndex - 1] : null;
    if (!cust) {
      wx.showToast({ title: '请选择客户', icon: 'none' });
      return;
    }
    const num = Number(batchTotal);
    if (batchTotal === '' || !Number.isFinite(num) || num <= 0) {
      wx.showToast({ title: '收款总额必须大于 0', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '确认批量收款',
      content: `${cust.label} 收款 ${batchTotal} 元，将按应收单创建时间从最早开始自动核销；若超出该客户未结总额将整笔拒绝。确认？`,
      success: (res) => {
        if (!res.confirm) return;
        this.setData({ batchSaving: true });
        batchReceiptByCustomer(cust.id, {
          total: batchTotal,
          method: BATCH_METHODS[batchMethodIndex].key,
          received_at: batchReceivedAt || undefined,
          handler: batchHandler.trim() || '',
          remark: batchRemark.trim() || ''
        })
          .then((result) => {
            this.setData({ batchSaving: false, batchVisible: false });
            const lines = result.allocated.map((a) =>
              `${a.receivable_no} 收 ${a.amount}`).join('\n');
            wx.showModal({
              title: '核销完成',
              content: lines + (result.leftover !== '0.00'
                ? `\n剩余未分配：${result.leftover}` : ''),
              showCancel: false
            });
            this.reload();
          })
          .catch(() => this.setData({ batchSaving: false }));
      }
    });
  },

  /** 导出 CSV（评审 Top10 #9）：当前已加载行（下拉补页后自动扩） */
  onExportCsv() {
    if (!this.data.rows.length) {
      wx.showToast({ title: '没有可导出的数据', icon: 'none' });
      return;
    }
    const headers = ['应收单号', '客户', '订单号', '应收金额', '已收', '剩余', '状态', '创建时间', '备注'];
    const rows = this.data.rows.map((r) => [
      r.receivableNo, r.customerName, r.orderNo, r.amount, r.receivedAmount,
      r.outstanding, r.statusText, r.createdAt, r.remark
    ]);
    shareCsvFile(`应收_${Date.now()}.csv`, buildCsv(headers, rows))
      .catch(() => wx.showToast({ title: '导出失败', icon: 'none' }));
  }
});
