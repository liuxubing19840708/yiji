/**
 * 应收账款接口（finance 域，P2）。
 * 契约唯一真源：backend/contract_check.py 6 条 finance 路由 + apps/finance/
 * serializers.py / views.py / services.py（2026-10-24 逐条核对）。权限：
 * 应收列表挂 finance.view；建应收/核销/撤销/作废/移动加权报表挂 finance.manage
 * （两点 legacy=False）。
 *
 * 口径（后端注释为准）：
 *  - 金额字段为字符串（DRF 两位小数原样），outstanding=amount−received_amount
 *    为计算字段，前端展示透传；
 *  - 超额收款后端 400 硬拦（金额级）——前端提交前预校验 本次+已收≤应收，
 *    超了就地报错不发请求；
 *  - received_amount/status 只由核销/撤销事务内推进，前端不可直改；
 *  - 作废=软删（列表不显），有活动核销须先全部撤销（400）；
 *  - status：unpaid 未收 / partial 部分收款 / settled 已结清；
 *  - method：cash 现款 / monthly 月结 / acceptance 承兑 / other 其他。
 *
 * 契约缺口 A/B 已由 INT 补齐（2026-10-24）：GET /finance/receivables/{id}
 * 详情 + GET /finance/receivables/{id}/receipt-lines 收款流水（均挂
 * finance.view，软删行 404 / 已撤销流水不显）——详情页已改走详情端点，
 * 时间线已接入逐笔流水。
 */
import { http } from '../utils/request';
import { Paginated } from './types';

// ============ 类型（与 apps/finance/serializers.py 字段一一对应） ============

export type ReceivableStatus = 'unpaid' | 'partial' | 'settled';

/** 应收账行（列表 = 详情同序列化器） */
export interface Receivable {
  id: number;
  receivable_no: string;
  customer: number | null;
  customer_name: string;
  order: number | null;
  order_no: string;
  /** P2 出货域预留透传列（手工端一般无值） */
  shipment_id: number | null;
  amount: string;
  received_amount: string;
  /** 未收余额 = amount − received_amount（字符串） */
  outstanding: string;
  status: ReceivableStatus;
  status_display: string;
  remark: string;
  created_at: string;
}

export type ReceiptMethod = 'cash' | 'monthly' | 'acceptance' | 'other';

/** 收款核销流水行（核销/撤销响应内嵌返回；列表走 GET receivables/{id}/receipt-lines） */
export interface ReceiptLine {
  id: number;
  receivable: number;
  amount: string;
  method: ReceiptMethod;
  method_display: string;
  received_at: string;
  handler: string;
  handler_name: string;
  remark: string;
  created_at: string;
}

/** POST /finance/receivables/{id}/receipts 响应：{receivable, receipt} 双块 */
export interface ReceiptCreateResult {
  receivable: Receivable;
  receipt: ReceiptLine;
}

/** POST /finance/receivables/{id}/receipts/revoke 响应 */
export interface ReceiptRevokeResult {
  receivable: Receivable;
  revoked_receipt_id: number;
}

/** POST /finance/receivables/{id}/void 响应 */
export interface ReceivableVoidResult {
  id: number;
  receivable_no: string;
  voided: boolean;
}

// ============ 移动加权库存估值（D4 双口径第二口径，finance.manage） ============

/** 批次明细行（金额/数量为字符串；wavg_price 三位小数） */
export interface StockValLotRow {
  lot_id: number;
  lot_no: string;
  fabric_name: string;
  color: string;
  unit: string;
  in_qty: string;
  in_amt: string;
  issued_qty: string;
  balance_qty: string;
  wavg_price: string;
  balance_amt: string;
  missing_price_rows: number;
}

/** 物料组行（组内批次单位不一致时 unit=''，以批次明细为准） */
export interface StockValRow {
  material_id: number | null;
  material_code: string;
  material_name: string;
  unit: string;
  in_qty: string;
  in_amt: string;
  issued_qty: string;
  balance_qty: string;
  wavg_price: string;
  balance_amt: string;
  missing_price_rows: number;
  lots: StockValLotRow[];
}

