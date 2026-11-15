/**
 * 打印预览组件：按当前 LabelStyle 渲染标签实打效果（canvas 2d）。
 * 坐标唯一来源 = utils/bluetooth/templates 的 computeLabelLayout（与打印指令逐点一致，
 * 含 offsetMm 微调/截断/竖版居中），二维码用 utils/qrcode 本地生成矩阵绘制。
 * 用法：<label-preview show tickets label-style company bind:confirm bind:cancel />
 */
import {
  computeLabelLayout, resolveFontMult, LabelLayout, LabelStyle, PrintTicketData, PrintCompanyInfo
} from '../../utils/bluetooth/templates';
import { make as makeQrMatrix } from '../../utils/qrcode';
import { getEffectiveLabelStyle, getPrinter, setSkipPreview } from '../../services/printer';
import * as bt from '../../utils/bluetooth/adapter';

/** 画布显示区最长边（px），按纸张比例换算另一边 */
const CANVAS_MAX = 300;

/** 构建标记：预览弹层右上可见，用于确认真机跑的是哪一版代码（真机排查用，随排版改动更新） */
const BUILD_TAG = 'B1026G';

/** 绘制上下文别名（2d canvas） */
type Ctx = WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D;

/** canvas 2d 绘制上下文（每次渲染现查现用，不跨弹层缓存——
 *  弹层 wx:if 关闭会销毁 canvas 节点，缓存旧节点二次打开会画到已分离的画布上=空白） */
interface RenderCtx {
  ctx: Ctx;
  cssW: number;
  cssH: number;
}

