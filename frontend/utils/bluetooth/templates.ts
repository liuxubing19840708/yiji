/**
 * 菲票标签指令模板（规格 §6.2 末段 / §7.2）。
 *
 * 只支持 TSPL2（2026-10-18 拍板下线 ESC/POS 小票机方案）：
 * 佳博/芯烨/TSC/汉印标签机统一走 buildLabelCommand，
 * 坐标唯一来源 = computeLabelLayout（纸张尺寸用户设、二维码竖版居中置顶/横版居左），
 * 预览组件与打印指令共用同一套坐标（所见即所得）。
 *
 * 中文编码说明（2026-10-17 真机修：打印字形不是简体中文）：
 *  - TSPL 简体内置字体 TSS24.BF2 走 GBK 双字节内码，`CODEPAGE 936` + GBK 编码字节
 *    才出正确简体字形；此前 65001(UTF-8) 在芯烨/佳博常见固件上中文乱码。
 *  - 小程序无原生 GBK 编码器，引入 libs/gbk.min.js（gbk.js 0.3.0，32KB，
 *    不可映射字符按 '?' 出字节）；encodeGbk 输出 ArrayBuffer 供蓝牙写入。
 */
import GBK = require('../../libs/gbk.min');

/** 打印所需的最小菲票字段（与 Ticket 序列化字段名一致） */
export interface PrintTicketData {
  id: number;
  qr_code: string;
  bed_no: string;
  bundle_no: string;
  color?: string;
  size?: string;
  qty: number;
  style_no?: string;
  style_name?: string;
  /** 质量追溯（2026-10-18 P0）：来源单号/面料批次号，空则该行不打印 */
  source_order_no?: string;
  lot_no?: string;
}

export interface PrintCompanyInfo {
  /** 公司名（菲票底部一行） */
  name?: string;
}

/** TSPL 指令行结束必须 CRLF */
const CRLF = '\r\n';

