/**
 * 客户端日志（管理员）：小程序端 JS 异常/接口失败/动线埋点的服务端日志查看。
 * - level 筛选 chips（info/warn/error）+ 日期区间（date-range-bar）+ 员工下钻（employee-picker 单选）
 * - 厂→用户隔离由后端保证：只回本公司在职成员的日志（含选厂前的匿名记录不展示）
 * - detail（异常栈/参数）默认折叠，点击卡片展开
 * - 分页加载（page_size 20，触底加载下一页）
 */
import { requireLogin } from '../../../utils/auth';
import { fetchClientLogs } from '../../../services/ops';
import { ClientLogItem } from '../../../services/types';
import { fmtDateTime } from '../../../utils/format';

interface LogView extends ClientLogItem {
  timeText: string;
  expanded: boolean;
  /** 级别徽标样式类（wxml 不能访问模块常量，行映射时预计算） */
  lvClass: string;
}

const LEVEL_CHIPS = [
  { key: '', label: '全部' },
  { key: 'error', label: '错误' },
  { key: 'warn', label: '告警' },
  { key: 'info', label: '信息' }
];

/** 级别徽标配色（wxss 类名后缀） */
const LEVEL_CLASS: Record<string, string> = { error: 'lv-error', warn: 'lv-warn', info: 'lv-info' };

const PAGE_SIZE = 20;

Page({
  data: {
    chips: LEVEL_CHIPS,
    activeLevel: '',
    startDate: '',
    endDate: '',
    /** 员工下钻（null=全部） */
    filterUserId: 0,
    filterUserName: '',
    pickerVisible: false,
    items: [] as LogView[],
    page: 1,
    hasMore: false,
    loading: false,
    /** 首屏失败（可重试） */
    error: false,
    /** 失败原因（排查用，正常为空） */
    errMsg: ''
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
    const { activeLevel, startDate, endDate, filterUserId, page, items } = this.data;
    this.setData({ loading: true, error: false });
    return fetchClientLogs({
      level: activeLevel || undefined,
      date_from: startDate || undefined,
      date_to: endDate || undefined,
      user_id: filterUserId || undefined,
      page: reset ? 1 : page,
      page_size: PAGE_SIZE
    })
      .then((res) => {
        const rows: LogView[] = res.results.map((it) => ({
          ...it,
          timeText: fmtDateTime(it.created_at),
          expanded: false,
          lvClass: LEVEL_CLASS[it.level] || 'lv-info'
        }));
        this.setData({
          items: reset ? rows : items.concat(rows),
          page: reset ? 1 : page + 1,
          hasMore: !!res.next,
          loading: false
        });
      })
      .catch((e) => {
        this.setData({ loading: false, error: !items.length, errMsg: String((e && e.message) || e).slice(0, 120) });
      });
  },

  onLevelTap(e: WechatMiniprogram.TouchEvent) {
    const key = (e.currentTarget.dataset.key as string) || '';
    if (key === this.data.activeLevel) return;
    this.setData({ activeLevel: key });
    this.reload();
  },

  onRangeChange(e: WechatMiniprogram.CustomEvent<{ start: string; end: string; quick: string }>) {
    this.setData({ startDate: e.detail.start, endDate: e.detail.end });
    this.reload();
  },

  // ---- 员工下钻 ----

  onOpenPicker() {
    this.setData({ pickerVisible: true });
  },

  onClosePicker() {
    this.setData({ pickerVisible: false });
  },

  onPickerConfirm(e: WechatMiniprogram.CustomEvent<{ users: Array<{ id: number; real_name?: string; username?: string }>; ids: number[] }>) {
    const user = e.detail.users && e.detail.users[0];
    this.setData({
      pickerVisible: false,
      filterUserId: user ? user.id : 0,
      filterUserName: user ? (user.real_name || user.username || `员工${user.id}`) : ''
    });
    this.reload();
  },

  onClearUser() {
    if (!this.data.filterUserId) return;
    this.setData({ filterUserId: 0, filterUserName: '' });
    this.reload();
  },

  // ---- 列表交互 ----

  onToggleDetail(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const items = this.data.items.slice();
    if (items[idx]) {
      items[idx] = { ...items[idx], expanded: !items[idx].expanded };
      this.setData({ items });
    }
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