/** GET /finance/stock-valuation 响应 */
export interface StockValuation {
  date_to: string | null;
  note: string;
  rows: StockValRow[];
}

// ============ 应收列表 / 建应收 ============

/** GET /finance/receivables?status=&customer_id=&keyword=&date_from=&date_to= */
export function fetchReceivables(params?: {
  status?: string; customer_id?: number; keyword?: string;
  date_from?: string; date_to?: string;
  page?: number; page_size?: number;
}): Promise<Paginated<Receivable>> {
  return http.get<Paginated<Receivable>>('/finance/receivables',
    (params || {}) as Record<string, unknown>);
}

/** POST /finance/receivables 手工建应收（customer/order 归属校验后端做；
 * 单号自动 AR+日期+3 位） */
export function createReceivable(body: {
  customer: number;
  order?: number | null;
  amount: string | number;
  remark?: string;
}): Promise<Receivable> {
  return http.post<Receivable>('/finance/receivables',
    body as unknown as Record<string, unknown>);
}

/** GET /finance/receivables/{id} 详情（INT 补缺口 A；软删/作废 404） */
export function fetchReceivable(id: number): Promise<Receivable> {
  return http.get<Receivable>(`/finance/receivables/${id}`);
}

/** GET /finance/receivables/{id}/receipt-lines 收款流水倒序列表
 * （INT 补缺口 B；已撤销（软删）行不显，撤销留痕走 ops 审计日志） */
export function fetchReceiptLines(receivableId: number): Promise<ReceiptLine[]> {
  return http.get<ReceiptLine[]>(`/finance/receivables/${receivableId}/receipt-lines`);
}

// ============ 核销 / 撤销 / 作废（finance.manage） ============

/** POST /finance/receivables/{id}/receipts 核销（收款）。
 * 前端须预校验 本次+已收≤应收（后端超额 400 硬拦）。 */
export function addReceipt(receivableId: number, body: {
  amount: string | number;
  method?: ReceiptMethod;
  received_at?: string;
  handler?: string;
  remark?: string;
}): Promise<ReceiptCreateResult> {
  return http.post<ReceiptCreateResult>(
    `/finance/receivables/${receivableId}/receipts`,
    body as unknown as Record<string, unknown>);
}

/** POST /finance/receivables/{id}/receipts/revoke 撤销最近一条核销
 * （反向回退 received_amount 并重算 status；无可撤 400） */
export function revokeLastReceipt(receivableId: number): Promise<ReceiptRevokeResult> {
  return http.post<ReceiptRevokeResult>(
    `/finance/receivables/${receivableId}/receipts/revoke`);
}

/** POST /finance/receivables/{id}/void 作废（软删；有活动核销 400） */
export function voidReceivable(receivableId: number): Promise<ReceivableVoidResult> {
  return http.post<ReceivableVoidResult>(`/finance/receivables/${receivableId}/void`);
}

// ============ 批量核销（评审 Top10 #5，2026-11-02） ============

/** 批量核销分配结果行（一张应收一条） */
export interface BatchAllocRow {
  receivable_id: number;
  receivable_no: string;
  amount: string;
}

/** 批量核销响应 */
export interface BatchReceiptResult {
  customer: number;
  allocated: BatchAllocRow[];
  total_allocated: string;
  leftover: string;
}

/** POST /finance/customers/{customer_id}/receipts-batch 按客户批量核销：
 * FIFO（最早应收先收）自动分配，溢收款后端 400 硬拦。 */
export function batchReceiptByCustomer(
  customerId: number,
  body: {
    total: string | number;
    method?: ReceiptMethod;
    received_at?: string;
    handler?: string;
    remark?: string;
  },
): Promise<BatchReceiptResult> {
  return http.post<BatchReceiptResult>(
    `/finance/customers/${customerId}/receipts-batch`,
    body as unknown as Record<string, unknown>);
}

