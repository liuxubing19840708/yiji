/**
 * 款式卡 + 工艺信息共用组件（F1 抽公用，规格：订单建单/订单详情/开床向导复用）：
 * - 款号/名称/图（style_image 优先，兜底 style.image）；
 * - 工艺说明（craft_note 文本）+ 工艺图片（craft_images 九宫格，点开 previewImage）；
 * - 产品视频（video 组件，src=mediaUrl(video)，无视频隐藏）。
 * 全部字段后端可能缺省（B1 并行开发中）——缺省块整体隐藏，不白屏。
 */
import { mediaUrl } from '../../utils/media';

interface StyleCraftVM {
  styleNo: string;
  styleName: string;
  imageUrl: string;
  craftNote: string;
  craftImages: string[];
  videoUrl: string;
  /** 三块工艺信息全空时隐藏「工艺」分区标题 */
  hasCraft: boolean;
}

Component({
  properties: {
    /** 款式对象（Style 或 Order 携带的 style 字段，字段可能缺省）。
     *  注：不能叫 style——wxml 里 style 是内联样式保留属性 */
    styleInfo: {
      type: Object,
      value: null as unknown as Record<string, unknown>,
      observer() {
        this.build();
      }
    },
    /** 是否隐藏款号/名称/图头部（只展示工艺信息，如详情页已有款式行时） */
    hideHeader: { type: Boolean, value: false }
  },

  data: {
    vm: null as StyleCraftVM | null,
    /** 点封面才挂 video 组件并 autoplay（原生组件常驻长表单里拖慢整页） */
    videoPlaying: false
  },

  methods: {
    build() {
      const raw = (this.data.styleInfo || {}) as Record<string, unknown>;
      const craftNote = String(raw.craft_note || '').trim();
      const craftImages = (Array.isArray(raw.craft_images) ? raw.craft_images : [])
        .map((u) => mediaUrl(String(u || '')))
        .filter((u) => !!u);
      const video = mediaUrl(String(raw.video || ''));
      const image = mediaUrl(String(raw.style_image || raw.image || ''));
      const vm: StyleCraftVM = {
        styleNo: String(raw.style_no || ''),
        styleName: String(raw.name || raw.style_name || ''),
        imageUrl: image,
        craftNote,
        craftImages,
        videoUrl: video,
        hasCraft: !!craftNote || craftImages.length > 0 || !!video
      };
      this.setData({ vm });
    },

    /** 工艺图片预览（当前张起步，全组可左右滑） */
    onTapCraftImage(e: WechatMiniprogram.TouchEvent) {
      const idx = Number(e.currentTarget.dataset.index) || 0;
      const urls = (this.data.vm && this.data.vm.craftImages) || [];
      if (!urls.length) return;
      wx.previewImage({ current: urls[idx], urls });
    },

    /** 点封面 → 挂载 video 并 autoplay */
    onPlayVideo() {
      this.setData({ videoPlaying: true });
    }
  }
});
