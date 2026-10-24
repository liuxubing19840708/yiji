/**
 * 款式详情页（2026-10-21 用户需求：款式管理双击进详情）：
 * 款式图（多图九宫格直显、点开预览）+ 产品视频（封面+点击即播，复用 style-craft）
 * + 工艺说明/工艺图片 + 单价/客户/备注。入口：款式管理列表卡片双击。
 */
import { requireLogin } from '../../../utils/auth';
import { mediaUrl } from '../../../utils/media';
import { money } from '../../../utils/format';
import { fetchStyle } from '../../../services/catalog';
import { fetchSettings } from '../../../services/ops';
import { Style } from '../../../services/types';

Page({
  data: {
    id: 0,
    loading: true,
    error: '',
    styleNo: '',
    name: '',
    /** 款式图多图（补域名后）；点图 wx.previewImage */
    imageUrls: [] as string[],
    priceText: '',
    /** 系统设置「隐藏款式总单价」（§15，与列表页同口径） */
    hideStylePrice: false,
    fabric: '',
    customerName: '',
    /** 传给 style-craft 的款式对象（工艺说明/工艺图/视频） */
    styleInfo: null as Record<string, unknown> | null
  },

  onLoad(options: Record<string, string>) {
    this.setData({ id: Number(options.id) || 0 });
  },

  onShow() {
    if (!requireLogin()) return;
    // 隐藏总单价开关进页取一次（与列表页同口径）
    fetchSettings()
      .then((s) => this.setData({ hideStylePrice: s.hide_style_total_price === '1' }))
      .catch(() => {/* 设置读失败不阻塞详情 */});
    this.load();
  },

  onPullDownRefresh() {
    this.load();
    wx.stopPullDownRefresh();
  },

  load() {
    if (!this.data.id) {
      this.setData({ loading: false, error: '缺少款式参数' });
      return;
    }
    this.setData({ loading: true, error: '' });
    fetchStyle(this.data.id)
      .then((s: Style) => {
        const raw = s as unknown as Record<string, unknown>;
        const images = Array.isArray(raw.images) && (raw.images as unknown[]).length
          ? (raw.images as unknown[]).map(String)
          : (String(raw.image || '') ? [String(raw.image || '')] : []);
        wx.setNavigationBarTitle({ title: `${raw.style_no || ''} ${raw.name || ''}`.trim() || '款式详情' });
        this.setData({
          loading: false,
          styleNo: String(raw.style_no || ''),
          name: String(raw.name || ''),
          imageUrls: images.map((u) => mediaUrl(u)),
          priceText: money(String(raw.price || '0')),
          fabric: String(raw.fabric || ''),
          customerName: String(raw.customer_name || ''),
          styleInfo: {
            style_no: raw.style_no || '',
            name: raw.name || '',
            style_image: images[0] || '',
            images,
            craft_note: raw.craft_note || '',
            craft_images: Array.isArray(raw.craft_images) ? raw.craft_images : [],
            video: raw.video || ''
          }
        });
      })
      .catch(() => this.setData({ loading: false, error: '款式信息加载失败，请重试' }));
  },

  onPreviewImage(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    if (!this.data.imageUrls.length) return;
    wx.previewImage({
      current: this.data.imageUrls[idx] || this.data.imageUrls[0],
      urls: this.data.imageUrls
    });
  }
});
