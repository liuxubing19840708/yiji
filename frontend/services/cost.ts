/**
 * 订单成本接口（cost 域，P1）：盈亏表 / 重算 / 制费 / 工资对账单。
 * 契约唯一真源：backend/contract_check.py 5 条 cost 路由 + apps/cost/views.py
 * （2026-10-24 逐条核对）。权限：GET 类挂 cost.view；recompute/overhead/batch 挂
 * cost.manage（两点 legacy=False）。
 *
 * 口径（后端 serializers/services 注释为准）：
 *  - 金额字段为字符串（DRF 两位/三位小数原样），展示不自行换算单位；
 *  - revenue=Σ OrderItem.qty×price 为理论收入，与实际收款（折扣/尾数/坏账）
 *    差异属预期，P2 应收核销后以收款实绩复核；
 *  - no_order_cnt=未挂单床次成本数（不入订单，顶部汇总提示）；
 *  - missing_price_cnt=缺价行数（lot 无价/0 价不进成本）——后端暂无下钻端点，
 *    明细需到入库单/批次档补价后重算；
 *  - is_stale=有回填/制费改动后待重算标记（软提醒不拦截，D3）；
 *  - 批量重算 body 二选一：order_ids 数组 或 date_from/date_to（订单创建日期范围）。
 */
import { http } from '../utils/request';
import { Paginated } from './types';

// ============ 类型（与 apps/cost/serializers.py 字段一一对应） ============

export interface OrderCost {
  id: number;
  order: number;
  order_no: string;
  customer_name: string;
  style_no: string;
  style_name: string;
  order_status: string;
  /** 交期 YYYY-MM-DD（可空） */
  delivery_date: string | null;
  material_cost: string;
  labor_cost: string;
  outsourcing_cost: string;
  overhead_cost: string;
  total_cost: string;
  /** 产出件数（成本分母；0 时 unit_cost=null） */
  output_qty: number;
  unit_cost: string | null;
  /** 理论收入 Σqty×price */
  revenue: string;
  /** 未归属成本床次个数（顶部汇总提示「未归属成本床次 N 个」） */
  no_order_cnt: number;
  /** 缺价行数（lot 无价/0 价不进成本） */
  missing_price_cnt: number;
  gross_profit: string;
  /** 毛利率 %（revenue=0 时 null） */
  gross_margin: string | null;
  status: string; // draft | computed
  /** 有改动待重算（软提醒不拦截发货，D3） */
  is_stale: boolean;
  computed_at: string | null;
  computed_by_name: string;
  created_at: string;
}

/** 工资对账单行：key=order_labor/process_wage/whole_wage/commission_wage/fixed_wage */
export interface WageReconciliationRow {
  key: string;
  label: string;
  /** 口径来源说明（逐项列数据源） */
  source: string;
  amount: string;
  /** 与发放总额的差额（amount - paid_total） */
  diff: string;
}

export interface WageReconciliation {
  date_from: string;
  date_to: string;
  /** 期间已结算发放总额 */
  paid_total: string;
  /** 四类工资应发合计 */
  wage_total: string;
  rows: WageReconciliationRow[];
  /** 口径说明（页底展示） */
  notes: string[];
}

// ============ 盈亏表列表（cost.view） ============

/** GET /cost/orders?date_from=&date_to=&keyword=&computed_at_from=
 * 过滤：date_from/date_to=订单创建日期；keyword=订单号/客户/款号/款名；
 * computed_at_from=计算时间（月底一键重算后看增量） */
export function fetchOrderCosts(params?: {
  date_from?: string; date_to?: string; keyword?: string; computed_at_from?: string;
  page?: number; page_size?: number;
}): Promise<Paginated<OrderCost>> {
  return http.get<Paginated<OrderCost>>('/cost/orders', (params || {}) as Record<string, unknown>);
}

// ============ 重算（cost.manage） ============

/** POST /cost/orders/{order_id}/recompute 单订单重算（幂等可重复） */
export function recomputeOrderCost(orderId: number): Promise<OrderCost> {
  return http.post<OrderCost>(`/cost/orders/${orderId}/recompute`);
}

/** POST /cost/orders/recompute-batch 批量重算：order_ids 与日期范围二选一 */
export function recomputeOrderCostsBatch(body: {
  order_ids?: number[];
  date_from?: string; date_to?: string;
}): Promise<{ success: number[]; failed: Array<{ order_id: number; error: string }>; count: number }> {
  return http.post('/cost/orders/recompute-batch', body as unknown as Record<string, unknown>);
}

// ============ 制费（cost.manage） ============

/** PUT /cost/orders/{order_id}/overhead 录制费/管理费（≥0；改动即翻 is_stale） */
export function updateOrderOverhead(orderId: number, overheadCost: string | number): Promise<OrderCost> {
  return http.put<OrderCost>(`/cost/orders/${orderId}/overhead`,
    { overhead_cost: overheadCost } as unknown as Record<string, unknown>);
}

// ============ 工资对账单（cost.view） ============

/** GET /cost/wage-reconciliation?date_from=&date_to=（缺省=本月 1 日至今天） */
export function fetchWageReconciliation(params?: {
  date_from?: string; date_to?: string;
}): Promise<WageReconciliation> {
  return http.get<WageReconciliation>('/cost/wage-reconciliation',
    (params || {}) as Record<string, unknown>);
}

// ============ 订单损益（cost.view，2026-11-02 评审 Top10 #2） ============

/** GET /cost/orders/pnl 行：OrderCost 全字段 + 应收实绩四项（后端聚合注解，
 *  无应收行兜 '0'；real_profit=实收−总成本，负值=垫资中） */
export interface OrderPnlRow extends OrderCost {
  ar_amount: string;
  ar_received: string;
  ar_gap: string;
  real_profit: string;
}

/** GET /cost/orders/pnl?keyword=&date_from=&date_to=&status=（status=订单状态） */
export function fetchOrderPnl(params?: {
  keyword?: string; date_from?: string; date_to?: string; status?: string;
  page?: number; page_size?: number;
}): Promise<Paginated<OrderPnlRow>> {
  return http.get<Paginated<OrderPnlRow>>('/cost/orders/pnl',
    (params || {}) as Record<string, unknown>);
}
