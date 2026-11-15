/**
 * 报价管理接口（quotation 域，P2）。
 * 契约唯一真源：backend/contract_check.py 7 条 quotation 路由 + apps/quotation/
 * serializers.py / views.py / services.py（2026-10-24 逐条核对）。权限：
 * GET 类挂 quotation.view；建/改/删/流转挂 quotation.manage（两点 legacy=False）。
 *
 * 口径（后端注释为准）：
 *  - 金额字段为字符串（DRF 小数原样），前端展示直接透传，不做浮点累加；
 *  - 算定字段（material_total/labor_total/total_price/profit_rate）服务端重算，
 *    前端不得自行汇总覆盖；
 *  - 倍率 rate 常规区间 1.8-3.0 默认 2.3，超界 rate_warning=true 且
 *    rate_warning_msg 直显（提示不拦截不改值，D2）；
 *  - 小单溢价：qty<300 默认 +30%（small_lot_addon），addon_amount=上机费/制版费
 *    等附加费单独项；
 *  - won/lost 为终态（mark-status 非法流转 400，won 可带 order_id 关联订单）；
 *  - DELETE 为软删。
 */
import { http } from '../utils/request';
import { Paginated } from './types';

// ============ 类型（与 apps/quotation/serializers.py 字段一一对应） ============

export type QuoteStatus = 'draft' | 'sent' | 'won' | 'lost';

/** GET /quotation/quotations 列表行（扁平不嵌套） */
export interface QuotationListItem {
  id: number;
  quote_no: string;
  customer: number | null;
  customer_name: string;
  style: number;
  style_no: string;
  style_name: string;
  qty: number;
  /** YYYY-MM-DD */
  quote_date: string;
  material_total: string;
  total_price: string;
  /** %（total=0 时 null） */
  profit_rate: string | null;
  status: QuoteStatus;
  status_display: string;
  order: number | null;
  order_no: string;
  remark: string;
  created_at: string;
}

/** 报价用料行（读）：amount=单耗×单价（计算属性随行下发，字符串） */
export interface QuoteMaterialRow {
  id: number;
  material: number;
  material_code: string;
  material_name: string;
  material_unit: string;
  bom_item: number | null;
  bom_item_id: number | null;
  quote_unit_usage: string;
  price: string;
  amount: string;
}

/** 报价人工行（读）：rate_warning 随行下发（超界只提示不拦截，D2） */
export interface QuoteLabor {
  id: number;
  labor_base: string;
  rate: string;
  rate_warning: boolean;
  rate_warning_msg: string;
  small_lot_addon: string;
  addon_rule: { threshold: number; addon_pct: number; auto_applied: boolean };
  addon_amount: string;
  labor_total: string;
}

/** GET /quotation/quotations/{id} 详情（嵌套用料行与人工行） */
export interface QuotationDetail extends QuotationListItem {
  materials: QuoteMaterialRow[];
  labor: QuoteLabor;
}

/** 用料行提交格式：material 与 bom_item 二选一（都传须同物料，后端校验） */
export interface QuoteMaterialPayload {
  material?: number;
  bom_item?: number;
  quote_unit_usage: string | number;
  price: string | number;
}

/** 人工行提交格式：labor_base 缺省=款式工序工价快照；rate 缺省 2.3；
 * small_lot_addon 缺省按 qty 规则（<300 → +30%）；addon_amount 缺省 0 */
export interface QuoteLaborPayload {
  labor_base?: string | number;
  rate?: string | number;
  small_lot_addon?: string | number;
  addon_rule?: Record<string, unknown>;
  addon_amount?: string | number;
}

export interface QuotationPayload {
  style: number;
  customer?: number | null;
  qty: number;
  quote_date?: string;
  remark?: string;
  materials?: QuoteMaterialPayload[];
  labor?: QuoteLaborPayload;
}

// ============ 回填对比（D3 软提醒只读） ============