// ============ 移动加权库存估值 ============

/** GET /finance/stock-valuation?material_id=&date_to= */
export function fetchStockValuation(params?: {
  material_id?: number; date_to?: string;
}): Promise<StockValuation> {
  return http.get<StockValuation>('/finance/stock-valuation',
    (params || {}) as Record<string, unknown>);
}

// ============ 采购应付、供应商付款、其他收支与经营现金流 ============

export interface Payable {
  id: number;
  payable_no: string;
  supplier: number | null;
  supplier_name: string;
  purchase: number | null;
  amount: string;
  paid_amount: string;
  outstanding: string;
  status: 'unpaid' | 'partial' | 'settled';
  status_display: string;
  payable_at: string;
  due_at: string | null;
  remark: string;
  created_at: string;
}

export interface SupplierPayment {
  id: number;
  payable: number | null;
  payable_no: string;
  supplier: number | null;
  supplier_name: string;
  amount: string;
  paid_at: string;
  method: string;
  transaction_ref: string;
  status: 'active' | 'voided';
  status_display: string;
  remark: string;
  voided_at: string | null;
  void_reason: string;
  created_at: string;
}

export interface CashEntry {
  id: number;
  entry_no: string;
  direction: 'income' | 'expense';
  direction_display: string;
  category: string;
  amount: string;
  occurred_at: string;
  counterparty: string;
  method: string;
  reference_no: string;
  status: 'active' | 'voided';
  status_display: string;
  remark: string;
  voided_at: string | null;
  void_reason: string;
  created_at: string;
}

export interface CashFlowRow {
  period: string | null;
  customer_receipts: string;
  other_income: string;
  salary_payments: string;
  supplier_payments: string;
  expenses: string;
  income: string;
  /** 后端原口径：流出为负数。 */
  outflow: string;
  net: string;
}

export interface CashFlowResponse {
  date_from: string;
  date_to: string;
  group_by: 'day' | 'month';
  rows: CashFlowRow[];
  totals: CashFlowRow;
  note: string;
}

export function fetchPayables(params?: Record<string, unknown>): Promise<Paginated<Payable>> {
  return http.get<Paginated<Payable>>('/finance/payables', params);
}

export function createPayable(body: Record<string, unknown>): Promise<Payable> {
  return http.post<Payable>('/finance/payables', body);
}

export function voidPayable(id: number): Promise<{ id: number; voided: boolean }> {
  return http.post<{ id: number; voided: boolean }>(`/finance/payables/${id}/void`);
}

export function fetchSupplierPayments(params?: Record<string, unknown>): Promise<Paginated<SupplierPayment>> {
  return http.get<Paginated<SupplierPayment>>('/finance/supplier-payments', params);
}

export function createSupplierPayment(body: Record<string, unknown>): Promise<SupplierPayment> {
  return http.post<SupplierPayment>('/finance/supplier-payments', body);
}

export function voidSupplierPayment(id: number, reason: string): Promise<SupplierPayment> {
  return http.post<SupplierPayment>(`/finance/supplier-payments/${id}/void`, { reason });
}

export function fetchCashEntries(params?: Record<string, unknown>): Promise<Paginated<CashEntry>> {
  return http.get<Paginated<CashEntry>>('/finance/cash-entries', params);
}

export function createCashEntry(body: Record<string, unknown>): Promise<CashEntry> {
  return http.post<CashEntry>('/finance/cash-entries', body);
}

export function voidCashEntry(id: number, reason: string): Promise<CashEntry> {
  return http.post<CashEntry>(`/finance/cash-entries/${id}/void`, { reason });
}

export function fetchCashFlow(params: {
  date_from: string; date_to: string; group_by: 'day' | 'month';
}): Promise<CashFlowResponse> {
  return http.get<CashFlowResponse>('/finance/cash-flow', params);
}
