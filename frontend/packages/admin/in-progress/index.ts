/**
 * 我的登记：
 * - 本人今日登记：fetchWorkbench 的 my_today（后端按登录人过滤）。
 * - 当日且未结算可撤销（deleteRecord）；床次进度由生产管理和生产看板承载。
 */
import { requireLogin, getUser } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { fetchWorkbench, deleteRecord } from '../../../services/production';
import { RecordItem } from '../../../services/types';
import { today, fmtDateTime } from '../../../utils/format';

type RecordRow = RecordItem & {
  bed_no?: string;
  bundle_no?: string;
  style_no?: string;
  style_name?: string;
  process_name?: string;
  user_name?: string;
  amount?: string;
  /** 视图字段：是否显示撤销按钮（本人当日/管理员当月，且未结算） */
  canRevoke?: boolean;
  /** 视图字段：登记时间展示文案 */
  timeText?: string;
};

Page({
  data: {
    loading: true,
    loadError: false,
    records: [] as RecordRow[],
    /** 撤销请求进行中的记录 id */
    revokingId: 0
  },

  onShow() {
    if (!requireLogin()) return;
    this.load();
  },

  onPullDownRefresh() {
    this.load().then(() => wx.stopPullDownRefresh());
  },

  async load() {
    this.setData({ loading: true, loadError: false });
    try {
      const wbRes = await fetchWorkbench({}) as unknown as { my_today?: RecordRow[] };
      const uid = (getUser() || { id: -1 }).id;
      const records = ((wbRes.my_today || []) as RecordRow[]).map((r) => ({
        ...r,
        canRevoke: this.checkRevoke(r, uid),
        timeText: fmtDateTime(r.registered_at)
      }));
      this.setData({
        records,
        loading: false
      });
    } catch (e) {
      this.setData({ loading: false, loadError: true });
    }
  },

  onRetry() {
    this.load();
  },

  /**
   * 可撤销：本人当日 + 未结算（管理员当月内撤销他人需 records.revoke 点，后端为最终校验）。
   * 权限点体系二期：撤销他人从 isAdmin 收窄为 records.revoke 点。
   */
  checkRevoke(r: RecordRow, uid: number): boolean {
    if (r.settlement) return false;
    const regDate = (r.registered_at || '').slice(0, 10);
    if (r.user === uid) {
      return regDate === today();
    }
    const app = getApp<IAppOption>();
    if (!app.globalData.isAdmin) return false;
    if (!hasPerm('records.revoke')) return false;
    return regDate.slice(0, 7) === today().slice(0, 7);
  },

  onRevoke(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    if (!id || this.data.revokingId) return;
    wx.showModal({
      title: '撤销登记',
      content: '撤销后该笔登记的工资将不再计入，确认撤销？',
      confirmColor: '#F53F3F',
      success: (res) => {
        if (res.confirm) this.doRevoke(id);
      }
    });
  },

  async doRevoke(id: number) {
    this.setData({ revokingId: id });
    try {
      await deleteRecord(id);
      wx.showToast({ title: '已撤销', icon: 'success' });
      this.load();
    } catch (e) {
      // 已结算/超期等原因：请求层已 toast
    } finally {
      this.setData({ revokingId: 0 });
    }
  }
});
