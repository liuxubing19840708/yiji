/**
 * 轻量仓库接口（批次 / 采购单 / 入库单 / 领料单）。
 * 契约以后端 apps/warehouse/views.py 为准（2026-10-18 全量核对）。
 */
import { http } from '../utils/request';

// ============ 面料批次（Lot） ============

export interface Lot {
  id: number;
  lot_no: string;
  fabric_name?: string;
  color?: string;
  qty?: string;
  unit?: string;
  batch_date?: string | null;
  source_in?: number | null;
  source_in_no?: string;
  remark?: string;
  created_at?: string;
}

export function fetchLots(params?: { keyword?: string; page?: number; page_size?: number }): Promise<PaginatedLike<Lot>> {
  return http.get<PaginatedLike<Lot>>('/lots', params as Record<string, unknown>);
}

export function createLot(data: {
  lot_no: string; fabric_name?: string; color?: string;
  qty?: string | number; unit?: string; batch_date?: string; remark?: string;
}): Promise<Lot> {
  return http.post<Lot>('/lots', data as Record<string, unknown>);
}

export function fetchLot(id: number): Promise<Lot> {
  return http.get<Lot>(`/lots/${id}`);
}

export function updateLot(id: number, data: Partial<Lot>): Promise<Lot> {
  return http.put<Lot>(`/lots/${id}`, data as Record<string, unknown>);
}

export function deleteLot(id: number): Promise<void> {
  return http.del<void>(`/lots/${id}`);
}

// ============ 床次 × 批次血缘 ============

export interface LotLink {
  id: number;
  production: number;
  lot: number;
  lot_no: string;
  fabric_name?: string;
  color?: string;
  qty: string;
}

export function fetchProductionLotLinks(productionId: number): Promise<LotLink[]> {
  return http.get<LotLink[]>(`/productions/${productionId}/lot-links`);
}

/** 整表重建床次血缘：items=[{lot_id, qty}] */
export function updateProductionLotLinks(productionId: number, items: Array<{ lot_id: number; qty?: string | number }>): Promise<LotLink[]> {
  return http.put<LotLink[]>(`/productions/${productionId}/lot-links`, { items } as Record<string, unknown>);
}

// ============ 采购单 ============

export interface PurchaseOrder {
  id: number;
  order_no: string;
  supplier_name?: string;
  ordered_at: string;
  status: string; // draft | done
  remark?: string;
  items: Array<{ id: number; fabric_name: string; color?: string; qty: string; unit?: string; price?: string;
    /** P2 契约下发（暂收带出/需求汇总锚点）：关联物料与到料累计 */
    material?: number | null; material_name?: string; material_unit?: string; arrived_qty?: string; qualified_qty?: string; demand_source?: string;
    /** D19：用户原报单价单位（''=按主单位）；price 恒为主单位规范化值 */
    price_unit?: string }>
  total_qty?: string;
  created_at?: string;
}

export function fetchPurchaseOrders(params?: { status?: string; page?: number; page_size?: number }): Promise<PaginatedLike<PurchaseOrder>> {
  return http.get<PaginatedLike<PurchaseOrder>>('/purchase-orders', params as Record<string, unknown>);
}

export function createPurchaseOrder(data: {
  order_no?: string; supplier_name?: string; ordered_at?: string; remark?: string;
  items?: Array<{ fabric_name: string; color?: string; qty?: string | number; unit?: string; price?: string | number;
    price_unit?: string; material_id?: number | null; demand_source?: string }>;
}): Promise<PurchaseOrder> {
  return http.post<PurchaseOrder>('/purchase-orders', data as Record<string, unknown>);
}

export function fetchPurchaseOrder(id: number): Promise<PurchaseOrder> {
  return http.get<PurchaseOrder>(`/purchase-orders/${id}`);
}