/** 对比单项：diff=实际−假设；diff_rate=diff/假设×100（假设 0 时 null） */
export interface BackfillDiffItem {
  assumed: string;
  actual: string;
  diff: string;
  diff_rate: string | null;
}

/** GET /quotation/quotations/{id}/backfill-compare：纯只读不拦截不写数 */
export interface BackfillCompare {
  quotation: {
    id: number;
    quote_no: string;
    status: QuoteStatus;
    qty: number;
    quote_date: string;
    total_price: string;
    profit_rate: string | null;
  };
  order: { id: number; order_no: string; status: string } | null;
  /** 报价假设口径（工价人工=base 不加倍率；附加费按过手成本） */
  assumed: {
    material: string;
    labor_base: string;
    rate: string;
    small_lot_addon: string;
    addon_amount: string;
    labor_total: string;
    cost_assumed: string;
    total_price: string;
  };
  actual: {
    material_cost: string;
    labor_cost: string;
    outsourcing_cost: string;
    overhead_cost: string;
    total_cost: string;
    revenue: string;
    status: string;
    computed_at: string | null;
    is_stale: boolean;
  } | null;
  diff: {
    material: BackfillDiffItem;
    labor: BackfillDiffItem;
    total: BackfillDiffItem;
    revenue: BackfillDiffItem;
  } | null;
  /** 软提醒 message 横幅（未关联订单/未重算成本/数据过期），不拦截 */
  message: string;
}

// ============ 列表（quotation.view） ============

/** GET /quotation/quotations?status=&customer_id=&style_id=&keyword= */
export function fetchQuotations(params?: {
  status?: string; customer_id?: number; style_id?: number; keyword?: string;
  page?: number; page_size?: number;
}): Promise<Paginated<QuotationListItem>> {
  return http.get<Paginated<QuotationListItem>>('/quotation/quotations',
    (params || {}) as Record<string, unknown>);
}

/** GET /quotation/quotations/{id} 详情 */
export function fetchQuotation(id: number): Promise<QuotationDetail> {
  return http.get<QuotationDetail>(`/quotation/quotations/${id}`);
}

// ============ 建/改/删（quotation.manage） ============

/** POST /quotation/quotations：quote_no 自动号（Q+日期+3位）；materials/labor
 * 缺省自动带出；算定字段服务端重算。返回详情。 */
export function createQuotation(body: QuotationPayload): Promise<QuotationDetail> {
  return http.post<QuotationDetail>('/quotation/quotations',
    body as unknown as Record<string, unknown>);
}

/** PUT /quotation/quotations/{id}：materials 传则全量替换（不传保持原明细）；
 * labor 传则覆盖（labor_base 传 null 即重带快照）。返回详情。 */
export function updateQuotation(id: number, body: Partial<QuotationPayload>): Promise<QuotationDetail> {
  return http.put<QuotationDetail>(`/quotation/quotations/${id}`,
    body as unknown as Record<string, unknown>);
}

/** DELETE /quotation/quotations/{id}（软删） */
export function deleteQuotation(id: number): Promise<unknown> {
  return http.del(`/quotation/quotations/${id}`);
}

// ============ 流转 / 回填对比 ============

/** POST /quotation/quotations/{id}/mark-status：sent/won/lost；
 * won/lost 为终态；won 可带 order_id（须本公司未删订单） */
export function markQuotationStatus(id: number, body: {
  status: 'sent' | 'won' | 'lost';
  order_id?: number;
}): Promise<QuotationDetail> {
  return http.post<QuotationDetail>(`/quotation/quotations/${id}/mark-status`,
    body as unknown as Record<string, unknown>);
}

/** GET /quotation/quotations/{id}/backfill-compare（quotation.view，软提醒只读） */
export function fetchBackfillCompare(id: number): Promise<BackfillCompare> {
  return http.get<BackfillCompare>(`/quotation/quotations/${id}/backfill-compare`);
}