Component({
  properties: {
    show: {
      type: Boolean,
      value: false,
      observer(val: boolean) {
        if (val) (this as unknown as ComponentInstance).render();
      }
    },
    /** 菲票列表（可多张，‹ › 翻页预览）；换一批数据从第 1 张看起 */
    tickets: {
      type: Array,
      value: [] as PrintTicketData[],
      observer() {
        this.setData({ index: 0 });
        if (this.properties.show) (this as unknown as ComponentInstance).render();
      }
    },
    /** 标签样式（缺省取本机当前打印样式） */
    labelStyle: { type: Object, value: undefined as unknown as WechatMiniprogram.IAnyObject },
    company: { type: Object, value: undefined as unknown as WechatMiniprogram.IAnyObject }
  },

  data: {
    canvasCssW: 300,
    canvasCssH: 300,
    index: 0,
    count: 0,
    canPrev: false,
    canNext: false,
    curTicket: {} as PrintTicketData,
    styleLabel: '',
    /** 当前绑定打印机连接状态（确认前可见，避免点确认才知道没连） */
    connName: '',
    connOk: false,
    /** 「以后批量打印不再预览」勾选（仅批量 count>1 展示，确认时写入开关） */
    skipNext: false
  },

  methods: {
    noop() { /* 阻止冒泡 */ },

    /** 当前样式：外部传入优先，否则本机打印样式+当前绑定机器的对位偏移（预览=实打） */
    currentStyle(): LabelStyle {
      const ext = this.properties.labelStyle as unknown as LabelStyle | undefined;
      return ext && ext.preset ? ext : getEffectiveLabelStyle();
    },

    onPrev() {
      if (this.data.index <= 0) return;
      this.setData({ index: this.data.index - 1 });
      (this as unknown as ComponentInstance).render();
    },

    onNext() {
      if (this.data.index >= this.data.count - 1) return;
      this.setData({ index: this.data.index + 1 });
      (this as unknown as ComponentInstance).render();
    },

    onConfirm() {
      // 勾选「不再预览」：写本机开关，批量入口下次直接打印（打印管理可恢复）
      if (this.data.skipNext) setSkipPreview(true);
      this.triggerEvent('confirm', { index: this.data.index });
    },

    onCancel() {
      this.triggerEvent('cancel');
    },

    onSkipChange(e: { detail: { value: Array<string> } }) {
      this.setData({ skipNext: (e.detail.value || []).length > 0 });
    },

    /** 弹层打开/翻页时重绘当前菲票 */
    render() {
      const self = this as unknown as ComponentInstance;
      const tickets = (this.properties.tickets || []) as PrintTicketData[];
      if (!tickets.length) return;
      const idx = Math.min(this.data.index, tickets.length - 1);
      const st = self.currentStyle();
      const company = this.properties.company as unknown as PrintCompanyInfo | undefined;
      // 字号倍率与蓝牙位图打印一致（样式 fontMult），预览=实打所见即所得
      const layout = computeLabelLayout(st, tickets[idx], company, resolveFontMult(st));
      const portrait = layout.heightMm > layout.widthMm;
      const cssW = portrait
        ? Math.round(CANVAS_MAX * layout.widthMm / layout.heightMm)
        : CANVAS_MAX;
      const cssH = portrait
        ? CANVAS_MAX
        : Math.round(CANVAS_MAX * layout.heightMm / layout.widthMm);
      this.setData({
        index: idx,
        count: tickets.length,
        canPrev: idx > 0,
        canNext: idx < tickets.length - 1,
        curTicket: tickets[idx],
        styleLabel: `${layout.widthMm}×${layout.heightMm}mm · ${BUILD_TAG}`,
        canvasCssW: cssW,
        canvasCssH: cssH,
        skipNext: false
      });
      // 连接状态 chip：确认前就知道打印机是否还在线
      const p = getPrinter();
      this.setData({
        connName: p ? p.name : '',
        connOk: !!(p && bt.isConnected(p.deviceId))
      });
      // canvas 尺寸刚变，等一拍再取节点绘制
      wx.nextTick(() => self.draw(layout, cssW, cssH));
    },

    /** 取 canvas 2d 节点（组件作用域查询；wx:if 刚插入时节点可能未就绪，重试几次） */
    ensureCtx(cb: (rc: RenderCtx) => void, attempt = 0) {
      this.createSelectorQuery()
        .select('#lp-canvas')
        .fields({ node: true, size: true })
        .exec((res: Array<{ node: WechatMiniprogram.Canvas; width: number; height: number } | null>) => {
          const info = res && res[0];
          if (!info || !info.node) {
            if (attempt < 4) {
              setTimeout(() => (this as unknown as ComponentInstance).ensureCtx(cb, attempt + 1), 80);
            }
            return;
          }
          const canvas = info.node;
          const dpr = (wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()).pixelRatio || 2;
          canvas.width = info.width * dpr;
          canvas.height = info.height * dpr;
          const ctx = canvas.getContext('2d');
          ctx.scale(dpr, dpr);
          cb({ ctx, cssW: info.width, cssH: info.height });
        });
    },

    /** 按布局逐元素绘制：白底标签纸 → 二维码矩阵 → 文本（点→px 同比例） */
    draw(layout: LabelLayout, cssW: number, cssH: number) {
      (this as unknown as ComponentInstance).ensureCtx(({ ctx }) => {
        const Wd = layout.widthMm * 8;   // 点宽
        const Hd = layout.heightMm * 8;
        const scale = Math.min(cssW / Wd, cssH / Hd); // px/点
        ctx.clearRect(0, 0, cssW, cssH);
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, cssW, cssH);
        const tickets = (this.properties.tickets || []) as PrintTicketData[];
        const ticket = tickets[this.data.index] || ({} as PrintTicketData);
        for (const el of layout.elements) {
          if (el.type === 'qr') {
            (this as unknown as ComponentInstance).drawQr(ctx, ticket.qr_code || '', el, scale);
          } else if (el.rot === 180) {
            // 倒排文字（码下模式）：绕文字框右下角旋转 180，与 TSPL 锚点口径一致
            const fs = 24 * (el.ymul || 1) * scale;
            ctx.fillStyle = '#000000';
            ctx.font = `${fs}px sans-serif`;
            ctx.textBaseline = 'top';
            ctx.save();
            ctx.translate((el.x + el.w) * scale, (el.y + el.h) * scale);
            ctx.rotate(Math.PI);
            ctx.fillText(el.text || '', 0, fs * 0.12);
            ctx.restore();
          } else {
            // 文字：按 TSPL 放大倍数双向拉伸（2x 大两号后与实打同宽同高）
            ctx.save();
            ctx.translate(el.x * scale, el.y * scale);
            ctx.scale(el.xmul || 1, el.ymul || 1);
            ctx.fillStyle = '#000000';
            ctx.font = `${24 * scale}px sans-serif`;
            ctx.textBaseline = 'top';
            ctx.fillText(el.text || '', 0, 24 * scale * 0.12);
            ctx.restore();
          }
        }
      });
    },

    /** 二维码：本地生成矩阵（纠错 M，与打印同内容），静区 2 模块 */
    drawQr(ctx: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D, content: string, el: { x: number; y: number; w: number; h: number }, scale: number) {
      let matrix: boolean[][] = [];
      try {
        matrix = makeQrMatrix(content);
      } catch (e) {
        // 内容超本地生成器容量：画占位框提示，不阻塞预览
        ctx.strokeStyle = '#000000';
        ctx.strokeRect(el.x * scale, el.y * scale, el.w * scale, el.h * scale);
        return;
      }
      const n = matrix.length;
      const box = el.w * scale; // 码区像素宽（与打印 QRCODE 占位一致）
      const quiet = 2;          // 静区（模块）
      const mod = box / (n + quiet * 2);
      const ox = el.x * scale + quiet * mod;
      const oy = el.y * scale + quiet * mod;
      ctx.fillStyle = '#000000';
      for (let r = 0; r < n; r++) {
        for (let c = 0; c < n; c++) {
          if (matrix[r][c]) {
            ctx.fillRect(ox + c * mod, oy + r * mod, mod + 0.5, mod + 0.5);
          }
        }
      }
    }
  }
});

/** 本组件实例（含私有 canvas 缓存与方法别名） */
type ComponentInstance = WechatMiniprogram.Component.Instance<
  { canvasCssW: number; canvasCssH: number; index: number; count: number; canPrev: boolean; canNext: boolean; curTicket: PrintTicketData; styleLabel: string; connName: string; connOk: boolean; skipNext: boolean },
  Record<string, never>,
  {
    currentStyle(): LabelStyle;
    render(): void;
    ensureCtx(cb: (rc: RenderCtx) => void, attempt?: number): void;
    draw(layout: LabelLayout, cssW: number, cssH: number): void;
    drawQr(ctx: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D, content: string, el: { x: number; y: number; w: number; h: number }, scale: number): void;
  }
>;
