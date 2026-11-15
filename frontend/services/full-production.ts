/**
 * 新生产全流程 API：产前、路线节点、节点检验、裁片交接、包装入库与成品库存。
 * 路径与后端 config/urls.py 对齐；裁片子路由保留服务端要求的尾斜杠。
 */
import { http } from '../utils/request';

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface RouteNode {
  id: number;
  stage: number;
  stage_code: string;
  stage_name: string;
  stage_type: string;
  sequence: number;
  required: boolean;
  allow_parallel: boolean;
  allow_skip: boolean;
}

export interface ProductionStage {
  id: number;
  code: string;
  name: string;
  stage_type: string;
  is_active?: boolean;
  in_use?: boolean;
}

export interface ProductionRoute {
  id: number;
  code: string;
  name: string;
  is_template: boolean;
  is_active?: boolean;
  version?: number;
  previous_version?: number | null;
  instance_count?: number;
  configuration_warnings?: string[];
  order: number | null;
  production: number | null;
  source_template: number | null;
  nodes: RouteNode[];
  created_at: string;
  updated_at: string;
}

export interface NodeBalance {
  received: number;
  qualified: number;
  rework: number;
  scrapped: number;
  transferred: number;
  inputs: number;
  available: number;
  qualification_remaining: number;
  transferable: number;
  reversals: number;
  rework_transferred?: number;
  forward_transferred?: number;
}

export interface RouteBalanceRow {
  ticket_id: number;
  bundle_no: string;
  color: string;
  size: string;
  node_id: number;
  sequence: number;
  stage_type: string;
  balance: NodeBalance;
}

export type StageAction = 'receive' | 'complete' | 'transfer' | 'return' | 'scrap';

export interface QuantityLedger {
  id: number;
  route: number;
  node: number;
  node_name?: string;
  node_sequence?: number;
  bed_no?: string;
  bundle_no?: string;
  event_type_display?: string;
  order: number | null;
  production: number | null;
  ticket: number;
  color: string;
  size: string;
  event_type: string;
  quantity: number;
  source: string;
  operator: number | null;
  operator_name?: string;
  related_entry: number | null;
  reversal_of: number | null;
  request_id: string;
  reason: string;
  created_at: string;
}

export interface QualityInspection {
  id: number;
  ticket: number;
  ticket_bed_no: string;
  ticket_bundle_no: string;
  route: number;
  node: number;
  node_name: string;
  node_sequence: number;
  inspection_type: 'semi_inspection' | 'final_inspection' | 'reinspection';
  inspection_type_display: string;
  status: 'pending' | 'confirmed';
  status_display: string;
  inspected: number;
  qualified: number;
  rework: number;
  scrapped: number;
  rework_node: number | null;
  rework_node_name: string | null;
  reinspection_of: number | null;
  reason: string;
  inspector_name: string | null;
  inspected_at: string;
  confirmed_at: string | null;
  request_id: string | null;
  create_request_id?: string;
}

export interface CuttingHandover {
  id: number;
  ticket: number;
  ticket_no: string;
  bed_no: string;
  bundle_no: string;
  source_node: number;
  source_node_name: string;
  target_node: number;
  target_node_name: string;
  quantity: number;
  receiver: number;
  receiver_name: string;
  status: 'pending' | 'signed' | 'cancelled';
  remark: string;
  created_by_name: string;
  created_at: string;
  signed_by_name: string;
  signed_at: string | null;
}

export interface PackingLine {
  id: number;
  ticket: number;
  bed_no: string;
  bundle_no: string;
  color: string;
  size: string;
  quantity: number;
}

export interface PackingBox {
  id: number;
  box_no: string;
  total_quantity: number;
  lines: PackingLine[];
}

