/**
 * 完成度矩阵共用构造（2026-10-22 用户需求：床次矩阵复用订单矩阵代码，只显示本床次）。
 * 数据源统一为 /records/bundle-progress（BundleProgressItem）；
 * 订单详情（订单矩阵格内扎串 + 按床次叠加小矩阵）与床次详情（本床完成矩阵）共用。
 */
import { BundleProgressItem } from '../services/types';

/** 落后格阈值（§2.5.2 决策：<80% 红底，与订单矩阵同口径） */
export const LOW_THRESHOLD = 80;

/**
 * 单个扎的显示串。
 * - 完整版（床次矩阵）：N床N扎·N件 + 全部参与工人（完工「✓名单」，进行中「（名单）」）；
 * - 简版（订单矩阵，2026-10-22 用户需求：不带数量与对号）：完工扎「名单」，进行中「（名单）」。
 */
export function bundleLabel(t: BundleProgressItem, short = false): string {
  const base = short
    ? `${t.bed_no}床${t.bundle_no}扎`
    : `${t.bed_no}床${t.bundle_no}扎·${Number(t.qty) || 0}件`;
  const names = (t.users || []).map((u) => u.name).filter(Boolean);
  if (short) {
    if (names.length) return t.done ? `${base} ${names.join('/')}` : `${base}（${names.join('/')}）`;
    return base;
  }
  if (t.done) return `${base}✓${names.join('/')}`;
  if (names.length) return `${base}（${names.join('/')}）`;
  return base;
}

/** 扎串列表 → 折叠显示串（>5 个折叠为「等N扎」；仅订单级合并矩阵用，床次矩阵按扎展开不折叠） */
export function foldBundleLabels(labels: string[]): string {
  const shown = labels.slice(0, 5).join('、');
  return labels.length > 5 ? `${shown} 等${labels.length}扎` : shown;
}

export interface MatrixCell {
  /** 裁货数 */
  qty: number;
  /** 已完成（登记数） */
  reg: number;
  /** 余数 = 裁货数 - 已完成 */
  left: number;
}

export interface MatrixTotals {
  qty: number;
  reg: number;
  left: number;
}

/** 渲染就绪的格（空列用 '-' 占位，uk 供 wx:key——多扎尺码重复多列，size 名会撞 key） */
export interface MatrixViewCell {
  uk: string;
  qty: number | string;
  reg: number | string;
  left: number | string;
}

export interface MatrixRow {
  color: string;
  /** 与展开列对齐：每列至多一扎（2026-10-22 用户需求：一色码多扎时尺码占多列、每格一扎） */
  cells: MatrixViewCell[];
  /** 扎行文本（与列对齐：N床N扎·N件+人名，空串占位） */
  bundleLabels: string[];
  /** 行合计（裁货/已完成/余数） */
  total: MatrixTotals;
}

export interface BedMatrix {
  /** 展开后列头（多扎尺码重复多列），uk 供 wx:key */
  cols: Array<{ uk: string; size: string }>;
  rows: MatrixRow[];
  total: MatrixTotals;
}

/**
 * 由菲票完成数据构造单张颜色×尺码矩阵（床次详情本床完成矩阵消费）。
 * 布局参考用户提供的「工序完成明细」样式（2026-10-22）：每颜色三行
 * 裁货数/已完成/余数 + 合计列；某色码多扎时该尺码横向占多列，每列恰一扎
 * （扎行显示 N床N扎·N件+完成人名，裁货/已完成/余数均为该扎单扎数值）。
 */
export function buildBedMatrix(items: BundleProgressItem[]): BedMatrix {
  const colors: string[] = [];
  const sizes: string[] = [];
  items.forEach((t) => {
    if (colors.indexOf(t.color || '') < 0) colors.push(t.color || '');
    if (sizes.indexOf(t.size || '') < 0) sizes.push(t.size || '');
  });
  const byCell = new Map<string, BundleProgressItem[]>();
  items.forEach((t) => {
    const key = `${t.color}|${t.size}`;
    let arr = byCell.get(key);
    if (!arr) { arr = []; byCell.set(key, arr); }
    arr.push(t);
  });
  // 每个尺码的列数 = 全颜色中该尺码最大扎数（至少 1 列，保持共享表头对齐）
  const colCount = new Map<string, number>();
  sizes.forEach((size) => {
    let m = 1;
    colors.forEach((color) => {
      const arr = byCell.get(`${color}|${size}`);
      if (arr && arr.length > m) m = arr.length;
    });
    colCount.set(size, m);
  });
  const cols = sizes.flatMap((size) =>
    Array.from({ length: colCount.get(size) || 1 }, (_, i) => ({ uk: `${size}-${i}`, size })));
  const grand: MatrixTotals = { qty: 0, reg: 0, left: 0 };
  const rows: MatrixRow[] = colors.map((color) => {
    const total: MatrixTotals = { qty: 0, reg: 0, left: 0 };
    const cells: MatrixViewCell[] = [];
    const bundleLabels: string[] = [];
    sizes.forEach((size) => {
      const arr = byCell.get(`${color}|${size}`) || [];
      const n = colCount.get(size) || 1;
      for (let i = 0; i < n; i++) {
        const t = arr[i];
        if (!t) {
          cells.push({ uk: `${color}-${size}-${i}`, qty: '-', reg: '-', left: '-' });
          bundleLabels.push('');
          continue;
        }
        const qty = Number(t.qty) || 0;
        const reg = Number(t.registered_qty) || 0;
        total.qty += qty;
        total.reg += reg;
        total.left += Math.max(0, qty - reg);
        cells.push({ uk: `${color}-${size}-${i}`, qty, reg, left: Math.max(0, qty - reg) });
        bundleLabels.push(bundleLabel(t));
      }
    });
    grand.qty += total.qty;
    grand.reg += total.reg;
    grand.left += total.left;
    return { color: color || '-', cells, bundleLabels, total };
  });
  return { cols, rows, total: grand };
}