/** TSPL 内容里的 " 与 \ 需转义 */
function tsplEscape(text: string): string {
  return text.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

/**
 * 对位诊断签（真机排查"内容压两张标签接缝"用）：
 * 四周全框 + 每 10mm 一条带数字的黑横条 + 底部 END 标记，一次打 2 张。
 * 从成签可读出：打印起点相对标签真实起点的偏移量/方向（哪头边框被切、
 * 0mm 条离边缘多远）、以及两张之间是否存在累积漂移（纸节距不符特征）。
 */
export function buildDiagCommand(style: LabelStyle): string {
  const W = style.widthMm * 8;
  const H = style.heightMm * 8;
  const lines: string[] = [
    `SIZE ${style.widthMm} mm,${style.heightMm} mm`,
    // DIRECTION 0 正打（2026-10-26 真机定案）：1 是镜像打印，是把文字打倒、
    // 内容跨签分裂的根源——孔先出纸的挂签纸物理坐标=打印坐标，无需镜像
    'DIRECTION 0',
    'REFERENCE 0,0',
    'CODEPAGE 936',
    'CLS',
    `BOX 0,0,${W - 1},${H - 1},2`
  ];
  for (let mm = 0; mm <= 70; mm += 10) {
    const y = mm * 8;
    lines.push(`BAR 4,${y},${W - 8},8`);
    lines.push(`TEXT ${(W / 2) - 24},${y + 14},"TSS24.BF2",0,1,1,"${mm}mm"`);
  }
  lines.push(`TEXT 4,${H - 36},"TSS24.BF2",0,1,1,"END${style.heightMm}"`);
  lines.push('PRINT 1,2');
  return lines.join(CRLF) + CRLF;
}

// ============ 模板定制（多预设 + 自定义字段，2026-10-17） ============

/** 可选打印字段（二维码恒打，不在此列——扫码登记的功能核心） */
export type FieldKey =
  | 'styleNo' | 'styleName' | 'bedNo' | 'bundleNo'
  | 'color' | 'size' | 'qty' | 'company' | 'date'
  | 'sourceOrderNo' | 'lotNo';

export const PRINT_FIELDS: Array<{ key: FieldKey; label: string }> = [
  { key: 'styleNo', label: '款号' },
  { key: 'styleName', label: '款名' },
  { key: 'bedNo', label: '床次' },
  { key: 'bundleNo', label: '扎号' },
  { key: 'color', label: '颜色' },
  { key: 'size', label: '尺码' },
  { key: 'qty', label: '数量' },
  { key: 'sourceOrderNo', label: '来源单号' },
  { key: 'lotNo', label: '批次' },
  { key: 'company', label: '公司名' },
  { key: 'date', label: '打印日期' }
];

/** 标签样式配置（存公司设置 print_label_style，全公司共用）。
 *  不再设「版式」：用户只设纸张尺寸/码大小/字段等，二维码位置固定居中（竖版）。 */
export interface LabelStyle {
  preset: 'classic' | 'bigQty' | 'detail' | 'hangtag' | 'custom';
  /** 纸张尺寸（毫米） */
  widthMm: number;
  heightMm: number;
  gapMm: number;
  /** 二维码单元点数：3 小 / 4 中 / 6 大 */
  qrUnit: number;
  /** 自定义字段（按渲染顺序） */
  fields: FieldKey[];
  /** 数量放大一倍显示 */
  bigQty: boolean;
  /** 上下微调（毫米，正=内容向下移）：打印机间隙传感器有固定偏差时人工对冲（跟打印机走） */
  offsetMm: number;
  /** 二维码位置（仅竖版纸张生效）：top=码上字下（默认）；
   *  bottom=码沉底+文字两列在上（孔先出纸用：孔区在纸头，码压底永不入孔区/边界） */
  qrPosition: 'top' | 'bottom';
  /** 字号倍率（位图打印）：1=3mm 字高起步，1.5=4.5mm（默认）；TSPL 指令路径不受影响 */
  fontMult?: number;
}

/** 默认样式 = 50×80 挂签（竖版码上字下）；已存过样式的公司不受影响 */
export const DEFAULT_LABEL_STYLE: LabelStyle = {
  preset: 'custom', widthMm: 50, heightMm: 80, gapMm: 2, qrUnit: 6,
  fields: ['styleNo', 'bedNo', 'bundleNo', 'color', 'size', 'qty', 'company'],
  bigQty: true, offsetMm: 0, qrPosition: 'top'
};

/** 二维码模块数估算（字节模式 v3~v5：内容 20~40 字节常见落在 29~37 模块） */
function qrModules(qr: string): number {
  let bytes = 0;
  for (let i = 0; i < qr.length; i++) {
    bytes += qr.charCodeAt(i) > 255 ? 3 : 1; // UTF-8 中文 3 字节
  }
  if (bytes <= 32) return 29;
  if (bytes <= 48) return 33;
  return 37;
}

/** 字段渲染文本（无值字段跳过；labeled=两列模式下补字段名前缀，
 *  裸值「红色」「M」各自占一格时分不清归属） */
function fieldText(key: FieldKey, ticket: PrintTicketData, company?: PrintCompanyInfo, labeled = false): string {
  switch (key) {
    case 'styleNo': return ticket.style_no ? `款号:${ticket.style_no}` : '';
    case 'styleName': return ticket.style_name || '';
    case 'bedNo': return ticket.bed_no ? `床次:${ticket.bed_no}` : '';
    case 'bundleNo': return ticket.bundle_no ? `扎:${ticket.bundle_no}` : '';
    case 'color': return ticket.color ? (labeled ? `颜色:${ticket.color}` : ticket.color) : '';
    case 'size': return ticket.size ? (labeled ? `尺码:${ticket.size}` : ticket.size) : '';
    case 'qty': return labeled ? `数量:${ticket.qty}` : `x${ticket.qty}`;
    case 'sourceOrderNo': return ticket.source_order_no ? `单号:${ticket.source_order_no}` : '';
    case 'lotNo': return ticket.lot_no ? `批:${ticket.lot_no}` : '';
    case 'company': return (company && company.name) || '';
    case 'date': {
      const d = new Date();
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }
    default: return '';
  }
}

/** 按当前样式构建 TSPL 菲票指令（布局唯一来源 = computeLabelLayout） */
export function buildLabelCommand(style: LabelStyle, ticket: PrintTicketData, company?: PrintCompanyInfo): string {
  return layoutToTspl(style, computeLabelLayout(style, ticket, company), ticket.qr_code || '');
}

// ============ 标签布局（打印指令与屏幕预览共用同一套坐标，2026-10-17） ============

/** 标签元素：坐标/尺寸均为 TSPL 点（8 点/mm），y 已含 offsetMm 平移（与实打一致） */
export interface LabelElement {
  type: 'qr' | 'text';
  x: number;
  y: number;
  /** 占位宽（点）：qr=码宽估算；text=逐字符估宽 */
  w: number;
  /** 占位高（点）：qr=码宽估算；text=24×ymul */
  h: number;
  /** type=text 的渲染文本（原始未转义） */
  text?: string;
  /** TSPL 横/纵向放大（emit 用） */
  xmul?: number;
  ymul?: number;
  /** TSPL 旋转角度（0/180）：码下模式文字整体倒排（持票方向），x/y 为文字框左上角 */
  rot?: number;
  /** type=qr 的单元点数 */
  qrUnit?: number;
}

/** 标签布局：预览按毫米换算绘制，与打印指令逐点一致 */
export interface LabelLayout {
  widthMm: number;
  heightMm: number;
  elements: LabelElement[];
}

/** 估算文本点宽（与排版引擎/打印机 TSS24 半角 12 全角 24 同口径） */
export function textDotWidth(text: string, xmul = 1): number {
  let w = 0;
  for (const ch of text) {
    w += (ch.charCodeAt(0) > 255 ? 24 : 12) * xmul;
  }
  return w;
}

/** 字号倍率：位图打印默认 1.5（24→36 点=4.5mm 字高，比 2x=6mm 小一号）；
 *  TSPL TEXT 指令放大只支持整数倍，指令路径恒 1 */
export const LABEL_FONT_MULT = 1.5;

/** 样式里的字号倍率取值（旧样式无此字段/坏值回落默认；合法域 [1, 3]） */
export function resolveFontMult(style: LabelStyle): number {
  const m = style.fontMult;
  return typeof m === 'number' && m >= 1 && m <= 3 ? m : LABEL_FONT_MULT;
}

/**
 * 布局计算（打印与预览的唯一坐标来源）：
 * 竖长标签二维码居中置顶、文字逐行居中；横条标签码左文右；截断、超高停止渲染。
 * fontMult：字号倍率（1=TSPL 指令路径，整数倍放大；1.5=位图路径，字号任意）。
 */
export function computeLabelLayout(
  style: LabelStyle, ticket: PrintTicketData, company?: PrintCompanyInfo, fontMult = 1
): LabelLayout {
  const W = style.widthMm * 8;   // 8 点/mm
  const H = style.heightMm * 8;
  const modules = qrModules(ticket.qr_code || '');
  const qrBox = modules * style.qrUnit;
  // 上下微调（毫米→点）：直接平移全部内容坐标，不依赖打印机 obey SHIFT/校准
  const off = Math.max(-Math.round(H * 0.9), Math.min(Math.round(H * 0.9), Math.round((style.offsetMm || 0) * 8)));
  const els: LabelElement[] = [];

  // 逐字符截断到 maxW（半角 12 / 全角 24 dot，与打印机 TSS24 同口径）
  const trunc = (text: string, maxW: number, xmul: number): string => {
    let out = '';
    let w = 0;
    for (const ch of text) {
      const cw = (ch.charCodeAt(0) > 255 ? 24 : 12) * xmul;
      if (w + cw > maxW) break;
      out += ch;
      w += cw;
    }
    return out;
  };

  if (style.heightMm > style.widthMm && style.qrPosition === 'bottom') {
    // ===== 竖版·码下模式（孔先出纸：孔区在纸头，码沉底永不入孔区/边界）=====
    // 文字两列排上方（两端对齐：a 靠左 b 靠右，装不下配对独占整行居中），二维码
    // 居中压底；厂名/日期不进两列流：各自整行居中、紧贴码上方（2026-10-26 需求）。
    // 字号 = 24×fontMult 点。TSPL 指令路径（fontMult=1）内置字体只支持整数倍：
    // 先试 2x（大两号）放不下回退 1x；位图路径字号任意（1.5x），一次排成。
    // 二维码：压底（距底边 1mm）+ offsetMm 随动——打印机出纸原点有固有偏移
    // 时（2026-10-26 真机：DIRECTION 0 下约 +10mm），码必须和文字一起平移
    // 才能补偿，压死不动会跨到下一签被孔打穿；极端配置兜底 ≥4
    const qrY = Math.max(4, H - 8 - qrBox + off);
    type Row = { text: string; xmul: number; ymul: number };
    const mainKeys = style.fields.filter(k => k !== 'company' && k !== 'date');
    // 在 maxW 宽度内截断后，于 [x, x+maxW] 区间内水平居中放置
    const putText = (out: LabelElement[], row: Row, x: number, y: number, maxW: number) => {
      const t = trunc(row.text, maxW, row.xmul);
      if (!t) return;
      const w = textDotWidth(t, row.xmul);
      out.push({
        type: 'text', x: Math.max(4, x + Math.round((maxW - w) / 2)), y,
        w, h: 24 * row.ymul, text: t, xmul: row.xmul, ymul: row.ymul
      });
    };
    // 主字段排版：absMult=字号绝对倍率，rowGap=行距，bottom=主字段区底界。
    // 返回是否丢字段（TSPL 路径 2x 放不下据此回退 1x）。
    const layoutMain = (absMult: number, rowGap: number, bottom: number):
      { out: LabelElement[]; dropped: boolean } => {
      const out: LabelElement[] = [];
      const items: Row[] = [];
      for (const key of mainKeys) {
        const text = fieldText(key, ticket, company, true);
        if (!text) continue;
        // bigQty 数量强调只在指令路径 1x 时翻倍（2x/位图再翻倍装不下）
        const big = key === 'qty' && style.bigQty && absMult < 2;
        const m = big ? absMult * 2 : absMult;
        items.push({ text, xmul: m, ymul: m });
      }
      const maxW = W - 24;
      // 文字从孔区下方起排（挂签孔压纸头 ~8-17mm，DIRECTION 0 后物理=打印坐标）
      let y = Math.max(16, 144 + off);
      let i = 0;
      while (i < items.length) {
        const a = items[i];
        // 宽度按未截断全文估：截断后再测永远 ≤ 上限，宽字段分支会失效
        const aw = textDotWidth(a.text, a.xmul);
        const b = items[i + 1];
        const bw = b ? textDotWidth(b.text, b.xmul) : 0;
        const rowH = Math.max(24 * a.ymul, b ? 24 * b.ymul : 0);
        if (y + rowH > bottom) return { out, dropped: true };
        if (b && aw + bw + 16 <= maxW) {
          // 两端对齐一行：a 靠左、b 靠右（比定宽两列多装约 1/3 宽度）
          out.push({ type: 'text', x: 12, y, w: aw, h: 24 * a.ymul, text: a.text, xmul: a.xmul, ymul: a.ymul });
          out.push({ type: 'text', x: W - 12 - bw, y, w: bw, h: 24 * b.ymul, text: b.text, xmul: b.xmul, ymul: b.ymul });
          y += rowH + rowGap;
          i += 2;
        } else {
          // 装不下配对（或最后一个）：独占整行居中
          putText(out, a, 12, y, maxW);
          y += 24 * a.ymul + rowGap;
          i++;
        }
      }
      return { out, dropped: false };
    };
    // 主字段区底界按脚注（厂名/日期）字号推算：日期贴码、厂名再上一行、再让 4 点
    const footBottom = (footMult: number) => qrY - 2 - 24 * footMult - 4 - 24 * footMult - 4;
    // TSPL 路径：先试全 2x，丢字段则全回 1x；位图路径按 fontMult 排，
    // 用户调太大装不下时回默认 1.5——宁可字小不静默丢字段
    const try2x = fontMult === 1 ? layoutMain(2, 4, footBottom(2)) : null;
    let mainMult: number;
    let mainOut: LabelElement[];
    if (try2x) {
      mainMult = try2x.dropped ? 1 : 2;
      mainOut = try2x.dropped ? layoutMain(mainMult, 8, footBottom(mainMult)).out : try2x.out;
    } else {
      const t = layoutMain(fontMult, 4, footBottom(fontMult));
      if (t.dropped && fontMult !== LABEL_FONT_MULT && fontMult > LABEL_FONT_MULT) {
        mainMult = LABEL_FONT_MULT;
        mainOut = layoutMain(mainMult, 4, footBottom(mainMult)).out;
      } else {
        mainMult = fontMult;
        mainOut = t.out;
      }
    }
    els.push(...mainOut);
    // 厂名/日期：各自整行居中、挨着码（坐标随 off 平移，与码同进退）
    const footH = 24 * mainMult;
    const dateY = Math.max(4, qrY - 2 - footH);
    const compY = Math.max(4, dateY - 4 - footH);
    const putFoot = (text: string, y: number) => {
      if (text) putText(els, { text, xmul: mainMult, ymul: mainMult }, 12, y, W - 24);
    };
    putFoot(fieldText('company', ticket, company, true), compY);
    putFoot(fieldText('date', ticket, company, true), dateY);
    els.push({
      type: 'qr', x: Math.max(8, Math.round((W - qrBox) / 2)), y: qrY,
      w: qrBox, h: qrBox, qrUnit: style.qrUnit
    });
    return { widthMm: style.widthMm, heightMm: style.heightMm, elements: els };
  }

  // ===== 原有布局：竖版码上字下 / 横版码左文右 =====
  // 竖长标签（如 50×80 挂签）居中置顶、文字逐行居中排下方、行距加大；
  // 横条标签（高≤宽）居中放不下，固定码左文右的紧凑布局
  const portrait = style.heightMm > style.widthMm;
  const qrX = portrait ? Math.max(8, Math.round((W - qrBox) / 2)) : 8;
  const textX = portrait ? 12 : Math.min(W - 60, 8 + qrBox + 12);
  const textMaxW = portrait ? W - 24 : W - 8 - textX;
  const lineGap = portrait ? 20 : 8;

  els.push({ type: 'qr', x: qrX, y: Math.max(0, 14 + off), w: qrBox, h: qrBox, qrUnit: style.qrUnit });
  let y = (portrait ? 14 + qrBox + 16 : 12) + off;
  for (const key of style.fields) {
    const text = fieldText(key, ticket, company);
    if (!text) continue;
    const big = key === 'qty' && style.bigQty;
    const xmul = big ? 2 : 1;
    const ymul = big ? 2 : 1;
    const out = trunc(text, textMaxW, xmul);
    if (!out) continue;
    const w = textDotWidth(out, xmul);
    // 竖长标签逐行水平居中；横条按固定列对齐
    const x = portrait ? Math.max(12, Math.round((W - w) / 2)) : textX;
    els.push({ type: 'text', x, y, w, h: 24 * ymul, text: out, xmul, ymul });
    y += 24 * ymul + lineGap;
    if (y + 24 > H - 8) break; // 超高停止（company/date 排最后会被优先牺牲）
  }
  return { widthMm: style.widthMm, heightMm: style.heightMm, elements: els };
}

/** 布局 → TSPL 指令（逐元素 emit，与实打坐标一致） */
function layoutToTspl(style: LabelStyle, layout: LabelLayout, qrContent: string): string {
  // 指令头不发 GAP/SHIFT：GAP n,m 的 m 参数（间隙传感器→打印头机械距离）与 SHIFT
  // 都会覆盖机器校准存进 flash 的偏移，导致内容固定压两张标签接缝（真机 15mm 之坑）。
  // 信任机器自身的纸张校准结果，残余偏差用 offsetMm 软件平移兜底。
  const lines: string[] = [
    `SIZE ${layout.widthMm} mm,${layout.heightMm} mm`,
    // DIRECTION 0 正打（与对位诊断签一致）：孔先出纸的挂签纸，物理上=打印坐标
    // 正读；此前 DIRECTION 1 镜像是文字倒向+内容跨签分裂（文字压到下一张签、
    // 被孔打穿）的根源。残余整体偏移用 offsetMm 补。
    'DIRECTION 0',
    'REFERENCE 0,0', // 仅坐标原点，不影响走纸
    'CODEPAGE 936',
    'CLS'
  ];
  for (const el of layout.elements) {
    if (el.type === 'qr') {
      lines.push(`QRCODE ${el.x},${el.y},M,${el.qrUnit},A,0,"${tsplEscape(qrContent || '')}"`);
    } else {
      // TSPL 旋转以首字符锚点为基准：180° 时文字从锚点向左上延伸，
      // 故锚点取文字框右下角（x+w, y+h），渲染结果与预览框一致
      const rot = el.rot || 0;
      const tx = rot === 180 ? el.x + el.w : el.x;
      const ty = rot === 180 ? el.y + el.h : el.y;
      lines.push(`TEXT ${tx},${ty},"TSS24.BF2",${rot},${el.xmul || 1},${el.ymul || 1},"${tsplEscape(el.text || '')}"`);
    }
  }
  lines.push('PRINT 1');
  return lines.join(CRLF) + CRLF;
}

/**
 * UTF-8 编码为 ArrayBuffer（小程序部分基础库无 TextEncoder，手写保证兼容）。
 * 仅作 encodeGbk 的回落（GBK 编码器不可用时），正常打印不走此编码。
 */
export function encodeUtf8(str: string): ArrayBuffer {
  const bytes: number[] = [];
  for (let i = 0; i < str.length; i++) {
    let code = str.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff && i + 1 < str.length) {
      const low = str.charCodeAt(i + 1);
      if (low >= 0xdc00 && low <= 0xdfff) {
        code = 0x10000 + ((code - 0xd800) << 10) + (low - 0xdc00);
        i++;
      }
    }
    if (code < 0x80) {
      bytes.push(code);
    } else if (code < 0x800) {
      bytes.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f));
    } else if (code < 0x10000) {
      bytes.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
    } else {
      bytes.push(
        0xf0 | (code >> 18),
        0x80 | ((code >> 12) & 0x3f),
        0x80 | ((code >> 6) & 0x3f),
        0x80 | (code & 0x3f)
      );
    }
  }
  return new Uint8Array(bytes).buffer;
}

/**
 * GBK 编码为 ArrayBuffer（打印指令用：TSS24.BF2 简体字形按 GBK 内码渲染）。
 * GBK 无法映射的字符（生僻字/emoji）按 '?' 出字节；编码器不可用时回落 UTF-8。
 */
export function encodeGbk(str: string): ArrayBuffer {
  try {
    const bytes = GBK.encode(str);
    return new Uint8Array(bytes).buffer;
  } catch (e) {
    return encodeUtf8(str);
  }
}