export interface PackingTask {
  id: number;
  task_no: string;
  route: number;
  route_code: string;
  packing_node: number;
  inbound_node: number;
  status: 'draft' | 'approved' | 'reversed';
  status_display: string;
  total_quantity: number;
  boxes: PackingBox[];
  remark: string;
  reversal_reason?: string;
  reverser_name?: string;
  reversed_at?: string | null;
  ledger_entries?: QuantityLedger[];
  created_at: string;
}

export interface InboundLine {
  id: number;
  box: number;
  box_no: string;
  ticket: number;
  bed_no: string;
  bundle_no: string;
  color: string;
  size: string;
  quantity: number;
}

export interface FinishedInbound {
  id: number;
  document_no: string;
  route: number;
  route_code: string;
  warehouse_name: string;
  location_name: string;
  status: 'draft' | 'approved' | 'reversed';
  status_display: string;
  total_quantity: number;
  lines: InboundLine[];
  remark: string;
  reversal_reason?: string;
  reverser_name?: string;
  reversed_at?: string | null;
  ledger_entries?: QuantityLedger[];
  created_at: string;
}

export interface FinishedInventoryRow {
  route_id: number;
  node_id: number;
  ticket_id: number;
  production_id: number;
  style_no: string;
  bed_no: string;
  bundle_no: string;
  color: string;
  size: string;
  available: number;
  received: number;
  transferred: number;
  scrapped: number;
  reversals: number;
}

export interface TechMeasurement {
  size: string;
  point: string;
  unit: string;
  value: string;
  tolerance_plus: string;
  tolerance_minus: string;
}

export interface SampleRecord {
  id: number;
  task: number;
  result: 'passed' | 'failed' | 'rework';
  note: string;
  attachments: string[];
  operator: number;
  operator_name: string;
  occurred_at: string;
  created_at: string;
}

export interface SampleTask {
  id: number;
  tech_pack: number;
  title: string;
  assignee: number | null;
  assignee_name: string;
  status: 'pending' | 'passed' | 'failed' | 'rework';
  records: SampleRecord[];
  created_at: string;
  updated_at: string;
}

