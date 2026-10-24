/**
 * 质量追溯接口（正向 / 反向 / 终端）。
 * 契约以后端 apps/warehouse/views.py（Trace* 视图）为准（2026-10-18 全量核对）。
 */
import { http } from '../utils/request';

/** 批次搜索行（含受影响面统计） */
export interface TraceLotRow {
  id: number;
  lot_no: string;
  fabric_name?: string;
  color?: string;
  qty?: string;
  unit?: string;
  batch_date?: string | null;
  production_count: number;
  ticket_count: number;
  qty_total: number;
}

/** 正向追溯：批次 → 受影响床次 */
export interface TraceLotResult {
  lot: {
    id: number; lot_no: string; fabric_name?: string; color?: string;
    qty?: string; unit?: string; batch_date?: string | null; remark?: string;
  };
  productions: Array<{
    id: number; bed_no: string; style_no: string; style_name: string;
    customer_name: string; source_order_no: string; order_type: string;
    total_qty: number; issue_qty: string; ticket_count: number;
  }>;
  impact: { production_count: number; ticket_count: number; qty_total: number };
}

/** 反向追溯：款式+颜色 → 床次 + 批次 */
export interface TraceReverseMatch {
  production_id: number;
  bed_no: string;
  style_no: string;
  style_name: string;
  customer_name: string;
  source_order_no: string;
  order_type: string;
  total_qty: number;
  created_at: string;
  lots: Array<{ id: number; lot_no: string; color: string; qty: string }>;
}

/** 终端追溯：一张菲票的全链路 */
export interface TraceTicketResult {
  ticket: {
    id: number; bed_no: string; bundle_no: string; color: string; size: string;
    qty: number; fabric: string; piece_no: string; qr_code: string;
  };
  production: {
    id: number; bed_no: string; style_no: string; style_name: string;
    customer_name: string; source_order_no: string; order_type: string;
    total_qty: number; fabric: string;
  };
  lots: Array<{
    id: number; lot_no: string; fabric_name: string; color: string;
    qty: string; batch_date: string | null;
  }>;
  records: Array<{
    id: number; process: string; user: string; operator: string;
    qty: number; price: string; registered_at: string;
  }>;
  print: { is_printed: boolean; print_count: number; print_at: string | null };
}

/** 床次视角追溯 */
export interface TraceProductionResult {
  production: {
    id: number; bed_no: string; style_no: string; style_name: string;
    customer_name: string; source_order_no: string; order_type: string;
    total_qty: number; fabric: string;
  };
  lots: Array<{ id: number; lot_no: string; fabric_name: string; color: string; qty: string }>;
  ticket_count: number;
  qty_total: number;
  issues: Array<{ issue_no: string; lot_no: string; qty: string; issue_at: string; status: string }>;
}

/** 批次搜索（追溯入口列表） */
export function searchTraceLots(keyword?: string): Promise<{ results: TraceLotRow[] }> {
  return http.get<{ results: TraceLotRow[] }>('/trace/lot', keyword ? { keyword } : undefined);
}

/** 正向：批次 → 受影响床次/菲票 → 客户/来源单号 */
export function fetchTraceLot(id: number): Promise<TraceLotResult> {
  return http.get<TraceLotResult>(`/trace/lot/${id}`);
}

/** 反向：客户投诉某款某色 → 定位床次 + 批次 */
export function fetchTraceReverse(params: {
  style_no?: string; color?: string; customer_id?: number | string;
  date_from?: string; date_to?: string;
}): Promise<{ matches: TraceReverseMatch[] }> {
  return http.get<{ matches: TraceReverseMatch[] }>('/trace/reverse', params as Record<string, unknown>);
}

/** 终端：菲票全链路（旧票七段码扫码 parse 后按 ticket.id 进本接口） */
export function fetchTraceTicket(id: number): Promise<TraceTicketResult> {
  return http.get<TraceTicketResult>(`/trace/ticket/${id}`);
}

/** 床次视角追溯（床次详情入口） */
export function fetchTraceProduction(id: number): Promise<TraceProductionResult> {
  return http.get<TraceProductionResult>(`/trace/production/${id}`);
}
