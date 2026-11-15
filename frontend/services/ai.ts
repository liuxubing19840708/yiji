/**
 * AI 问报与受治理动态报表接口（后端 apps/ai_report）。
 * LLM 只负责意图解析；动态报表的数字全部由 ORM 白名单计划产出。
 */
import { http } from '../utils/request';
import { BASE_URL } from '../utils/config';
import { getToken, getCompanyId } from '../utils/auth';

/** 报表卡片结构：表头 + 行 + 汇总 + 口径说明 */
export interface AiReportCard {
  title: string;
  summary: string;
  headers: string[];
  rows: Array<Array<string | number | boolean | null>>;
  note: string;
  columns?: AiReportColumn[];
  scope?: string[];
}

export interface AiQueryResult {
  fallback: boolean;
  quick_questions?: string[];
  intent?: { report_key: string } | { free: true };
  report?: AiReportCard;
  plan?: AiQueryPlan;
  mode?: string;
  free?: boolean;
  answer?: string;
}

export interface AiHistoryTurn {
  role: 'user' | 'ai';
  content: string;
}

export function queryAiReport(question: string, history?: AiHistoryTurn[]) {
  return http.post<AiQueryResult>('/ai/report/query', {
    question, history: history || [], mode: 'query_plan'
  });
}

export interface AiChoice {
  value: string | number | boolean;
  label: string;
}

export interface AiFieldSpec {
  key: string;
  label: string;
  type: string;
  can_dimension: boolean;
  can_filter: boolean;
  operators: string[];
  choices?: AiChoice[];
}

export interface AiMetricSpec {
  key: string;
  label: string;
  type: string;
  aggregations: string[];
  default_aggregation: string;
  requires_unit: boolean;
}

export interface AiDatasetSpec {
  key: string;
  label: string;
  description: string;
  note: string;
  time_field: string;
  time_label: string;
  deleted_label: string;
  unit_dimension: string;
  supports_as_of: boolean;
  supports_include_deleted: boolean;
  fields: AiFieldSpec[];
  metrics: AiMetricSpec[];
  default_dimensions: string[];
  default_metrics: string[];
}

export interface AiCatalog {
  version: string;
  datasets: AiDatasetSpec[];
  capabilities: { full_data: boolean; export: boolean; share: boolean };
  limits: { preview_rows: number; export_rows: number };
}

export interface AiMetricChoice {
  key: string;
  aggregation: string;
}

export interface AiPlanFilter {
  field: string;
  operator: string;
  value: unknown;
}

export interface AiQueryPlan {
  dataset: string;
  metrics: AiMetricChoice[];
  dimensions: string[];
  filters: AiPlanFilter[];
  order_by: Array<{ field: string; direction: 'asc' | 'desc' }>;
  limit: number;
  include_deleted?: boolean;
  presentation: 'table' | 'bar' | 'line';
  comparison?: string;
  as_of?: string;
}

export interface AiReportColumn {
  key: string;
  label: string;
  type: string;
  choices?: AiChoice[];
}

export interface AiExecuteResult {
  mode: 'query_plan';
  catalog_version: string;
  execution_id: string | null;
  plan: AiQueryPlan;
  report: AiReportCard & { columns: AiReportColumn[]; scope: string[] };
}

export interface AiSavedReport {
  id: number;
  name: string;
  dataset: string;
  plan: AiQueryPlan;
  visibility: string;
  is_owner: boolean;
  owner: { id: number; name: string };
}

export interface AiExportTask {
  id: string;
  status: 'pending' | 'running' | 'success' | 'failed' | 'expired';
  file_name: string;
  error_message: string;
  total_count: number;
  download_url: string | null;
}

export interface AiSchedule {
  id: number;
  name: string;
  saved_report_id: number | null;
  saved_report: { id: number; name: string } | null;
  plan: AiQueryPlan;
  frequency: 'daily' | 'weekly' | 'monthly';
  hour: number;
  minute: number;
  weekday: number | null;
  day_of_month: number | null;
  timezone: string;
  next_run_at: string | null;
  enabled: boolean;
}

