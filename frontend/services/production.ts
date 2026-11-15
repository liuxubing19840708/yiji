/**
 * 生产管理 + 扫码解析 + 扫菲登记接口（production / records）。
 * 契约以后端 apps/production/views.py、apps/records/views.py 为准（2026-10-15 全量核对）。
 */
import { http } from '../utils/request';
import { BASE_URL } from '../utils/config';
import {
  Production, ProductionProcessItem, RecordItem, ScanParseResult,
  Workbench, Paginated, ProcessSelectorItem
} from './types';

// ============ 床次订单（production） ============

export function fetchProductions(params?: {
  keyword?: string; bed_no?: string; style_id?: number; order_type?: string;
  is_locked?: boolean; is_cut_done?: boolean; page?: number; page_size?: number;
}): Promise<Paginated<Production>> {
  const p = { ...(params || {}) } as Record<string, unknown>;
  // 后端只认 '0'/'1'
  if (p.is_locked !== undefined) {
    p.is_locked = p.is_locked ? '1' : '0';
  }
  if (p.is_cut_done !== undefined) {
    p.is_cut_done = p.is_cut_done ? '1' : '0';
  }
  return http.get<Paginated<Production>>('/productions', p);
}

export function createProduction(data: {
  style_id?: number; style?: number; bed_no: string; total_qty?: number;
  plan_qty?: number | null;
  order_type?: string; customer?: number | null; fabric?: string;
  /** 来源单号/客户订单号（质量追溯 P0，选填） */
  source_order_no?: string;
  /** 挂单开床：带 order_id 时 style 可不传（后端从订单继承款式） */
  order_id?: number;
  /** D4 无生效BOM拦截放行：需同时带 bypass_reason（后端 ops audit 留痕） */
  bom_bypass?: boolean;
  bypass_reason?: string;
  process_items?: Array<{ process_id: number; price?: string }>;
}): Promise<Production> {
  return http.post<Production>('/productions', data as Record<string, unknown>);
}

export function fetchProduction(id: number): Promise<Production> {
  return http.get<Production>(`/productions/${id}`);
}

export function updateProduction(id: number, data: Partial<Production>): Promise<Production> {
  return http.put<Production>(`/productions/${id}`, data as Record<string, unknown>);
}

/** 锁定/解锁（PUT，{locked} 缺省为取反） */
export function lockProduction(id: number, locked?: boolean): Promise<{ id: number; is_locked: boolean }> {
  return http.put<{ id: number; is_locked: boolean }>(`/productions/${id}/lock`, locked === undefined ? undefined : { locked });
}

/** 置裁完/取消（PUT，{is_cut_done} 缺省为取反）。裁完=裁剪完成标记，不拦截登记/加扎 */
export function cutDoneProduction(id: number, isCutDone?: boolean): Promise<{ id: number; is_cut_done: boolean }> {
  return http.put<{ id: number; is_cut_done: boolean }>(`/productions/${id}/cut-done`, isCutDone === undefined ? undefined : { is_cut_done: isCutDone });
}

/** 订单工序（覆盖款式单价）；读取返回数组，写入格式 {process_id, price} */
export function fetchOrderProcesses(id: number): Promise<ProductionProcessItem[]> {
  return http.get<ProductionProcessItem[]>(`/productions/${id}/order-processes`);
}

export function updateOrderProcesses(id: number, processItems: Array<{ process_id: number; price?: string }>): Promise<ProductionProcessItem[]> {
  return http.put<ProductionProcessItem[]>(`/productions/${id}/order-processes`, { process_items: processItems });
}

/** 该床次的菲票列表：{count, process_count, results} */
export function fetchProductionBundles(id: number, params?: { page?: number; page_size?: number }): Promise<{ count: number; process_count: number; results: Array<Record<string, unknown>> }> {
  return http.get(`/productions/${id}/bundles`, params);
}

// ---- 按扎卡片视图（竞品对齐借鉴#1，2026-11-05）：expand=1 逐扎逐工序明细 ----

