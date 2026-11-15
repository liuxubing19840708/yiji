/**
 * 工资对账单（cost 域 P1，工资总额对账 §3.3 必做界面）：
 * - Σ订单人工 vs 整件计件/计件提成/固定工资 逐项差异列表（与发放总额对账）；
 * - 口径差异属预期——页顶固定说明文案（决策口径：「整件计件按票面均摊、
 *   提成差额与固定工资不入订单成本，差异为口径所致非数据错误」）；
 * - 日期缺省=本月 1 日至今天（后端同口径兜底）。
 * 权限：挂 cost.view（成本页子页，宫格同点入口）。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { monthRange } from '../../../utils/format';
import {
  fetchWageReconciliation, WageReconciliation, WageReconciliationRow
} from '../../../services/cost';

Page({
  data: {
    denied: false,
    start: '',
    end: '',
    quick: 'month',
    loading: false,
    error: false,
    /** 页顶固定口径说明（属预期，非数据错误） */
    headline: '整件计件按票面均摊、提成差额与固定工资不入订单成本，差异为口径所致非数据错误。',
    data: null as WageReconciliation | null,
    rows: [] as WageReconciliationRow[]
  },

  onShow() {
    if (!requireLogin()) return;
    this.setData({ denied: !hasPerm('cost.view') });
    if (!this.data.denied) {
      if (!this.data.start) {
        const r = monthRange();
        this.setData({ start: r.start, end: r.end, quick: 'month' });
      }
      this.load();
    }
  },

  onPullDownRefresh() {
    this.load();
    wx.stopPullDownRefresh();
  },

  onRangeChange(e: WechatMiniprogram.CustomEvent<{ start: string; end: string; quick: string }>) {
    this.setData({ start: e.detail.start, end: e.detail.end, quick: e.detail.quick || '' });
    this.load();
  },

  load() {
    const { start, end } = this.data;
    this.setData({ loading: true, error: false });
    fetchWageReconciliation({ date_from: start || undefined, date_to: end || undefined })
      .then((rep) => {
        this.setData({ loading: false, data: rep, rows: rep.rows || [] });
      })
      .catch(() => this.setData({ loading: false, error: true }));
  }
});
