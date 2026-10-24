/**
 * 进行中床次进度卡：统一视图模型 + 映射函数（P3B「beds 合并」）。
 *
 * 背景（重构设计 §4.1）：工作台「进行中床次」（工作中页）与看板「大屏」页签
 * 展示同一类数据，此前两处各自拼视图模型；后端 SA2 已把口径统一进
 * apps/production/stats.py（bed_progress_stats），前端只需收敛渲染——
 * 两处都映射成本文件的 BedProgressVM，交给共用组件 components/bed-progress 渲染。
 * 后端不改（口径已统一），只消费 services 既有字段（types.ts 只读，不改动）。
 */
import { Production } from '../services/types';

/** 床次进度卡统一视图模型（components/bed-progress 的输入形状） */
export interface BedProgressVM {
  id: number;
  bedNo: string;        // 床次号
  styleNo: string;      // 款号
  styleName: string;    // 款名（可空，空不渲染）
  registeredQty: number; // 已登记件数（stats 口径）
  cutTotal: number;      // 裁货总数（cut_total 兜底 total_qty）
  doneCount: number;     // 完工扎数
  ticketCount: number;   // 扎总数
  progress: number;      // 0-100（登记/裁货，封顶 100）
}

/** 映射入参：Production + 后端附加统计字段（types.ts 可选字段，缺省兜底 0） */
export type BedProgressSource = Pick<Production, 'id' | 'bed_no'> &
  Partial<Pick<Production,
    'style_no' | 'style_name' | 'total_qty' | 'cut_total' |
    'registered_qty' | 'done_ticket_count' | 'ticket_count'>>;

/** Production（+stats 附加字段）→ 床次进度卡 VM（字段兜底与原工作台逐位一致） */
export function toBedProgressVM(p: BedProgressSource): BedProgressVM {
  const total = Number(p.cut_total) || Number(p.total_qty) || 0;
  const reg = Number(p.registered_qty) || 0;
  return {
    id: p.id,
    bedNo: p.bed_no || '',
    styleNo: p.style_no || '',
    styleName: p.style_name || '',
    registeredQty: reg,
    cutTotal: total,
    doneCount: Number(p.done_ticket_count) || 0,
    ticketCount: Number(p.ticket_count) || 0,
    progress: total > 0 ? Math.min(Math.round((reg / total) * 100), 100) : 0
  };
}
