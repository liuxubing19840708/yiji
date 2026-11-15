/**
 * 通用交接第一期接口。所有业务字段与 handover-phase1-contract.md 对齐；
 * Bearer 与 X-Company-Id 由统一 request 层注入。
 */
import { http } from '../utils/request';
import { Paginated } from './types';

export type HandoverStatus = 'pending' | 'received' | 'rejected' | 'cancelled';
export type HandoverDifferenceType = '' | 'shortage' | 'overage' | 'damage' | 'wrong_item' | 'quality' | 'other';

export interface HandoverItem {
  id: number;
  ticket: number;
  ticket_label: string;
  quantity: number;
  received_quantity: number;
  difference_type: HandoverDifferenceType;
  reason: string;
}

export interface HandoverEvent {
  id: number;
  action: 'created' | 'received' | 'rejected' | 'reassigned' | 'cancelled';
  actor: number;
  actor_name: string;
  actual_user: number | null;
  actual_user_name: string | null;
  reason: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  created_at: string;
}

export interface Handover {
  id: number;
  handover_no: string;
  batch_no: string;
  kind: 'cutting';
  status: HandoverStatus;
  source_node: number;
  source_node_name: string;
  target_node: number;
  target_node_name: string;
  target_group_no: number | null;
  receiver: number | null;
  receiver_name: string;
  created_by: number;
  created_by_name: string;
  received_by: number | null;
  received_by_name: string | null;
  received_at: string | null;
  reject_reason: string;
  has_discrepancy: boolean;
  total_quantity: number;
  received_quantity: number;
  policy_mode: 'off' | 'warn' | 'strict';
  timeout_minutes: number;
  due_at: string;
  is_overdue: boolean;
  overdue_minutes: number;
  remark: string;
  created_at: string;
  items: HandoverItem[];
  events: HandoverEvent[];
}

export interface HandoverCreateItem {
  ticket: number;
  quantity: number;
}

export interface HandoverCreateTarget {
  receiver: number | null;
  target_group_no: number | null;
  items: HandoverCreateItem[];
}

export interface HandoverCreatePayload {
  kind: 'cutting';
  request_id: string;
  source_node: number;
  target_node: number;
  remark: string;
  targets: HandoverCreateTarget[];
}

export interface HandoverCreateResult {
  batch_no: string;
  items: Handover[];
}

export interface HandoverBatch extends HandoverCreateResult {
  summary: {
    handover_count: number;
    received_count: number;
    pending_count: number;
    rejected_count: number;
    total_quantity: number;
    received_quantity: number;
  };
}

export interface HandoverPolicy {
  id: number;
  kind: 'cutting';
  mode: 'off' | 'warn' | 'strict';
  source_node: number | null;
  target_node: number | null;
  target_group_no: number | null;
  receiver: number | null;
  timeout_minutes: number;
  updated_by_name: string;
}

export type HandoverPolicyPayload = Omit<HandoverPolicy, 'id' | 'updated_by_name'>;

export interface ReceiveDifference {
  id: number;
  received_quantity: number;
  difference_type: Exclude<HandoverDifferenceType, ''>;
  reason: string;
}

export function fetchHandovers(params: {
  box: 'mine' | 'created';
  status?: HandoverStatus;
  batch_no?: string;
  kind?: 'cutting';
  page?: number;
  page_size?: number;
}): Promise<Paginated<Handover>> {
  return http.get<Paginated<Handover>>('/handovers/', params as Record<string, unknown>);
}

export function fetchHandover(id: number): Promise<Handover> {
  return http.get<Handover>(`/handovers/${id}/`);
}

export function createHandovers(data: HandoverCreatePayload): Promise<HandoverCreateResult> {
  return http.post<HandoverCreateResult>('/handovers/', data as unknown as Record<string, unknown>);
}

export function receiveHandover(id: number, data: {
  request_id: string;
  reason?: string;
  items?: ReceiveDifference[];
}): Promise<Handover> {
  return http.post<Handover>(`/handovers/${id}/receive`, data as Record<string, unknown>);
}

export function rejectHandover(id: number, data: { request_id: string; reason: string }): Promise<Handover> {
  return http.post<Handover>(`/handovers/${id}/reject`, data);
}

export function reassignHandover(id: number, data: {
  request_id: string;
  receiver: number;
  reason: string;
}): Promise<Handover> {
  return http.post<Handover>(`/handovers/${id}/reassign`, data);
}

export function fetchHandoverBatch(batchNo: string): Promise<HandoverBatch> {
  return http.get<HandoverBatch>(`/handovers/batches/${encodeURIComponent(batchNo)}/`);
}

export function fetchHandoverPolicies(): Promise<HandoverPolicy[] | Paginated<HandoverPolicy>> {
  return http.get<HandoverPolicy[] | Paginated<HandoverPolicy>>('/handovers/policies');
}

export function createHandoverPolicy(data: HandoverPolicyPayload): Promise<HandoverPolicy> {
  return http.post<HandoverPolicy>('/handovers/policies', data as unknown as Record<string, unknown>);
}

export function updateHandoverPolicy(id: number, data: HandoverPolicyPayload): Promise<HandoverPolicy> {
  return http.put<HandoverPolicy>(`/handovers/policies/${id}`, data as unknown as Record<string, unknown>);
}