export function updatePurchaseOrder(id: number, data: Partial<PurchaseOrder>): Promise<PurchaseOrder> {
  return http.put<PurchaseOrder>(`/purchase-orders/${id}`, data as Record<string, unknown>);
}

export function deletePurchaseOrder(id: number): Promise<void> {
  return http.del<void>(`/purchase-orders/${id}`);
}

export function confirmPurchaseOrder(id: number): Promise<PurchaseOrder> {
  return http.post<PurchaseOrder>(`/purchase-orders/${id}/confirm`);
}

// ============ 入库单 ============

export interface StockIn {
  id: number;
  in_no: string;
  source_type: string; // purchase | return | other
  purchase?: number | null;
  purchase_no?: string;
  in_at: string;
  status: string; // draft | done
  remark?: string;
  items: Array<{ id: number; fabric_name: string; color?: string; lot_no: string; qty: string; unit?: string; lot_id: number;
    unit_price?: string | null; price_unit?: string }>;
  created_at?: string;
}

export function fetchStockIns(params?: { status?: string; page?: number; page_size?: number }): Promise<PaginatedLike<StockIn>> {
  return http.get<PaginatedLike<StockIn>>('/stock-ins', params as Record<string, unknown>);
}

export function createStockIn(data: {
  in_no?: string; source_type?: string; purchase?: number | null; remark?: string;
  items: Array<{ fabric_name: string; color?: string; lot_no: string; qty?: string | number; unit?: string;
    unit_price?: string | number | null; price_unit?: string; material_id?: number | null }>;
}): Promise<StockIn> {
  return http.post<StockIn>('/stock-ins', data as Record<string, unknown>);
}

export function fetchStockIn(id: number): Promise<StockIn> {
  return http.get<StockIn>(`/stock-ins/${id}`);
}

export function updateStockIn(id: number, data: Partial<StockIn>): Promise<StockIn> {
  return http.put<StockIn>(`/stock-ins/${id}`, data as Record<string, unknown>);
}

export function deleteStockIn(id: number): Promise<void> {
  return http.del<void>(`/stock-ins/${id}`);
}

export function confirmStockIn(id: number): Promise<StockIn> {
  return http.post<StockIn>(`/stock-ins/${id}/confirm`);
}

// ============ 领料单（P1 定额化：type 双态 + 定额/超领列；契约 2026-10-24 逐字段核对） ============

/** 领料单类型：issue=领料 return=退料（行 qty 正数=退回量，确认时冲减已领量） */
export type MaterialIssueType = 'issue' | 'return';

export interface IssueItem {
  id: number;
  production: number;
  bed_no: string;
  lot: number;
  lot_no: string;
  /** 批次单位（与 qty 同单位；主单位=material_unit） */
  lot_unit: string;
  qty: string;
  /** 定额行 id（无则该行为手选物料/存量行） */
  bom_item: number | null;
  material: number | null;
  material_name: string;
  material_unit: string;
  /** 定额数量（单位=material_unit；null=无定额） */
  qty_req: string | null;
  /** 后端落表时的超额标记 */
  over: boolean;
  /** 超额状态：normal/over/no_req/pending（换算缺参待补） */
  over_state: string;
  over_reason: string;
}

export interface MaterialIssue {
  id: number;
  issue_no: string;
  /** P1：issue=领料 return=退料（存量单缺省按 issue 兜底） */
  type?: MaterialIssueType | string;
  issue_at: string;
  status: string; // draft | confirmed
  remark?: string;
  items: IssueItem[];
  created_at?: string;
}

/** 领料行提交格式：定额行传 bom_item_id（服务端算定 material/qty_req）；
 * 无 BOM 手选物料传 material_id（qty_req 可填自报定额，留空=无定额）；
 * 超领零拦截，over_reason 选填（缩率超差/避疵/缸差排版/其他） */
export interface IssueItemPayload {
  production_id: number;
  lot_id: number;
  qty: string | number;
  bom_item_id?: number | null;
  material_id?: number | null;
  qty_req?: string | number | null;
  over_reason?: string;
}

