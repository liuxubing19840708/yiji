/**
 * 日志管理（管理员，规格 §14）：操作记录列表。
 * - type 筛选 chips（枚举以后端 AuditLog.Type 为准：price/assist/qty/process/revoke/other）
 * - 日期区间用 date-range-bar（date_from/date_to，见服务 fetchAuditLogs）
 * - 分页加载（page_size 20，触底加载下一页）；每条展示 type_display/操作人/目标员工/message/时间
 */
import { requireLogin } from '../../../utils/auth';
import { fetchAuditLogs } from '../../../services/ops';
import { AuditLog } from '../../../services/types';
import { fmtDateTime } from '../../../utils/format';

interface LogView extends AuditLog {
  timeText: string;
}

/** 筛选 chips：key 为空 = 全部 */
const TYPE_CHIPS = [
  { key: '', label: '全部' },
  { key: 'price', label: '修改价格' },
  { key: 'assist', label: '协助登记' },
  { key: 'qty', label: '修改裁床数' },
  { key: 'process', label: '修改工序' },
  { key: 'revoke', label: '撤销登记' },
  { key: 'other', label: '其他' }
];

const PAGE_SIZE = 20;

Page({
  data: {
    chips: TYPE_CHIPS,
    activeType: '',
    startDate: '',
    endDate: '',
    items: [] as LogView[],
    page: 1,
    hasMore: false,
    loading: false,
    /** 首屏失败（可重试） */
    error: false
  },

  onShow() {
    if (!requireLogin()) return;
    if (!this.data.items.length && !this.data.error) {
      this.reload();
    }
  },

  /** 按当前筛选条件重新拉第一页 */
  reload() {
    this.setData({ page: 1 });
    this.fetchPage(true);
  },

  fetchPage(reset: boolean): Promise<unknown> {
    const { activeType, startDate, endDate, page, items } = this.data;
    this.setData({ loading: true, error: false });
    return fetchAuditLogs({
      type: activeType || undefined,
      date_from: startDate || undefined,
      date_to: endDate || undefined,
      page: reset ? 1 : page,
      page_size: PAGE_SIZE
    })
      .then((res) => {
        const rows: LogView[] = res.results.map((it) => ({
          ...it,
          timeText: fmtDateTime(it.created_at)
        }));
        this.setData({
          items: reset ? rows : items.concat(rows),
          page: reset ? 1 : page + 1,
          hasMore: !!res.next,
          loading: false
        });
      })
      .catch(() => {
        this.setData({ loading: false, error: !items.length });
      });
  },

  onTypeTap(e: WechatMiniprogram.TouchEvent) {
    const key = (e.currentTarget.dataset.key as string) || '';
    if (key === this.data.activeType) return;
    this.setData({ activeType: key });
    this.reload();
  },

  onRangeChange(e: WechatMiniprogram.CustomEvent<{ start: string; end: string; quick: string }>) {
    this.setData({ startDate: e.detail.start, endDate: e.detail.end });
    this.reload();
  },

  onReachBottom() {
    if (this.data.loading || !this.data.hasMore) return;
    this.fetchPage(false);
  },

  onPullDownRefresh() {
    this.fetchPage(true).finally(() => wx.stopPullDownRefresh());
  },

  onRetry() {
    this.reload();
  }
});
