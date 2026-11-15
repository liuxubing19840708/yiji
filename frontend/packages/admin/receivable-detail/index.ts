/**
 * 应收详情页（finance 域 P2，finance.manage 动作）：
 * - 行数据：走 GET /finance/receivables/{id} 详情端点（INT 2026-10-24 补缺口 A）；
 *   详情异常时回落列表 keyword 回查（兜底不阻塞）；
 * - 核销弹层：金额/方式/经手人/备注——提交前预校验 本次+已收≤应收
 *   （超收就地报错不发请求，后端 400 硬拦兜底）；
 * - 撤销收款：确认弹窗写明反转影响（回退已收、重算状态、最近一条）；
 * - 作废：有活动核销须先全部撤销（后端 400），确认弹窗写明下架影响；
 * - 收款时间线：GET /finance/receivables/{id}/receipt-lines 逐笔流水
 *   （INT 2026-10-24 补缺口 B，倒序，已撤销行不显）。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import {
  fetchReceivable, fetchReceivables, fetchReceiptLines,
  addReceipt, revokeLastReceipt, voidReceivable,
  Receivable, ReceiptLine, ReceiptMethod
} from '../../../services/finance';

/** 收款方式（与后端 ReceiptLine.Method 一致） */
const METHODS: Array<{ key: ReceiptMethod; label: string }> = [
  { key: 'cash', label: '现款' },
  { key: 'monthly', label: '月结' },
  { key: 'acceptance', label: '承兑' },
  { key: 'other', label: '其他' }
];

Page({
  data: {
    denied: false,
    canManage: false,
    id: 0,
    no: '',
    loading: true,
    error: false,
    gone: false,
    rec: null as Receivable | null,
    /** 收款流水（倒序；已撤销行后端不回） */
    lines: [] as ReceiptLine[],
    // ---- 核销弹层 ----
    receiptVisible: false,
    receiptSaving: false,
    methodLabels: METHODS.map((m) => m.label),
    methodIndex: 0,
    amount: '',
    receivedAt: '',
    handler: '',
    remark: '',
    /** 超收/非法金额就地报错（不发请求） */
    receiptError: ''
  },

  onLoad(query: Record<string, string | undefined>) {
    this.setData({
      id: Number(query.id || 0),
      no: query.no || '',
      denied: !hasPerm('finance.view'),
      canManage: hasPerm('finance.manage')
    });
  },

  onShow() {
    if (!requireLogin()) return;
    if (this.data.denied || !this.data.id) return;
    this.load();
  },

  onPullDownRefresh() {
    this.load();
    wx.stopPullDownRefresh();
  },

  /** 详情端点直取（缺口 A）；异常回落列表 keyword 回查（软删行列表不显 → gone） */
  load() {
    this.setData({ loading: true, error: false, gone: false });
    fetchReceivable(this.data.id)
      .then((row) => {
        this.setData({ loading: false, rec: row, gone: false });
        this.loadLines();
      })
      .catch(() => {
        if (!this.data.no) {
          this.setData({ loading: false, error: true });
          return;
        }
        fetchReceivables({ keyword: this.data.no, page: 1, page_size: 20 })
          .then((res) => {
            const row = (res.results || []).find((r) => r.id === this.data.id) || null;
            this.setData({ loading: false, rec: row, gone: !row });
            if (row) this.loadLines();
          })
          .catch(() => this.setData({ loading: false, error: true }));
      });
  },

  /** 收款流水（缺口 B；静默失败不影响主信息展示） */
  loadLines() {
    fetchReceiptLines(this.data.id)
      .then((lines) => this.setData({ lines: lines || [] }))
      .catch(() => this.setData({ lines: [] }));
  },

  // ============ 核销（finance.manage） ============

  onOpenReceipt() {
    const rec = this.data.rec;
    if (!rec) return;
    if (rec.status === 'settled') {
      wx.showToast({ title: '已结清，如需调整请先撤销', icon: 'none' });
      return;
    }
    this.setData({
      receiptVisible: true,
      methodIndex: 0,
      amount: '',
      receivedAt: '',
      handler: '',
      remark: '',
      receiptError: ''
    });
  },

  onAmount(e: WechatMiniprogram.Input) {
    this.setData({ amount: e.detail.value, receiptError: '' });
  },

  onHandler(e: WechatMiniprogram.Input) {
    this.setData({ handler: e.detail.value });
  },

  onRemark(e: WechatMiniprogram.Input) {
    this.setData({ remark: e.detail.value });
  },

  onMethodChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ methodIndex: Number(e.detail.value) });
  },

  onReceivedAtChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ receivedAt: String(e.detail.value || '') });
  },

  onCloseReceipt() {
    this.setData({ receiptVisible: false });
  },

  noop() { /* 阻止冒泡 */ },

  onSaveReceipt() {
    const rec = this.data.rec;
    if (!rec) return;
    const amt = Number(this.data.amount);
    if (this.data.amount === '' || !Number.isFinite(amt) || amt <= 0) {
      this.setData({ receiptError: '核销金额必须大于 0' });
      return;
    }
    // 超收前端预校验（后端 400 硬拦兜底）：本次 + 已收 ≤ 应收
    const received = Number(rec.received_amount);
    const total = Number(rec.amount);
    if (amt + received > total) {
      const over = (amt + received - total).toFixed(2);
      this.setData({
        receiptError: `超额收款：应收 ${rec.amount}，已收 ${rec.received_amount}，本笔超出 ${over} 元（多收须拦截）`
      });
      return;
    }
    const method = METHODS[this.data.methodIndex];
    this.setData({ receiptSaving: true, receiptError: '' });
    addReceipt(this.data.id, {
      amount: this.data.amount,
      method: method ? method.key : 'cash',
      received_at: this.data.receivedAt || undefined,
      handler: this.data.handler.trim() || undefined,
      remark: this.data.remark.trim() || undefined
    })
      .then(() => {
        this.setData({ receiptSaving: false, receiptVisible: false });
        wx.showToast({ title: '核销成功', icon: 'success' });
        this.load();
      })
      .catch(() => this.setData({ receiptSaving: false }));
  },

  // ============ 撤销收款 / 作废（finance.manage） ============

  onRevoke() {
    const rec = this.data.rec;
    if (!rec) return;
    if (Number(rec.received_amount) <= 0) {
      wx.showToast({ title: '无可撤销的核销记录', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '撤销最近一条收款',
      content: `将撤销 ${rec.receivable_no} 最近一条核销：已收金额反向回退、状态重算（撤销仅对最近一条生效，流水保留可追）。确定？`,
      confirmColor: '#F53F3F',
      success: (m) => {
        if (!m.confirm) return;
        wx.showLoading({ title: '撤销中…' });
        revokeLastReceipt(this.data.id)
          .then(() => {
            wx.hideLoading();
            wx.showToast({ title: '已撤销', icon: 'success' });
            this.load();
          })
          .catch(() => wx.hideLoading());
      }
    });
  },

  onVoid() {
    const rec = this.data.rec;
    if (!rec) return;
    wx.showModal({
      title: '作废应收',
      content: `作废后 ${rec.receivable_no} 从列表下架（软删，日志可追）；有收款记录须先全部撤销。确定？`,
      confirmColor: '#F53F3F',
      success: (m) => {
        if (!m.confirm) return;
        wx.showLoading({ title: '作废中…' });
        voidReceivable(this.data.id)
          .then(() => {
            wx.hideLoading();
            wx.showToast({ title: '已作废', icon: 'success' });
            setTimeout(() => wx.navigateBack(), 600);
          })
          .catch(() => wx.hideLoading());
      }
    });
  }
});
