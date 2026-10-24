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
  items: Array<{ id: number; fabric_name: string; color?: string; qty: string; unit?: string; price?: string }>;
  total_qty?: string;
  created_at?: string;
}

export function fetchPurchaseOrders(params?: { status?: string; page?: number; page_size?: number }): Promise<PaginatedLike<PurchaseOrder>> {
  return http.get<PaginatedLike<PurchaseOrder>>('/purchase-orders', params as Record<string, unknown>);
}

export function createPurchaseOrder(data: {
  order_no?: string; supplier_name?: string; ordered_at: string; remark?: string;
  items?: Array<{ fabric_name: string; color?: string; qty?: string | number; unit?: string; price?: string | number }>;
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
  items: Array<{ id: number; fabric_name: string; color?: string; lot_no: string; qty: string; unit?: string; lot_id: number }>;
  created_at?: string;
}

export function fetchStockIns(params?: { status?: string; page?: number; page_size?: number }): Promise<PaginatedLike<StockIn>> {
  return http.get<PaginatedLike<StockIn>>('/stock-ins', params as Record<string, unknown>);
}

export function createStockIn(data: {
  in_no?: string; source_type?: string; purchase?: number | null; remark?: string;
  items: Array<{ fabric_name: string; color?: string; lot_no: string; qty?: string | number; unit?: string }>;
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

// ============ 领料单 ============

export interface MaterialIssue {
  id: number;
  issue_no: string;
  issue_at: string;
  status: string; // draft | confirmed
  remark?: string;
  items: Array<{ id: number; production: number; bed_no: string; lot: number; lot_no: string; qty: string }>;
  created_at?: string;
}

export function fetchMaterialIssues(params?: { status?: string; page?: number; page_size?: number }): Promise<PaginatedLike<MaterialIssue>> {
  return http.get<PaginatedLike<MaterialIssue>>('/material-issues', params as Record<string, unknown>);
}

export function createMaterialIssue(data: {
  issue_no?: string; remark?: string;
  items: Array<{ production_id: number; lot_id: number; qty?: string | number }>;
}): Promise<MaterialIssue> {
  return http.post<MaterialIssue>('/material-issues', data as Record<string, unknown>);
}

export function fetchMaterialIssue(id: number): Promise<MaterialIssue> {
  return http.get<MaterialIssue>(`/material-issues/${id}`);
}

export function updateMaterialIssue(id: number, data: Partial<MaterialIssue>): Promise<MaterialIssue> {
  return http.put<MaterialIssue>(`/material-issues/${id}`, data as Record<string, unknown>);
}

export function deleteMaterialIssue(id: number): Promise<void> {
  return http.del<void>(`/material-issues/${id}`);
}

export function confirmMaterialIssue(id: number): Promise<MaterialIssue> {
  return http.post<MaterialIssue>(`/material-issues/${id}/confirm`);
}

/** 兼容分页/数组两种返回（与 services/production.ts unwrapList 口径一致） */
interface PaginatedLike<T> {
  count?: number;
  results?: T[];
}