export function fetchMaterialIssues(params?: { status?: string; page?: number; page_size?: number }): Promise<PaginatedLike<MaterialIssue>> {
  return http.get<PaginatedLike<MaterialIssue>>('/material-issues', params as Record<string, unknown>);
}

export function createMaterialIssue(data: {
  issue_no?: string; type?: MaterialIssueType | string; remark?: string;
  items: IssueItemPayload[];
}): Promise<MaterialIssue> {
  return http.post<MaterialIssue>('/material-issues', data as unknown as Record<string, unknown>);
}

export function fetchMaterialIssue(id: number): Promise<MaterialIssue> {
  return http.get<MaterialIssue>(`/material-issues/${id}`);
}

export function updateMaterialIssue(id: number, data: Partial<MaterialIssue>): Promise<MaterialIssue> {
  return http.put<MaterialIssue>(`/material-issues/${id}`, data as unknown as Record<string, unknown>);
}

export function deleteMaterialIssue(id: number): Promise<void> {
  return http.del<void>(`/material-issues/${id}`);
}

export function confirmMaterialIssue(id: number): Promise<MaterialIssue> {
  return http.post<MaterialIssue>(`/material-issues/${id}/confirm`);
}

// ============ P1 超额领料差异报表（GET /issues/divergence，挂 warehouse.manage） ============

/** 只统计已确认单；数量均为 Material 主单位（批次单位不同按克重×幅宽折算） */
export interface DivergenceOverRow {
  issue_id: number;
  issue_no: string;
  issue_type: string; // issue | return
  lot_no: string;
  qty: string;
  qty_req: string | null;
  state: string; // normal | over | no_req | pending
  over: boolean;
  over_reason: string;
}

export interface DivergenceRow {
  production_id: number;
  bed_no: string;
  style_no: string;
  order_id: number | null;
  order_no: string;
  material_id: number | null;
  material_code: string;
  material_name: string;
  material_unit: string;
  /** 无物料存量行按批次单列 */
  lot_no: string;
  /** 实领（主单位） */
  qty_issued: string;
  /** 退回（主单位，冲减口径） */
  qty_returned: string;
  /** 净领 = max(0, 实领-退回) */
  qty_net: string;
  /** 发料时快照定额（Σ IssueItem.qty_req） */
  qty_req_snapshot: string;
  /** 按当前 active BOM 重算的应发；null=无生效 BOM */
  qty_req_current: string | null;
  over_qty: string;
  no_req_qty: string;
  over: boolean;
  /** 该行存在换算缺参（克重/幅宽未录） */
  unit_pending: boolean;
  over_rows: DivergenceOverRow[];
}

export interface DivergencePendingUnit {
  issue_no: string;
  lot_no: string;
  lot_unit: string;
  material_unit: string;
  qty: string;
}

export interface DivergenceReport {
  summary: {
    /** 超领占比 %（无定额量时 null） */
    over_ratio: number | null;
    /** 无定额占比 %（无实领量时 null） */
    no_req_ratio: number | null;
    issued_total: string;
    no_req_total: string;
    over_total: string;
    /** 待补参数行数 */
    pending_count: number;
  };
  rows: DivergenceRow[];
  pending_units: DivergencePendingUnit[];
}

/** GET /issues/divergence?date_from=&date_to=&order_id=&production_id=&material_id= */
export function fetchIssueDivergence(params?: {
  date_from?: string; date_to?: string;
  order_id?: number | string; production_id?: number | string; material_id?: number | string;
}): Promise<DivergenceReport> {
  return http.get<DivergenceReport>('/issues/divergence', params as Record<string, unknown> || {});
}

/** 兼容分页/数组两种返回（与 services/production.ts unwrapList 口径一致） */
interface PaginatedLike<T> {
  count?: number;
  results?: T[];
}

