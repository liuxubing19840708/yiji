/**
 * 与我相关的变更（调研 §8.3 痛点 #3，切片 §22.3）：
 * 员工可随时查看与自己相关的全部审计记录——谁改了我的件数/单价、谁替我代登、
 * 谁撤销了我的登记。数据源与「日志管理」同一条 AuditLog 流（GET /audit-logs?scope=mine，
 * 后端只返回 target_user 或 operator 是本人的记录），纯可见性开放、无需新表。
 * 展示字段：类型徽标 + message（服务端插值后的完整文案）+ 时间；分页触底加载。
 */
import { requireLogin } from '../../../utils/auth';
import { fetchAuditLogs } from '../../../services/ops';
import { AuditLog } from '../../../services/types';
import { fmtDateTime } from '../../../utils/format';

interface ChangeView extends AuditLog {
  timeText: string;
}

const PAGE_SIZE = 20;

Page({
  data: {
    items: [] as ChangeView[],
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

  reload() {
    this.setData({ page: 1 });
    this.fetchPage(true);
  },

  fetchPage(reset: boolean): Promise<unknown> {
    const { page, items } = this.data;
    this.setData({ loading: true, error: false });
    return fetchAuditLogs({ scope: 'mine', page: reset ? 1 : page, page_size: PAGE_SIZE })
      .then((res) => {
        const rows: ChangeView[] = res.results.map((it) => ({
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