/** 扎上单条登记（改数/撤销的行内入口；无 salary.view 时后端不下发明细） */
export interface BundleRecordItem {
  id: number;
  qty: number;
  user_id: number;
  user_name: string;
  registered_at: string;
  is_today: boolean;
  settled: boolean;
}

/** 扎卡上的工序行：价目 + 已登量 + 记录明细 */
export interface BundleProcessItem {
  process_id: number;
  name: string;
  price: string;
  registered_qty: number;
  done: boolean;
  records: BundleRecordItem[];
}

/** 扎卡行：基础字段同旧 bundles，expand=1 时附 processes */
export interface BundleCardRow {
  id: number;
  bundle_no: string;
  color: string;
  size: string;
  qty: number;
  is_printed: boolean;
  registered_process_count: number;
  process_count: number;
  registered_qty: number;
  state: 'none' | 'partial' | 'done';
  state_display: string;
  processes?: BundleProcessItem[];
}

export function fetchBundleCards(
  id: number,
  params?: { keyword?: string; state?: string }
): Promise<{ count: number; process_count: number; can_see_records: boolean; results: BundleCardRow[] }> {
  return http.get(`/productions/${id}/bundles`, { expand: 1, ...params });
}

/** 裁货明细（颜色×尺码汇总） */
export function fetchCutDetail(id: number): Promise<Record<string, unknown>> {
  return http.get<Record<string, unknown>>(`/productions/${id}/cut-detail`);
}

/** 厂内常用颜色（编菲点选）：按历史菲票颜色用量倒序 top 20 */
export function fetchProductionColors(): Promise<{ results: Array<{ name: string; count: number }> }> {
  return http.get<{ results: Array<{ name: string; count: number }> }>('/production-colors');
}

/** 编菲录入提示：sizes=厂内历史尺码、fabrics=厂内历史面料（均用量倒序 top 20） */
export function fetchProductionHints(): Promise<{ sizes: Array<{ name: string; count: number }>; fabrics: Array<{ name: string; count: number }> }> {
  return http.get<{ sizes: Array<{ name: string; count: number }>; fabrics: Array<{ name: string; count: number }> }>('/production-hints');
}

// ============ 扫码与登记（records） ============

/** 扫码解析菲票二维码 → {ticket, style, production}（后端字段名 qr） */
export function parseScan(code: string): Promise<ScanParseResult> {
  return http.post<ScanParseResult>('/scan/parse', { qr: code });
}

/** 扫菲登记（offline=true：弱网入本地队列自动补发） */
export function createRecord(data: {
  ticket_id: number;
  process_id: number;
  user_id?: number; // 协助扫菲：被代登记员工（需管理员）
  qty: number;
}, opts?: { offline?: boolean }): Promise<RecordItem> {
  return http.post<RecordItem>('/records', {
    ticket_id: data.ticket_id,
    process_id: data.process_id,
    user_id: data.user_id,
    qty: data.qty
  }, { offline: opts?.offline });
}

export function fetchRecords(params?: {
  ticket_id?: number; user_id?: number; process_id?: number;
  date_from?: string; date_to?: string; settlement?: string;
  page?: number; page_size?: number;
}): Promise<Paginated<RecordItem>> {
  return http.get<Paginated<RecordItem>>('/records', params);
}

/** 撤销登记（本人当日 / 管理员当月，已结算禁止） */
export function deleteRecord(id: number): Promise<unknown> {
  return http.del(`/records/${id}`);
}

/** 修改登记数量（原因必填，后端留痕；竞品对齐 GAP-3，2026-11-04；wx.request 无 PATCH 走 PUT） */
export function updateRecordQty(
  id: number, data: { qty: number; reason: string }
): Promise<unknown> {
  return http.put<unknown>(`/records/${id}`, data as unknown as Record<string, unknown>);
}

/** 批量登记结果行（GAP-2）：ok=false 时 error 带原因（超限/工序不在配置/锁定…） */
export interface BatchRegisterResultRow {
  ticket_id: number | null;
  bundle_no?: string;
  ok: boolean;
  error?: string;
  record_id?: number;
  qty?: number;
  amount?: string;
}