// ============ P2 供应商主档（权限点 warehouse.supplier，legacy 默认零授权） ============
// 契约：backend/contract_check.py suppliers 5 条路由 + apps/warehouse/views.py
// SupplierListCreateView / SupplierDetailView（2026-10-24 逐字段核对）。

export interface Supplier {
  id: number;
  code: string;
  name: string;
  contact_person: string;
  phone: string;
  settle_method: string;
  rating: string;
  remark: string;
  created_at: string;
}

/** GET /suppliers?keyword=（模糊 编码/名称/联系人/电话） */
export function fetchSuppliers(params?: {
  keyword?: string; page?: number; page_size?: number;
}): Promise<PaginatedLike<Supplier>> {
  return http.get<PaginatedLike<Supplier>>('/suppliers', params as Record<string, unknown> || {});
}

/** POST /suppliers：code 必填（空/重复 400 文案直显） */
export function createSupplier(data: {
  code: string; name?: string; contact_person?: string; phone?: string;
  settle_method?: string; rating?: string; remark?: string;
}): Promise<Supplier> {
  return http.post<Supplier>('/suppliers', data as Record<string, unknown>);
}

export function fetchSupplier(id: number): Promise<Supplier> {
  return http.get<Supplier>(`/suppliers/${id}`);
}

/** PUT /suppliers/{id}：code 变更后端查重；其余字段字符串截断 128 */
export function updateSupplier(id: number, data: Partial<Supplier>): Promise<Supplier> {
  return http.put<Supplier>(`/suppliers/${id}`, data as Record<string, unknown>);
}

/** DELETE /suppliers/{id}：软删；仍被采购单/暂收单引用 400
 * （「该供应商已被采购单/暂收单引用，无法删除」——前端直显不加工） */
export function deleteSupplier(id: number, silent = false): Promise<void> {
  return http.del<void>(`/suppliers/${id}`, silent);
}

// ============ P2 到料暂收（暂收 → IQC 行级拆量 → 确认生成采购入库） ============
// 契约：backend/contract_check.py receipts 6 条路由 + views.py Receipt 三视图。
// 权限全部挂 warehouse.manage（复用 P1 点，legacy 覆盖旧 production）。

export interface ReceiptItem {
  id: number;
  /** 关联采购行（null=无单直收；带出物料/颜色/单位与确认价链） */
  purchase_item: number | null;
  purchase_item_no: string;
  material: number | null;
  material_name: string;
  material_unit: string;
  fabric_name: string;
  color: string;
  /** 批次号（沿用供应商原始编号，确认时 get_or_create） */
  lot_no: string;
  unit: string;
  arrived_qty: string;
  qualified_qty: string;
  rejected_qty: string;
  remark: string;
}

export interface Receipt {
  id: number;
  receipt_no: string;
  supplier: number | null;
  supplier_code: string;
  supplier_name: string;
  purchase: number | null;
  purchase_no: string;
  status: string; // draft | confirmed
  confirmed_at: string | null;
  confirmed_by: number | null;
  confirmed_by_name: string;
  remark: string;
  items: ReceiptItem[];
  created_at: string;
}

/** 暂收行提交：带 purchase_item 时 fabric_name/color/unit 缺省由 PO 行带出
 * （前端允许手改）；IQC 拆量零拦截：合格+拒收可超到料量（让步收货），
 * 前端仅拦 合格+拒收=0 与 lot_no/fabric_name 缺失。 */
export interface ReceiptItemPayload {
  purchase_item?: number | null;
  material_id?: number | null;
  fabric_name?: string;
  color?: string;
  lot_no: string;
  unit?: string;
  arrived_qty?: string | number;
  qualified_qty?: string | number;
  rejected_qty?: string | number;
  remark?: string;
}

