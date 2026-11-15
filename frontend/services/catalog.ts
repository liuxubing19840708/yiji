/**
 * 档案接口（catalog）：客户/分组、工序、款式、菲票。
 * 契约以后端 apps/catalog/views.py 为准（2026-10-15 全量核对）。
 */
import { http } from '../utils/request';
import {
  BundleProgressItem, Customer, CustomerGroup, Process, Style,
  StyleProcessItem, Ticket, Paginated
} from './types';

/** 兼容分页/数组两种返回（后端 ListAPIView 默认分页） */
function unwrapList<T>(data: Paginated<T> | T[]): T[] {
  return Array.isArray(data) ? data : (data.results || []);
}

// ============ 客户 ============

/** 客户分组（后端分页返回，此处解包为数组） */
export function fetchCustomerGroups(): Promise<CustomerGroup[]> {
  return http.get<Paginated<CustomerGroup>>('/customer-groups', { page_size: 100 }).then(unwrapList);
}

export function createCustomerGroup(name: string): Promise<CustomerGroup> {
  return http.post<CustomerGroup>('/customer-groups', { name });
}

export function updateCustomerGroup(id: number, name: string): Promise<CustomerGroup> {
  return http.put<CustomerGroup>(`/customer-groups/${id}`, { name });
}

export function deleteCustomerGroup(id: number): Promise<unknown> {
  return http.del(`/customer-groups/${id}`);
}

export function fetchCustomers(params?: { keyword?: string; group_id?: number; page?: number; page_size?: number }): Promise<Paginated<Customer>> {
  return http.get<Paginated<Customer>>('/customers', params);
}

export function createCustomer(data: Partial<Customer>): Promise<Customer> {
  return http.post<Customer>('/customers', data as Record<string, unknown>);
}

export function updateCustomer(id: number, data: Partial<Customer>): Promise<Customer> {
  return http.put<Customer>(`/customers/${id}`, data as Record<string, unknown>);
}

export function deleteCustomer(id: number): Promise<unknown> {
  return http.del(`/customers/${id}`);
}

// ============ 工序 ============

/** 工序列表（后端分页，支持 keyword/is_fixed；此处解包，需要分页请用 fetchProcessesPaged） */
export function fetchProcesses(params?: { keyword?: string; is_fixed?: boolean; page?: number; page_size?: number }): Promise<Process[]> {
  const p = { page_size: 200, ...(params || {}) } as Record<string, unknown>;
  if (p.is_fixed !== undefined) {
    p.is_fixed = p.is_fixed ? '1' : '0';
  }
  return http.get<Paginated<Process>>('/processes', p).then(unwrapList);
}

export function createProcess(data: Partial<Process>): Promise<Process> {
  return http.post<Process>('/processes', data as Record<string, unknown>);
}

export function updateProcess(id: number, data: Partial<Process>): Promise<Process> {
  return http.put<Process>(`/processes/${id}`, data as Record<string, unknown>);
}

export function deleteProcess(id: number): Promise<unknown> {
  return http.del(`/processes/${id}`);
}

/** 扫菲工序选择器（catalog 版）：固定工序 ∪ 本人指定工序，返回 Process 数组 */
export function fetchProcessSelector(): Promise<Process[]> {
  return http.get<Process[]>('/process-selector');
}

// ============ 款式 ============

export function fetchStyles(params?: { keyword?: string; customer_id?: number; page?: number; page_size?: number }): Promise<Paginated<Style>> {
  return http.get<Paginated<Style>>('/styles', params);
}

export function fetchStyle(id: number): Promise<Style> {
  return http.get<Style>(`/styles/${id}`);
}

export function createStyle(data: Partial<Style>): Promise<Style> {
  return http.post<Style>('/styles', data as Record<string, unknown>);
}

/** 更新款式；process_items 存在时同步保存工序单价（写入格式 {process_id, price, sort, allow_skip}） */
export function updateStyle(id: number, data: Partial<Style> & { process_items?: Array<{ process_id: number; price?: string; sort?: number; allow_skip?: boolean }> }): Promise<Style> {
  return http.put<Style>(`/styles/${id}`, data as Record<string, unknown>);
}

export function deleteStyle(id: number): Promise<unknown> {
  return http.del(`/styles/${id}`);
}

export function copyStyle(id: number): Promise<Style> {
  return http.post<Style>(`/styles/${id}/copy`);
}

/** 款式工序配置：走款式详情（Style.processes） */
export function fetchStyleProcesses(id: number): Promise<StyleProcessItem[]> {
  return fetchStyle(id).then((style) => (style.processes || []) as unknown as StyleProcessItem[]);
}

/**
 * 保存款式工序单价：PUT /styles/{id} + process_items。
 * 另有 POST /styles/{id}/sync-process 用于把配置同步到存量菲票（§5.1），见 syncStyleProcessesToTickets。
 */
export function updateStyleProcesses(id: number, processItems: Array<{ process_id: number; price?: string; sort?: number; allow_skip?: boolean }>): Promise<Style> {
  return http.put<Style>(`/styles/${id}`, { process_items: processItems } as Record<string, unknown>);
}

/** 把款式工序配置同步到该款存量菲票（可能返回 {need_confirm, ticket_count} 需二次确认） */
export function syncStyleProcessesToTickets(id: number, force = false): Promise<{ synced?: boolean; need_confirm?: boolean; ticket_count?: number }> {
  return http.post(`/styles/${id}/sync-process`, { confirm: force });
}

// ============ 菲票 ============

export function fetchTickets(params?: { production_id?: number; bed_no?: string; bundle_no?: string; is_printed?: boolean; page?: number; page_size?: number }): Promise<Paginated<Ticket>> {
  const p = { ...(params || {}) } as Record<string, unknown>;
  if (p.is_printed !== undefined) {
    p.is_printed = p.is_printed ? '1' : '0';
  }
  return http.get<Paginated<Ticket>>('/tickets', p);
}

/**
 * 编菲（§六）：按颜色×尺码矩阵批量生成菲票。
 * items: [{color, size, qty, fabric?, piece_no?}]，qty 为该颜色尺码总数量；
 * bundle_limit 每扎上限（超量自动拆整扎+尾扎），扎号自动递增冲突顺延。
 */
export function createTicketsBatch(data: {
  production_id: number;
  items: Array<{ color: string; size: string; qty: number; fabric?: string; piece_no?: string }>;
  bundle_limit?: number;
  start_bundle_no?: string;
}): Promise<Ticket[]> {
  return http.post<Ticket[]>('/tickets/batch', data as unknown as Record<string, unknown>);
}

export function fetchTicket(id: number): Promise<Ticket> {
  return http.get<Ticket>(`/tickets/${id}`);
}

/**
 * 扎级完成矩阵（2026-10-22 需求）：按床次批量返回每扎完成状态 + 参与员工。
 * productionIds 上限 50（后端截断）；订单详情/床次详情完成矩阵消费。
 */
export function fetchBundleProgress(productionIds: number[]): Promise<{ count: number; results: BundleProgressItem[] }> {
  return http.get<{ count: number; results: BundleProgressItem[] }>(
    '/records/bundle-progress',
    { production_ids: productionIds.join(',') }
  );
}