export interface AiScheduleRun {
  id: number;
  schedule_id: number;
  schedule_name: string;
  kind: 'natural' | 'manual' | 'retry';
  scheduled_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  status: 'pending' | 'running' | 'success' | 'denied' | 'invalid' | 'failed';
  outcome: string;
  error_code: string;
  error_summary: string;
  retry_of_id: number | null;
}

export function fetchAiCatalog() {
  return http.get<AiCatalog>('/ai/report/catalog', undefined, true);
}

export function executeAiPlan(plan: AiQueryPlan) {
  return http.post<AiExecuteResult>('/ai/report/execute', { plan: plan as unknown as Record<string, unknown> }, { silent: true });
}

export function fetchSavedAiReports() {
  return http.get<{ results: AiSavedReport[] }>('/ai/report/saved', undefined, true);
}

export function createSavedAiReport(name: string, plan: AiQueryPlan) {
  return http.post<AiSavedReport>('/ai/report/saved', {
    name, plan: plan as unknown as Record<string, unknown>
  }, { silent: true });
}

export function createAiExportTask(plan: AiQueryPlan) {
  return http.post<AiExportTask>('/ai/report/exports', {
    plan: plan as unknown as Record<string, unknown>
  }, { silent: true });
}

export function fetchAiExportTasks() {
  return http.get<{ results: AiExportTask[] }>('/ai/report/exports', undefined, true);
}

export function downloadAiExportTask(id: string): Promise<void> {
  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url: `${BASE_URL}/ai/report/exports/${id}/download`,
      header: {
        Authorization: `Bearer ${getToken()}`,
        'X-Company-Id': String(getCompanyId())
      },
      success: (res) => {
        if (res.statusCode !== 200) {
          reject(new Error(res.statusCode === 410 ? '导出文件已过期，请重新导出' : `下载失败（${res.statusCode}）`));
          return;
        }
        wx.openDocument({
          filePath: res.tempFilePath, fileType: 'xlsx', showMenu: true,
          success: () => resolve(),
          fail: () => reject(new Error('文件已下载，但当前环境无法打开'))
        });
      },
      fail: () => reject(new Error('网络异常，导出文件下载失败'))
    });
  });
}

export interface AiScheduleInput {
  name: string;
  frequency: 'daily' | 'weekly' | 'monthly';
  hour: number;
  minute: number;
  enabled: boolean;
  weekday?: number;
  day_of_month?: number;
  saved_report_id?: number;
  plan?: AiQueryPlan;
}

export function fetchAiSchedules() {
  return http.get<{ results: AiSchedule[] }>('/ai/report/schedules', undefined, true);
}

export function createAiSchedule(data: AiScheduleInput) {
  return http.post<AiSchedule>('/ai/report/schedules', data as unknown as Record<string, unknown>, { silent: true });
}

export function setAiScheduleEnabled(id: number, enabled: boolean) {
  // 小程序 wx.request 不支持 PATCH，统一使用后端同等支持的 PUT。
  return http.put<AiSchedule>(`/ai/report/schedules/${id}`, { enabled }, true);
}

export function deleteAiSchedule(id: number) {
  return http.del<void>(`/ai/report/schedules/${id}`, true);
}

export function fetchAiScheduleRuns(limit = 50) {
  return http.get<{ results: AiScheduleRun[] }>('/ai/report/schedules/runs', { limit }, true);
}

/** 每次用户操作生成独立 UUID；同一次请求的重复提交由后端 request_id 去重。 */
export function createAiRequestId(): string {
  const hex = '0123456789abcdef';
  let uuid = '';
  for (let i = 0; i < 36; i += 1) {
    if (i === 8 || i === 13 || i === 18 || i === 23) uuid += '-';
    else if (i === 14) uuid += '4';
    else if (i === 19) uuid += hex[(Math.floor(Math.random() * 4) + 8)];
    else uuid += hex[Math.floor(Math.random() * 16)];
  }
  return uuid;
}

export function runAiSchedule(id: number, requestId: string) {
  return http.post<AiScheduleRun>(`/ai/report/schedules/${id}/run`, { request_id: requestId }, { silent: true });
}

export function retryAiScheduleRun(id: number, requestId: string) {
  return http.post<AiScheduleRun>(`/ai/report/schedules/runs/${id}/retry`, { request_id: requestId }, { silent: true });
}