/** GET /receipts?status=&keyword=（keyword 命中 单号/供应商名/面料名） */
export function fetchReceipts(params?: {
  status?: string; keyword?: string; page?: number; page_size?: number;
}): Promise<PaginatedLike<Receipt>> {
  return http.get<PaginatedLike<Receipt>>('/receipts', params as Record<string, unknown> || {});
}

/** POST /receipts：单号留空自动 ZS+日期+序号（前端从不传单号） */
export function createReceipt(data: {
  receipt_no?: string;
  supplier?: number | null;
  supplier_name?: string;
  purchase?: number | null;
  remark?: string;
  items: ReceiptItemPayload[];
}): Promise<Receipt> {
  return http.post<Receipt>('/receipts', data as unknown as Record<string, unknown>);
}

export function fetchReceipt(id: number): Promise<Receipt> {
  return http.get<Receipt>(`/receipts/${id}`);
}

/** PUT /receipts/{id}：仅 draft；items 整表重建（IQC 行级拆量在此落定） */
export function updateReceipt(id: number, data: Partial<{
  supplier: number | null; supplier_name: string; purchase: number | null;
  remark: string; items: ReceiptItemPayload[];
}>): Promise<Receipt> {
  return http.put<Receipt>(`/receipts/${id}`, data as unknown as Record<string, unknown>);
}

/** DELETE /receipts/{id}：仅 draft */
export function deleteReceipt(id: number, silent = false): Promise<void> {
  return http.del<void>(`/receipts/${id}`, silent);
}

/** POST /receipts/{id}/confirm：合格量>0 的行自动生成采购入库（P1 价链带价）；
 * 拒收留痕不进库存；全部行拒收不生成入库单。 */
/** POST /receipts/{id}/confirm 确认即入库（合格量自动生成采购入库单）。
 * 响应在 Receipt 之上回带 in_no/in_id（INT 补缺口 E，2026-10-24）：
 * 生成过入库单时回带，全拒收不生成则缺省两字段。 */
export function confirmReceipt(id: number): Promise<Receipt & {
  in_no?: string; in_id?: number;
}> {
  return http.post<Receipt & { in_no?: string; in_id?: number; }>(
    `/receipts/${id}/confirm`);
}

// ============ P2 采购需求汇总（GET /demand-summary，挂 warehouse.manage） ============
// 口径（DemandSummaryView docstring 为准）：qty_suggested = Σ(active BOM
// gross_usage × 订单未投产件数) − 当前库存 − 在途；可为负（库存富余）。

export interface DemandSummaryRow {
  material_id: number;
  material_code: string;
  material_name: string;
  unit: string;
  qty_demand: string;
  qty_stock: string;
  qty_in_transit: string;
  /** 订单空差口径（不含安全库存，可为负=富余） */
  qty_suggested: string;
  /** P2.5 主单位安全水位（0=未设） */
  safety_stock: string;
  /** P2.5 补货点口径 max(0, 需求-库存-在途+安全库存)，≥0 */
  qty_reorder: string;
}

export interface DemandSummary {
  summary: {
    order_total: number;
    order_planned_full: number;
    order_no_style: number;
    order_no_bom: number;
    /** 缺克重/幅宽换算参数的行数（这些行不入合计，前端显黄条） */
    conversion_pending_cnt: number;
    material_cnt: number;
    /** 建议采购量 > 0 的物料数 */
    need_cnt: number;
    /** P2.5 补货点口径 >0 行数（含纯水位触发） */
    reorder_cnt: number;
    /** P2.5 库存+在途 < 安全库存 的物料数（纯水位预警） */
    safety_alert_cnt: number;
  };
  rows: DemandSummaryRow[];
}

/** GET /demand-summary?style_id=&customer_id= */
export function fetchDemandSummary(params?: {
  style_id?: number | string; customer_id?: number | string;
}): Promise<DemandSummary> {
  return http.get<DemandSummary>('/demand-summary',
    params as Record<string, unknown> || {});
}