export interface ReviewIssue {
  id: number;
  tech_pack: number;
  title: string;
  detail: string;
  status: 'open' | 'rectified' | 'closed';
  rectification: string;
  review_note: string;
  raised_by: number;
  raised_by_name: string;
  reviewed_by: number | null;
  handled_by_name: string;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface BriefingReceipt {
  id: number;
  target: number;
  employee: number;
  signed_at: string;
  remark: string;
  reversal_reason?: string;
  reversed_at?: string | null;
  created_at: string;
}

export interface BriefingTarget {
  id: number;
  tech_pack: number;
  employee: number;
  employee_name: string;
  note: string;
  receipt: BriefingReceipt | null;
  created_at: string;
}

export interface BriefingConfirmation {
  id: number;
  tech_pack: number;
  content_hash: string;
  note: string;
  confirmed_by: number;
  confirmed_by_name: string;
  confirmed_at: string;
  created_at: string;
}

export type CraftTemplateKind = 'measurements' | 'requirements' | 'challenges' |
  'common_mistakes' | 'quality_standards';

export interface CraftTemplate {
  id: number;
  kind: CraftTemplateKind;
  name: string;
  content: TechMeasurement[] | string;
  is_system: boolean;
  created_by_name?: string;
}

export function fetchCraftTemplates(kind?: CraftTemplateKind): Promise<CraftTemplate[]> {
  return http.get<CraftTemplate[]>('/preproduction/templates', kind ? { kind } : {});
}

export function createCraftTemplate(data: {
  kind: CraftTemplateKind; name: string; content: TechMeasurement[] | string;
}): Promise<CraftTemplate> {
  return http.post<CraftTemplate>('/preproduction/templates', data as unknown as Record<string, unknown>);
}

export function updateCraftTemplate(id: number, data: {
  kind: CraftTemplateKind; name: string; content: TechMeasurement[] | string;
}): Promise<CraftTemplate> {
  return http.put<CraftTemplate>(`/preproduction/templates/${id}`, data as unknown as Record<string, unknown>);
}

export interface TechPack {
  id: number;
  order: number;
  order_no: string;
  style: number;
  style_no: string;
  version_no: number;
  pattern_no: string;
  pattern_files: string[];
  measurements: TechMeasurement[];
  tolerances: Record<string, unknown>;
  attachments: Array<{ name: string; path: string; note: string }>;
  requirements: string;
  challenges: string;
  common_mistakes: string;
  quality_standards: string;
  content_hash: string;
  revision: number;
  is_current: boolean;
  sample_tasks: SampleTask[];
  review_issues: ReviewIssue[];
  briefing_targets: BriefingTarget[];
  briefing_confirmations: BriefingConfirmation[];
  approvals: Array<{
    id: number; status: string; reason: string; decided_at: string | null;
    decided_by_name?: string;
  }>;
  created_by: number;
  created_at: string;
  updated_at: string;
}

export interface TechPackPayload {
  pattern_no?: string;
  requirements: string;
  challenges: string;
  common_mistakes: string;
  quality_standards: string;
  measurements: TechMeasurement[];
  pattern_files: string[];
  tolerances: Record<string, unknown>;
  attachments: Array<{ name: string; path: string; note: string }>;
}

export interface PreproductionStatus {
  approved: boolean;
  approval: null | {
    id: number; status: string; decided_at: string; decided_by_name?: string; reason?: string;
  };
  problems: string[];
  status: 'draft' | 'pending_approval' | 'approved' | 'reconfirm';
  status_display: string;
  missing_items: string[];
  current_sample: SampleRecord | null;
  unresolved_issue_count: number;
  briefing_confirmation: BriefingConfirmation | null;
  has_previous_approval: boolean;
}

/** 单次用户意图一个请求号：调用方在失败重试时保留，成功后再换新。 */
export function createRequestId(prefix: string): string {
  const random = `${Math.random().toString(36).slice(2, 10)}${Math.random().toString(36).slice(2, 10)}`;
  const clean = prefix.replace(/[^a-z0-9-]/gi, '').slice(0, 12) || 'wx';
  return `${clean}-${Date.now().toString(36)}-${random}`.slice(0, 60);
}

export function fetchRoutes(isTemplate = false): Promise<ProductionRoute[]> {
  return http.get<ProductionRoute[]>('/manufacturing/routes', { is_template: isTemplate ? '1' : '0' });
}

export function fetchStages(): Promise<ProductionStage[]> {
  return http.get<ProductionStage[]>('/manufacturing/stages');
}

export function saveStage(data: { id?: number; code: string; name: string; stage_type: string }): Promise<ProductionStage> {
  const payload = { code: data.code, name: data.name, stage_type: data.stage_type };
  return data.id
    ? http.put<ProductionStage>(`/manufacturing/stages/${data.id}`, payload)
    : http.post<ProductionStage>('/manufacturing/stages', payload);
}

export interface RouteNodePayload {
  stage_id: number;
  sequence: number;
  required: boolean;
  allow_parallel: boolean;
  allow_skip: boolean;
}

export function saveRouteTemplate(data: {
  id?: number;
  code: string;
  name: string;
  nodes: RouteNodePayload[];
}): Promise<ProductionRoute> {
  return data.id
    ? http.put<ProductionRoute>(`/manufacturing/routes/${data.id}`, { name: data.name, nodes: data.nodes })
    : http.post<ProductionRoute>('/manufacturing/routes', data);
}

export function setStageStatus(id: number, isActive: boolean): Promise<ProductionStage> {
  return http.post<ProductionStage>(`/manufacturing/stages/${id}/status`, { is_active: isActive });
}

export function setRouteTemplateStatus(id: number, isActive: boolean): Promise<ProductionRoute> {
  return http.post<ProductionRoute>(`/manufacturing/routes/${id}/status`, { is_active: isActive });
}

export function copyRouteTemplate(id: number, mode: 'copy' | 'version', data: {
  code: string;
  name: string;
  nodes?: RouteNodePayload[];
}): Promise<ProductionRoute> {
  return http.post<ProductionRoute>(`/manufacturing/routes/${id}/${mode === 'version' ? 'new-version' : 'copy'}`, data);
}

export function instantiateRoute(id: number, data: {
  order_id?: number;
  production_id?: number;
  code?: string;
  name?: string;
}): Promise<ProductionRoute> {
  return http.post<ProductionRoute>(`/manufacturing/routes/${id}/instantiate`, data as Record<string, unknown>);
}

export function fetchRouteBalances(routeId: number, ticketId?: number): Promise<RouteBalanceRow[]> {
  return http.get<RouteBalanceRow[]>(`/manufacturing/routes/${routeId}/balances`, { ticket_id: ticketId });
}

export interface FlowDashboard {
  route: number;
  route_code: string;
  summary: { tickets: number; ticket_qty: number; wip: number; scrapped: number; finished_available: number };
  pending: { quality: number; cutting: number; packing: number; inbound: number };
  delivery: { ordered: number; shipped: number; remaining: number } | null;
  rows: Array<{
    ticket_id: number; bed_no: string; style_no: string; bundle_no: string;
    color: string; size: string; ticket_qty: number; wip: number;
    scrapped: number; finished_available: number;
    nodes: Array<{ node_id: number; sequence: number; stage_name: string; stage_type: string } & NodeBalance>;
  }>;
}

export function fetchRouteDashboard(routeId: number): Promise<FlowDashboard> {
  return http.get<FlowDashboard>(`/manufacturing/routes/${routeId}/dashboard`);
}

export interface OperationsDashboard {
  total: number;
  page: number;
  page_size: number;
  pages: number;
  has_next: boolean;
  has_previous: boolean;
  truncated: boolean;
  stalled_hours: number;
  summary: { routes: number; wip: number; scrapped: number; finished_available: number; stalled_routes: number };
  results: Array<{
    route: number;
    route_code: string;
    route_name: string;
    order: number | null;
    production: number | null;
    summary: FlowDashboard['summary'];
    pending: FlowDashboard['pending'];
    delivery: FlowDashboard['delivery'];
    stalled_nodes: Array<{ node_id: number; stage_name: string; available: number; last_activity_at: string }>;
  }>;
}

export function fetchOperationsDashboard(stalledHours = 24, filters: {
  order_id?: number;
  production_id?: number;
  customer_id?: number;
  style_no?: string;
  q?: string;
  page?: number;
  page_size?: number;
} = {}): Promise<OperationsDashboard> {
  return http.get<OperationsDashboard>('/manufacturing/operations', {
    stalled_hours: stalledHours, page: filters.page || 1, page_size: filters.page_size || 20, ...filters
  });
}

export function postStageAction(action: StageAction, data: {
  ticket_id: number;
  node_id: number;
  to_node_id?: number;
  quantity: number;
  request_id: string;
  source?: string;
  reason?: string;
}): Promise<QuantityLedger | QuantityLedger[]> {
  return http.post<QuantityLedger | QuantityLedger[]>(
    `/manufacturing/actions/${action}`, data as unknown as Record<string, unknown>);
}

export function fetchRouteTimeline(routeId: number, ticketId?: number): Promise<{
  route: number;
  ticket: number | null;
  events: QuantityLedger[];
  total: number;
  truncated: boolean;
}> {
  return http.get(`/manufacturing/routes/${routeId}/timeline`, { ticket_id: ticketId });
}

export function reverseLedger(id: number, requestId: string, reason: string): Promise<QuantityLedger> {
  return http.post<QuantityLedger>(`/manufacturing/ledger/${id}/reverse`, {
    request_id: requestId, reason
  });
}

export function fetchInspections(routeId: number): Promise<QualityInspection[]> {
  // 后端支持 ticket/node/status/type 过滤；路线由页面按返回 route 收敛。
  return http.get<Paginated<QualityInspection>>('/quality/inspections', { page_size: 100 })
    .then((res) => (res.results || []).filter((row) => row.route === routeId));
}

export function createInspection(data: {
  ticket: number;
  node: number;
  inspection_type: string;
  inspected: number;
  qualified: number;
  rework: number;
  scrapped: number;
  rework_node?: number;
  reinspection_of?: number;
  reason?: string;
  create_request_id?: string;
}): Promise<QualityInspection> {
  return http.post<QualityInspection>('/quality/inspections', data as unknown as Record<string, unknown>);
}

export function confirmInspection(id: number, requestId: string): Promise<QualityInspection> {
  return http.post<QualityInspection>(`/quality/inspections/${id}/confirm`, { request_id: requestId });
}

export function fetchHandovers(routeNodeIds: number[]): Promise<CuttingHandover[]> {
  return http.get<Paginated<CuttingHandover>>('/cutting/handovers/', { page_size: 100 })
    .then((res) => (res.results || []).filter((row) => routeNodeIds.indexOf(row.source_node) >= 0));
}

export function createHandover(data: {
  ticket_id: number;
  source_node_id: number;
  target_node_id: number;
  receiver_id: number;
  quantity: number;
  request_id: string;
  remark?: string;
}): Promise<CuttingHandover> {
  return http.post<CuttingHandover>('/cutting/handovers/', data as unknown as Record<string, unknown>);
}

export function signHandover(id: number): Promise<CuttingHandover> {
  return http.post<CuttingHandover>(`/cutting/handovers/${id}/sign/`);
}

export function cancelHandover(id: number): Promise<CuttingHandover> {
  return http.post<CuttingHandover>(`/cutting/handovers/${id}/cancel/`);
}

export function fetchPackingTasks(routeId: number): Promise<PackingTask[]> {
  return http.get<PackingTask[]>('/finished-goods/packing-tasks', { route_id: routeId });
}

export function fetchPackingTask(id: number): Promise<PackingTask> {
  return http.get<PackingTask>(`/finished-goods/packing-tasks/${id}`);
}

export function createPackingTask(data: {
  route_id: number;
  task_no?: string;
  boxes: Array<{ box_no: string; items: Array<{ ticket_id: number; quantity: number }> }>;
  request_id: string;
  remark?: string;
}): Promise<PackingTask> {
  return http.post<PackingTask>('/finished-goods/packing-tasks', data as unknown as Record<string, unknown>);
}

export function approvePackingTask(id: number, requestId: string): Promise<PackingTask> {
  return http.post<PackingTask>(`/finished-goods/packing-tasks/${id}/approve`, { request_id: requestId });
}

export function reversePackingTask(id: number, requestId: string, reason: string): Promise<PackingTask> {
  return http.post<PackingTask>(`/finished-goods/packing-tasks/${id}/reverse`, { request_id: requestId, reason });
}

export function fetchInbounds(routeId: number): Promise<FinishedInbound[]> {
  return http.get<FinishedInbound[]>('/finished-goods/inbounds', { route_id: routeId });
}

export function fetchInbound(id: number): Promise<FinishedInbound> {
  return http.get<FinishedInbound>(`/finished-goods/inbounds/${id}`);
}

export function createInbound(data: {
  route_id: number;
  document_no?: string;
  lines: Array<{ box_id: number; ticket_id: number; quantity: number }>;
  warehouse_name?: string;
  location_name?: string;
  request_id: string;
  remark?: string;
}): Promise<FinishedInbound> {
  return http.post<FinishedInbound>('/finished-goods/inbounds', data as unknown as Record<string, unknown>);
}

export function approveInbound(id: number, requestId: string): Promise<FinishedInbound> {
  return http.post<FinishedInbound>(`/finished-goods/inbounds/${id}/approve`, { request_id: requestId });
}

export function reverseInbound(id: number, requestId: string, reason: string): Promise<FinishedInbound> {
  return http.post<FinishedInbound>(`/finished-goods/inbounds/${id}/reverse`, { request_id: requestId, reason });
}

export function fetchFinishedInventory(params: {
  route_id?: number;
  bed_no?: string;
  color?: string;
  size?: string;
  include_zero?: boolean;
}): Promise<{ results: FinishedInventoryRow[]; total_available: number }> {
  const data = { ...params } as Record<string, unknown>;
  if (typeof data.include_zero === 'boolean') data.include_zero = data.include_zero ? '1' : '0';
  return http.get<{ results: FinishedInventoryRow[]; total_available: number }>('/finished-goods/inventory', data);
}

export function fetchTechPacks(orderId: number): Promise<TechPack[]> {
  return http.get<TechPack[]>('/preproduction/tech-packs', { order_id: orderId });
}

export function createTechPack(data: TechPackPayload & { order: number; version_no?: number }): Promise<TechPack> {
  return http.post<TechPack>('/preproduction/tech-packs', data as unknown as Record<string, unknown>);
}

export function updateTechPack(id: number, data: TechPackPayload): Promise<TechPack> {
  return http.put<TechPack>(`/preproduction/tech-packs/${id}`, data as unknown as Record<string, unknown>);
}

export function createSampleTask(packId: number, data: { title: string; assignee_id?: number }): Promise<SampleTask> {
  return http.post<SampleTask>(`/preproduction/tech-packs/${packId}/samples`, data as Record<string, unknown>);
}

export function recordSampleResult(taskId: number, data: {
  result: 'passed' | 'failed' | 'rework';
  note: string;
  attachments?: string[];
}): Promise<SampleRecord> {
  return http.post<SampleRecord>(`/preproduction/samples/${taskId}/records`, data as Record<string, unknown>);
}

export function createReviewIssue(packId: number, data: { title: string; detail: string }): Promise<ReviewIssue> {
  return http.post<ReviewIssue>(`/preproduction/tech-packs/${packId}/issues`, data as Record<string, unknown>);
}

export function rectifyReviewIssue(id: number, rectification: string): Promise<ReviewIssue> {
  return http.put<ReviewIssue>(`/preproduction/issues/${id}`, { action: 'rectify', rectification });
}

export function reviewReviewIssue(id: number, close: boolean, reviewNote: string): Promise<ReviewIssue> {
  return http.put<ReviewIssue>(`/preproduction/issues/${id}`, { action: 'review', close, review_note: reviewNote });
}

export function addBriefingTarget(packId: number, employee: number, note: string): Promise<BriefingTarget> {
  return http.post<BriefingTarget>(`/preproduction/tech-packs/${packId}/briefing-targets`, { employee, note });
}

export function signBriefingTarget(id: number, remark: string): Promise<BriefingReceipt> {
  return http.post<BriefingReceipt>(`/preproduction/briefing-targets/${id}/sign`, { remark });
}

export function resolveReviewIssue(id: number, resolution: string): Promise<ReviewIssue> {
  return http.put<ReviewIssue>(`/preproduction/issues/${id}`, { action: 'resolve', resolution });
}

export function reopenReviewIssue(id: number, reason = ''): Promise<ReviewIssue> {
  return http.put<ReviewIssue>(`/preproduction/issues/${id}`, { action: 'reopen', reason });
}

export function confirmBriefing(packId: number, note: string): Promise<BriefingConfirmation> {
  return http.post<BriefingConfirmation>(
    `/preproduction/tech-packs/${packId}/briefing-confirmation`, { note });
}

export function fetchPreproductionStatus(orderId: number): Promise<PreproductionStatus> {
  return http.get<PreproductionStatus>(`/preproduction/orders/${orderId}/status`);
}

export function decideTechPack(id: number, approved: boolean, reason = ''): Promise<unknown> {
  return http.post(`/preproduction/tech-packs/${id}/approval`, {
    action: approved ? 'approve' : 'reject', reason
  });
}
