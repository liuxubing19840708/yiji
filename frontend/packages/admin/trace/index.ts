/**
 * 质量追溯查询入口（P0，2026-10-18）：
 * - 扫码追溯：扫任意菲票（旧七段码兼容）→ parse 命中菲票 → 终端全链路页；
 * - 反向追溯：客户投诉某款某色 → 定位床次 + 批次；
 * - 批次追溯：批次号/面料/颜色关键字 → 批次受影响面（正向）。
 * 支持带参进入：?production_id=xx（床次详情「查看追溯」直达床次视角）。
 */
import { requireLogin } from '../../../utils/auth';
import { parseScan } from '../../../services/production';
import {
  fetchTraceReverse, searchTraceLots,
  TraceReverseMatch, TraceLotRow
} from '../../../services/traceability';

Page({
  data: {
    mode: 'reverse' as 'reverse' | 'lot',
    styleNo: '',
    color: '',
    searching: false,
    searched: false,
    matches: [] as TraceReverseMatch[],
    lotKeyword: '',
    lots: [] as TraceLotRow[]
  },

  onLoad(options: Record<string, string | undefined>) {
    // 床次详情「查看追溯」直达床次视角
    if (options.production_id) {
      wx.navigateTo({
        url: `/packages/admin/trace-detail/index?mode=production&id=${options.production_id}`
      });
    }
  },

  onShow() {
    requireLogin();
  },

  // ============ 扫码追溯（旧票七段码兼容） ============

  onScan() {
    wx.scanCode({
      onlyFromCamera: false,
      success: (res) => {
        const qr = (res.result || '').trim();
        if (!qr) return;
        wx.showLoading({ title: '解析中', mask: true });
        parseScan(qr)
          .then((raw) => {
            wx.hideLoading();
            const ticket = (raw as unknown as { ticket?: { id?: number } }).ticket;
            if (!ticket || !ticket.id) {
              wx.showToast({ title: '未解析到菲票', icon: 'none' });
              return;
            }
            wx.navigateTo({
              url: `/packages/admin/trace-detail/index?mode=ticket&id=${ticket.id}`
            });
          })
          .catch(() => wx.hideLoading()); // 解析失败请求层已 toast
      },
      fail: () => { /* 用户取消 */ }
    });
  },

  // ============ 反向追溯（款式+颜色） ============

  onStyleNo(e: WechatMiniprogram.Input) {
    this.setData({ styleNo: e.detail.value });
  },

  onColor(e: WechatMiniprogram.Input) {
    this.setData({ color: e.detail.value });
  },

  onReverseSearch() {
    if (this.data.searching) return;
    const styleNo = this.data.styleNo.trim();
    const color = this.data.color.trim();
    if (!styleNo && !color) {
      wx.showToast({ title: '请填款号或颜色', icon: 'none' });
      return;
    }
    this.setData({ searching: true });
    fetchTraceReverse({ style_no: styleNo, color })
      .then((res) => this.setData({
        searching: false, searched: true, matches: res.matches || []
      }))
      .catch(() => this.setData({ searching: false, searched: true }));

  },

  onTapMatch(e: WechatMiniprogram.TouchEvent) {
    const m = this.data.matches[Number(e.currentTarget.dataset.index)];
    if (!m) return;
    wx.navigateTo({
      url: `/packages/admin/trace-detail/index?mode=production&id=${m.production_id}`
    });
  },

  // ============ 批次追溯（正向） ============

  onMode(e: WechatMiniprogram.TouchEvent) {
    const mode = String(e.currentTarget.dataset.mode) as 'reverse' | 'lot';
    if (mode !== this.data.mode) this.setData({ mode, searched: false, matches: [], lots: [] });
  },

  onLotKeyword(e: WechatMiniprogram.Input) {
    this.setData({ lotKeyword: e.detail.value });
  },

  onLotSearch() {
    if (this.data.searching) return;
    this.setData({ searching: true });
    searchTraceLots(this.data.lotKeyword.trim() || undefined)
      .then((res) => this.setData({ searching: false, searched: true, lots: res.results || [] }))
      .catch(() => this.setData({ searching: false, searched: true }));
  },

  onTapLot(e: WechatMiniprogram.TouchEvent) {
    const lot = this.data.lots[Number(e.currentTarget.dataset.index)];
    if (!lot) return;
    wx.navigateTo({
      url: `/packages/admin/trace-detail/index?mode=lot&id=${lot.id}`
    });
  }
});
