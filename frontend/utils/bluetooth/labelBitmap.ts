/**
 * 标签位图渲染（2026-10-26）：蓝牙打印从 TSPL TEXT 指令改为整签位图。
 * 动机：TSPL 内置 TSS24 字体放大只支持整数倍（1x=3mm / 2x=6mm），没有中间
 * 字号；位图方式字号任意（LABEL_FONT_MULT=1.5 → 4.5mm 字高），且预览与实打
 * 所见即所得。布局唯一来源仍是 computeLabelLayout（fontMult=1.5 与预览一致），
 * 离屏 canvas 渲染 → 1 位单色打包（1=黑点，MSB 在前）→ TSPL BITMAP 指令。
 */
import {
  computeLabelLayout, resolveFontMult, LabelStyle, PrintTicketData, PrintCompanyInfo
} from './templates';
import { make as makeQrMatrix } from '../../utils/qrcode';

/** 构建整签位图打印指令（SIZE/DIRECTION/CLS + BITMAP + PRINT）。
 *  离屏 canvas 不可用（低版本基础库）等异常直接抛出，由调用方回退指令路径。 */
export function buildLabelBitmapCommand(
  style: LabelStyle, ticket: PrintTicketData, company?: PrintCompanyInfo
): ArrayBuffer {
  const W = Math.round(style.widthMm * 8);   // 8 点/mm
  const H = Math.round(style.heightMm * 8);
  const layout = computeLabelLayout(style, ticket, company, resolveFontMult(style));
  const canvas = wx.createOffscreenCanvas({ type: '2d', width: W, height: H });
  const ctx = canvas.getContext('2d') as WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D;
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = '#000000';
  ctx.textBaseline = 'top';
  for (const el of layout.elements) {
    if (el.type === 'qr') {
      drawQr(ctx, ticket.qr_code || '', el);
    } else {
      // 与预览 label-preview 同口径：基础字号 24 点 + scale 双向拉伸（字号=24×ymul 点）。
      // 2026-10-29 用户反馈「字体调大没有生效」根因：此处漏设 ctx.font，canvas
      // 默认 10px → 实打字号恒约预览的 5/12（1.5x 设定实出 ~1.9mm 字高），调
      // 字号只在 10px 小基数上按比例微变，肉眼难辨。补设后实打=预览所见即所得。
      ctx.save();
      ctx.translate(el.x, el.y);
      ctx.font = '24px sans-serif';
      ctx.scale(el.xmul || 1, el.ymul || 1);
      ctx.fillText(el.text || '', 0, 24 * 0.12);
      ctx.restore();
    }
  }
  // 打包 1 位单色：每行 W/8 字节（宽为 8 的倍数），MSB 在前。
  // TSPL BITMAP 位极性：0=打黑点、1=不打（与 ESC/POS 的 1=黑相反，@psdk/tspl
  // 源码同口径：先按暗像素置 1 再整体取反）——此处直接按亮像素置 1 打包
  const img = ctx.getImageData(0, 0, W, H).data;
  const rowBytes = W >> 3;
  const bits = new Uint8Array(rowBytes * H);
  for (let y = 0; y < H; y++) {
    const row = y * rowBytes;
    const px = y * W * 4;
    for (let x = 0; x < W; x++) {
      const i = px + x * 4;
      const gray = (img[i] + img[i + 1] + img[i + 2]) / 3;
      if (gray >= 190) {
        bits[row + (x >> 3)] |= 0x80 >> (x & 7);
      }
    }
  }
  // TSPL BITMAP 语法（TSC TSPL2/@psdk/tspl 同口径）：
  // BITMAP x,y,<每行字节数>,<高度(点)>,<mode>,<二进制数据>
  // —— 宽度按字节而非点，高度必填；缺失会让打印机把 PRINT 指令当数据吞掉
  const head = `SIZE ${style.widthMm} mm,${style.heightMm} mm\r\n` +
    'DIRECTION 0\r\n' +   // 正打：孔先出纸的挂签纸，物理=打印坐标（对位诊断签同款）
    'REFERENCE 0,0\r\n' +
    'CLS\r\n' +
    `BITMAP 0,0,${rowBytes},${H},0,`;
  const tail = '\r\nPRINT 1\r\n';
  const out = new Uint8Array(head.length + bits.length + tail.length);
  for (let i = 0; i < head.length; i++) out[i] = head.charCodeAt(i);
  out.set(bits, head.length);
  for (let i = 0; i < tail.length; i++) out[head.length + bits.length + i] = tail.charCodeAt(i);
  return out.buffer;
}

/** 二维码：本地生成矩阵绘制（纠错 M，与打印内容一致），模块大小=qrUnit 点，
 *  与 QRCODE 指令的实打尺寸一致（静区由码周留白承担） */
function drawQr(
  ctx: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D,
  content: string,
  el: { x: number; y: number; w: number; qrUnit?: number }
): void {
  let matrix: boolean[][] = [];
  try {
    matrix = makeQrMatrix(content);
  } catch (e) {
    // 内容超本地生成器容量：画占位框提示，不阻塞打印
    ctx.strokeStyle = '#000000';
    ctx.strokeRect(el.x, el.y, el.w, el.w);
    return;
  }
  const unit = el.qrUnit || 6;
  ctx.fillStyle = '#000000';
  for (let r = 0; r < matrix.length; r++) {
    for (let c = 0; c < matrix.length; c++) {
      if (matrix[r][c]) {
        ctx.fillRect(el.x + c * unit, el.y + r * unit, unit, unit);
      }
    }
  }
}
