/**
 * 工作中（规格 §9.3）：
 * - 进行中床次：fetchProductions({ is_cut_done: false, is_locked: false })，展示登记进度
 *   （P3B beds 合并：映射为 utils/bed-progress.ts 的 BedProgressVM，
 *   交给共用组件 components/bed-progress 渲染，与看板「大屏」页签同一数据形状）
 * - 本人今日登记：fetchWorkbench 的 my_today（后端按登录人过滤），当日且未结算可撤销（deleteRecord）
 */
import { requireLogin, getUser } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { fetchProductions, fetchWorkbench, deleteRecord } from '../../../services/production';
import { toBedProgressVM, BedProgressVM } from '../../../utils/bed-progress';
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
    beds: [] as BedProgressVM[],
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
      const [prodRes, wbRes] = await Promise.all([
        fetchProductions({ is_cut_done: false, is_locked: false }),
        fetchWorkbench({}) as unknown as Promise<{ beds?: unknown[]; my_today?: RecordRow[] }>
      ]);
      // 后端筛选参数为 '0'/'1' 字符串（services 传 boolean 可能被忽略），客户端再兜底过滤；
      // 进度口径（登记/裁货，封顶 100）在 toBedProgressVM 内统一（与看板大屏一致）
      const beds = (prodRes.results || [])
        .filter((p) => !p.is_cut_done && !p.is_locked)
        .map(toBedProgressVM);
      const uid = (getUser() || { id: -1 }).id;
      const records = ((wbRes.my_today || []) as RecordRow[]).map((r) => ({
        ...r,
        canRevoke: this.checkRevoke(r, uid),
        timeText: fmtDateTime(r.registered_at)
      }));
      this.setData({
        beds,
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
