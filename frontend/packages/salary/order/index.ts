/**
 * 订单薪酬（规格 §10.6 / §13.1）：
 * 按款式维度汇总工资支出 —— 款号 / 单价 / 件数 / 总额 卡片列表。
 * 数据走 fetchCompletion（不带 user_id，按 style 聚合）。
 */
import { requireLogin } from '../../../utils/auth';
import { money, monthRange } from '../../../utils/format';
import { fetchCompletion } from '../../../services/salary';

interface StyleSalaryVM {
  styleId: number;
  styleNo: string;
  styleName: string;
  qty: number;
  priceText: string;
  amountText: string;
  processCount: number;
}

Page({
  data: {
    start: '',
    end: '',
    quick: '',
    loading: true,
    error: '',
    styles: [] as StyleSalaryVM[],
    totalQty: 0
  },

  onLoad() {
    const r = monthRange();
    this.setData({ start: r.start, end: r.end, quick: 'month' });
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
    const params: Record<string, string> = {};
    if (this.data.start) params.date_from = this.data.start;
    if (this.data.end) params.date_to = this.data.end;
    return fetchCompletion(params)
      .then((res) => {
        const styles: StyleSalaryVM[] = res.styles.map((s) => ({
          styleId: s.style_id,
          styleNo: s.style_no,
          styleName: s.style_name,
          qty: s.qty,
          priceText: money(s.avg_price),
          amountText: money(s.amount),
          processCount: s.process_count
        }));
        const totalQty = res.styles.reduce((sum, s) => sum + s.qty, 0);
        this.setData({ styles, totalQty, loading: false });
      })
      .catch(() => this.setData({ loading: false, error: '加载失败，请重试' }));
  },

  onRangeChange(e: WechatMiniprogram.CustomEvent<{ start: string; end: string; quick: string }>) {
    this.setData({ start: e.detail.start, end: e.detail.end, quick: e.detail.quick });
    this.reload();
  },

  onGoCompletion() {
    wx.navigateTo({ url: '/packages/salary/completion/index' });
  }
});