export interface BatchRegisterResult {
  total: number;
  ok: number;
  fail: number;
  results: BatchRegisterResultRow[];
}

/** 批量协助登记（竞品对齐 GAP-2，2026-11-04）：选员工→选工序→多扎一次登记，部分成功语义 */
export function batchRegister(data: {
  process_id: number;
  user_id?: number;
  entries: Array<{ ticket_id: number; qty?: number }>;
}): Promise<BatchRegisterResult> {
  return http.post<BatchRegisterResult>('/records/batch-register', data as unknown as Record<string, unknown>);
}

export interface BatchUndoResultRow { record_id: number; ok: boolean; error?: string; }
export interface BatchUndoResult { total: number; ok: number; fail: number; results: BatchUndoResultRow[]; }

/** 批量撤销（GAP-2）：逐条复用单撤门禁，部分成功语义 */
export function batchUndo(recordIds: number[]): Promise<BatchUndoResult> {
  return http.post<BatchUndoResult>(
    '/records/batch-undo', { record_ids: recordIds } as unknown as Record<string, unknown>);
}

/**
 * 计件明细导出（竞品对齐 GAP-7，2026-11-04）：GET /records/export 返回 xlsx 文件流，
 * 需 wx.downloadFile（带 Authorization 头）→ wx.openDocument 打开。
 * 返回完整 URL 供 downloadFile 使用；权限 salary.view 由后端校验。
 */
export function recordsExportUrl(params?: {
  user_id?: number; process_id?: number; date_from?: string; date_to?: string;
}): string {
  const qs: string[] = [];
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.push(`${k}=${encodeURIComponent(String(v))}`);
    });
  }
  return `${BASE_URL}/records${qs.length ? `?${qs.join('&')}` : ''}`;
}

/**
 * 登记页工序选择器：{results: [{id,name,is_fixed,price}], single}。
 * 传 ticket_id/user_id 时单价按该菲票解析（订单工序可覆盖款式价）。
 */
export function fetchRecordProcessSelector(params?: { ticket_id?: number; user_id?: number }): Promise<{ results: ProcessSelectorItem[]; single: boolean }> {
  return http.get('/records/process-selector', params);
}

/** 扫菲工作台：{beds: 进行中床次进度, my_today: 本人今日登记} */
export function fetchWorkbench(params?: { date_from?: string; date_to?: string }): Promise<Workbench> {
  return http.get<Workbench>('/records/workbench', params);
}

// ============ 手输码兜底（规格 §2.7.2，P0） ============

/** GET /tickets/lookup 候选行：床次/款号/色码/扎号/数量（后端字段以后端 views.py 为准，前端做缺省兜底） */
export interface TicketLookupRow {
  id: number;
  bed_no: string;
  bundle_no: string;
  color?: string;
  size?: string;
  qty: number;
  style_no?: string;
  style_name?: string;
  /** 床次 id（2026-10-29 与 /scan/parse 同构补齐）：选中后启用补打入口 */
  production_id?: number;
  /** 所属床次锁定态（后端若返回则前端预拦截，最终以后端 400 为准） */
  is_locked?: boolean;
  /** SA3 实际返回：status=locked/cut_done/normal（按床次派生） */
  status?: string;
}

/**
 * 手输码兜底：床次号+扎号查候选菲票（权限 records.create；0 命中后端 404）。
 * silent=true：404「未找到」/ 接口未就绪均由页面自行提示，请求层不弹通用错误。
 */
export function lookupTickets(bedNo: string, bundleNo: string): Promise<TicketLookupRow[]> {
  return http
    .get<TicketLookupRow[] | { results?: TicketLookupRow[] }>('/tickets/lookup', {
      bed_no: bedNo,
      bundle_no: bundleNo
    }, true)
    .then((res) => (Array.isArray(res) ? res : (res && res.results) || []));
}
