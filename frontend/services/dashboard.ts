/**
 * 生产看板聚合接口（GET /api/dashboard?range=，后端 apps/production/views.py DashboardView）。
 * 类型在本文件内维护（勿改 services/types.ts，避免与并行改动冲突）。
 */
import { http } from '../utils/request';

/** 时间范围 key（与后端 DashboardView.RANGE_KEYS 对应；custom=自定义区间另传起止日期） */
export type DashboardRange = 'today' | 'yesterday' | 'week' | 'month' | 'last_month' | 'custom';

/** 汇总卡：已裁/完工/在制/登记随 range 变；在产床次/裁货总量/订单总量为存量 */
export interface DashboardSummary {
  done_qty: number;      // 范围内完工件数（配满全部工序的菲票 qty 合计）
  working_qty: number;   // 范围内在制件数（已开工未完工的菲票 qty 合计）
  cut_qty: number;       // 范围内已裁件数（新开床次裁货数合计）
  registered_qty: number; // 范围内登记件数（= 完工 + 在制，含各工序累加口径）
  user_count: number;     // 范围内有登记的员工数
  ticket_count: number;   // 范围内经手菲票（扎）数
  active_beds: number;    // 在产床次数（存量）
  cut_total: number;      // 全部床次裁货总件数（存量）
  order_total: number;    // 全部菲票订单总数量（存量）
}

/** 裁床按色归组中的单个床次：该色在该床的菲票数量 */
export interface PlanBedQty {
  bed_no: string;
  qty: number;
}

/** 生产计划表行（行粒度 = 款号+下单日期+颜色，每个款式每个颜色一行；
 *  同一 (款号,下单日期) 的颜色行连续排在一起，仅含 order_date 落在所选区间的款式，
 *  随 range 过滤） */
export interface DashboardPlan {
  style_id: number;
  style_no: string;
  style_name: string;
  order_date: string;   // 下单日期（该款最早床次创建日期 YYYY-MM-DD）
  color: string;        // 颜色（可为空串，前端显示「未分色」）
  order_qty: number;    // 订单数量 = 该款该色的菲票 qty 合计
  beds: PlanBedQty[];   // 床次：该色涉及床次（床号 + 该色菲票数；一床混多色时在各色行都出现）
  cut_qty: number;      // 已裁数量 = 该色涉及床次的裁货合计（混色床整床计入各色行）
  cut_total: number;    // 已裁合计（款号级）= 该款所有颜色行已裁数量之和，同款各行重复显示
}

/** 近 7 天每日登记件数（缺数日为 0；柱状图固定近 7 天不随 range） */
export interface DashboardDaily {
  date: string;    // MM-DD
  weekday: string; // 周一…周日
  qty: number;
}

/** 范围内工序占比 Top 条目 */
export interface DashboardProcess {
  name: string;
  qty: number;
  percent: number;
}

/** 范围内登记排行条目 */
export interface DashboardRank {
  user_id: number;
  name: string;
  qty: number;
  ticket_count: number;
}

/** 范围内组排行条目（按员工组编号聚合；group_no=0 表示未分组成员） */
export interface DashboardGroupRank {
  group_no: number;
  qty: number;
  user_count: number;
  ticket_count: number;
}

/** 看板聚合响应 */
export interface DashboardResp {
  range: DashboardRange;
  summary: DashboardSummary;
  plans: DashboardPlan[];
  daily: DashboardDaily[];
  processes: DashboardProcess[];
  rank: DashboardRank[];
  group_rank: DashboardGroupRank[];
}

/** range：时间范围（today/yesterday/week/month/last_month/custom，默认 month）；
 *  custom 时传 opts.start/end（YYYY-MM-DD 闭区间） */
export function fetchDashboard(
  range: DashboardRange,
  opts?: { start?: string; end?: string }
): Promise<DashboardResp> {
  const params: Record<string, string> = { range };
  if (range === 'custom' && opts) {
    if (opts.start) params.start_date = opts.start;
    if (opts.end) params.end_date = opts.end;
  }
  return http.get<DashboardResp>('/dashboard', params);
}

// ================= 维度查看（2026-10-23 用户需求：按订单/裁床/部门/组/人） =================

/** 看板维度 key（style=按款式走模板渲染接口，其余走 /dashboard/dim） */
export type DashboardDim = 'style' | 'order' | 'bed' | 'post' | 'group' | 'user';

/** 维度表列定义（width=rpx 建议列宽，前端横向滚动渲染） */
export interface DashboardDimColumn {
  key: string;
  title: string;
  width: number;
}

/** 维度表行：key=行主键（订单/床次 id，供下钻），nav=下钻目标（order/bed，空=不可点） */
export interface DashboardDimRow {
  key: string;
  nav: '' | 'order' | 'bed';
  cells: string[];
}

/** 维度聚合响应（GET /api/dashboard/dim?dim=order|bed|post|group|user） */
export interface DashboardDimResp {
  dim: Exclude<DashboardDim, 'style'>;
  range: DashboardRange;
  columns: DashboardDimColumn[];
  rows: DashboardDimRow[];
}

/** 维度查看：range/自定义区间与 fetchDashboard 同口径 */
export function fetchDashboardDim(
  dim: Exclude<DashboardDim, 'style'>,
  range: DashboardRange,
  opts?: { start?: string; end?: string }
): Promise<DashboardDimResp> {
  const params: Record<string, string> = { dim, range };
  if (range === 'custom' && opts) {
    if (opts.start) params.start_date = opts.start;
    if (opts.end) params.end_date = opts.end;
  }
  return http.get<DashboardDimResp>('/dashboard/dim', params);
}
