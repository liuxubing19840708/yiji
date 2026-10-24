/**
 * 消息通知（规格 §16.1）：站内信列表，未读加粗+红点，点击标记已读，全部已读。
 * onShow 同步未读数到自定义 tabBar（setUnread）并 setSelected(3)。
 * 订阅消息（wx.requestSubscribeMessage）引导在「我的」页，模板 id 见 profile 页 TODO。
 */
import { requireLogin } from '../../utils/auth';
import { fetchNotifications, markNotificationRead, markAllNotificationsRead } from '../../services/ops';
import { Notification } from '../../services/types';
import { fmtDateTime } from '../../utils/format';

/** 视图模型：is_read 转成 boolean（契约里是 0/1 整型） */
interface NotificationView {
  id: number;
  code: string;
  title: string;
  message: string;
  is_read: boolean;
  created_at: string;
  timeText: string;
}

const PAGE_SIZE = 20;

/** 顶部筛选：全部 / 未读 / 已读 */
const FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'unread', label: '未读' },
  { key: 'read', label: '已读' }
];

/** tabBar 实例（getTabBar 返回 TrivialInstance，需窄化出 setUnread/setSelected） */
interface TabBarLike {
  setUnread?: (n: number) => void;
  setSelected?: (n: number) => void;
}

Page({
  data: {
    filters: FILTERS,
    activeFilter: 'all',
    items: [] as NotificationView[],
    page: 1,
    hasMore: false,
    loading: false,
    error: false,
    unread: 0
  },

  onShow() {
    if (!requireLogin()) return;
    const tab = (this.getTabBar() as unknown) as TabBarLike | undefined;
    if (tab && tab.setSelected) {
      tab.setSelected(3);
    }
    if (!this.data.items.length && !this.data.error) {
      this.reload();
    } else {
      // 回到页面时刷新未读数（其他页面可能触发过通知）
      this.syncUnread();
    }
  },

  /** is_read 筛选：后端识别 '0'/'1'（services 已统一为 0/1） */
  buildQuery(page: number) {
    const { activeFilter } = this.data;
    return {
      is_read: (activeFilter === 'unread' ? 0 : activeFilter === 'read' ? 1 : undefined) as 0 | 1 | undefined,
      page,
      page_size: PAGE_SIZE
    };
  },

  reload() {
    this.setData({ page: 1 });
    this.fetchPage(true);
  },

  fetchPage(reset: boolean): Promise<unknown> {
    const { page, items } = this.data;
    this.setData({ loading: true, error: false });
    return fetchNotifications(this.buildQuery(reset ? 1 : page))
      .then((res) => {
        const rows: NotificationView[] = res.results.map((it) => ({
          ...it,
          is_read: !!it.is_read,
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

  /** 未读数：用 is_read=0 的分页 count（后端暂无专门的未读数接口） */
  syncUnread(): Promise<unknown> {
    return fetchNotifications({ is_read: 0, page: 1, page_size: 1 })
      .then((res) => {
        this.updateUnread(res.count);
      })
      .catch(() => {
        // 静默：未读数拉取失败不影响列表
      });
  },

  updateUnread(unread: number) {
    this.setData({ unread });
    const tab = (this.getTabBar() as unknown) as TabBarLike | undefined;
    if (tab && tab.setUnread) {
      tab.setUnread(unread);
    }
  },

  onFilterTap(e: WechatMiniprogram.TouchEvent) {
    const key = e.currentTarget.dataset.key as string;
    if (key === this.data.activeFilter) return;
    this.setData({ activeFilter: key });
    this.reload();
  },

  /** 点击通知：未读 → 标记已读 */
  onItemTap(e: WechatMiniprogram.TouchEvent) {
    const index = e.currentTarget.dataset.index as number;
    const item = this.data.items[index];
    if (!item || item.is_read) return;
    markNotificationRead(item.id)
      .then(() => {
        this.setData({
          [`items[${index}].is_read`]: true,
          unread: Math.max(0, this.data.unread - 1)
        });
        this.updateUnread(this.data.unread);
      })
      .catch(() => {
        // 请求层已统一 toast
      });
  },

  onMarkAll() {
    if (!this.data.unread) {
      wx.showToast({ title: '没有未读通知', icon: 'none' });
      return;
    }
    markAllNotificationsRead()
      .then(() => {
        wx.showToast({ title: '已全部标记为已读', icon: 'none' });
        const items = this.data.items.map((it) => ({ ...it, is_read: true }));
        this.setData({ items });
        this.updateUnread(0);
      })
      .catch(() => {
        // 请求层已统一 toast
      });
  },

  onReachBottom() {
    if (this.data.loading || !this.data.hasMore) return;
    this.fetchPage(false);
  },

  onPullDownRefresh() {
    Promise.all([this.fetchPage(true), this.syncUnread()]).finally(() => wx.stopPullDownRefresh());
  },

  onRetry() {
    this.reload();
  }
});
