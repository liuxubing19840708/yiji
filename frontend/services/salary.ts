/**
 * 薪酬结算接口（salary）。
 */
import { http } from '../utils/request';
import {
  SalaryCompanySummary, EmployeeSalaryDetail, Settlement, SalaryAdjust,
  SettleApply, CompletionSummary, Paginated
} from './types';

// ============ 本分包补充类型（types.ts 冻结，勿动，缺字段在此扩展） ============

/** 公司汇总的「总工资」（计件未结 + 固定预发，后端合计） */
export interface SalaryCompanySummaryFull extends SalaryCompanySummary {
  total: string;
}

/** 员工薪酬明细的头部已结/未结（全历史，record.settlement 快照口径） */
export interface EmployeeSalaryFull extends EmployeeSalaryDetail {
  settled: string;
  unsettled: string;
}

/** tab=count：只有数量，不含金额 */
export interface EmployeeSalaryCount {
  period: { date_from: string | null; date_to: string | null };
  by_date: Array<{ date: string; qty: number }>;
  by_bed: Array<{ production_id: number; bed_no: string; style_no: string; style_name: string; qty: number }>;
  by_process: Array<{ process_id: number; name: string; qty: number }>;
  summary: { qty: number; ticket_count: number };
}

/** 汇总查看行（2026-10-22 参考竞品：按款式聚合 完成件数/单价/金额） */
export interface EmployeeSalaryStyleRow {
  style_id: number;
  style_no: string;
  style_name: string;
  image: string;
  qty: number;
  amount: string;
  avg_price: string;
}

/** 员工明细扩展（2026-10-22 改版：by_date 补扎数、by_bed 补床次总数/已结算、新增 by_style，summary 补工序数，fixed 随默认下发） */
export interface EmployeeSalaryFullExt extends EmployeeSalaryFull {
  by_date: Array<{ date: string; qty: number; amount: string; tickets: number }>;
  by_bed: Array<{
    production_id: number; bed_no: string; style_no: string; style_name: string;
    image: string; bed_total: number; qty: number; amount: string; settled: string;
  }>;
  by_style: EmployeeSalaryStyleRow[];
  summary: EmployeeSalaryDetail['summary'] & { processes: number; no_price_count: number };
  /** 工资计算方式（四类工资制）：fixed/whole/process/commission + 中文名 */
  salary_type?: 'fixed' | 'whole' | 'process' | 'commission';
  salary_type_display?: string;
}

/** count 版扩展（无金额字段） */
export interface EmployeeSalaryCountExt extends EmployeeSalaryCount {
  by_date: Array<{ date: string; qty: number; tickets: number }>;
  by_bed: Array<{
    production_id: number; bed_no: string; style_no: string; style_name: string;
    image: string; bed_total: number; qty: number; settled: string;
  }>;
  by_style: EmployeeSalaryStyleRow[];
  summary: EmployeeSalaryCount['summary'] & { processes: number; no_price_count: number };
}

/** GET /salary/company/employees：员工薪酬卡（规格 §10.1） */
export interface SalaryEmployeeCard {
  user_id: number;
  real_name: string;
  post_name: string;
  /** 成员状态（1 在职 / 2 离职；2026-10-18 起薪酬卡并入离职成员） */
  status?: number;
  status_display?: string;
  /** 全历史未结总额 */
  unsettled: string;
  /** 期间内完成件数 */
  done_qty: number;
  /** 期间内未结工资额（工资结算确认页用） */
  period_amount: string;
  /** 2026-10-22 参考竞品改版补字段 */
  /** 全历史已结总额 */
  settled: string;
  /** 期间未结计件 */
  piece_amount: string;
  /** 期间固定工资 */
  fixed_amount: string;
  /** 计件 + 固定 */
  total_amount: string;
  /** 期间完成工序数（去重） */
  process_count: number;
  /** 期间无单价工序数 */
  no_price_count: number;
}

/** 完成汇总第三层员工行（types.ts 的 CompletionSummary.employees 之外补单价） */
export interface CompletionEmployeeRow {
  user_id: number;
  user_name: string;
  qty: number;
  amount: string;
  avg_price: string;
}

// ============ 汇总 ============

/** 公司工资汇总（管理员）。quick: today / yesterday / month */
export function fetchSalarySummary(params?: { date_from?: string; date_to?: string; quick?: string }): Promise<SalaryCompanySummaryFull> {
  return http.get<SalaryCompanySummaryFull>('/salary/company', params);
}

/** 公司员工薪酬卡列表（管理员），按未结降序 */
export function fetchCompanyEmployees(params?: { date_from?: string; date_to?: string; quick?: string }): Promise<SalaryEmployeeCard[]> {
  return http.get<SalaryEmployeeCard[]>('/salary/company/employees', params);
}

/** 员工工资明细（本人或管理员）；tab=fixed 固定工资概览 / tab=count 只显数量（响应含 by_style/fixed/工序数扩展） */
export function fetchEmployeeSalary(
  uid: number,
  params?: { date_from?: string; date_to?: string; quick?: string; tab?: 'fixed' | 'count' }
): Promise<EmployeeSalaryFullExt> {
  return http.get<EmployeeSalaryFullExt>(`/salary/employee/${uid}`, params);
}

/** 员工完成计数（tab=count，只显数量不含金额） */
export function fetchEmployeeSalaryCount(
  uid: number,
  params?: { date_from?: string; date_to?: string; quick?: string }
): Promise<EmployeeSalaryCountExt> {
  return http.get<EmployeeSalaryCountExt>(`/salary/employee/${uid}`, { ...params, tab: 'count' });
}

/** 设置员工工资配置（管理员，四类工资制：salary_type 四选一 + 对应参数） */
export function updateEmployeeConfig(
  uid: number,
  data: {
    fixed_salary?: string;
    fixed_type?: 'day' | 'month';
    salary_type?: 'fixed' | 'whole' | 'process' | 'commission';
    piece_rate?: string;
    commission_rate?: string;
  },
): Promise<Record<string, unknown>> {
  return http.put(`/salary/employee/${uid}/config`, data);
}

// ============ 工资调整 ============

export function fetchAdjusts(params?: { user_id?: number; date_from?: string; date_to?: string; page?: number; page_size?: number }): Promise<Paginated<SalaryAdjust>> {
  return http.get<Paginated<SalaryAdjust>>('/salary/adjust', params);
}

export function createAdjust(user_id: number, amount: string, reason: string): Promise<SalaryAdjust> {
  return http.post<SalaryAdjust>('/salary/adjust', { user_id, amount, reason });
}

// ============ 结算单（管理员） ============

export function fetchSettlements(params?: { user_id?: number; date_from?: string; date_to?: string; page?: number; page_size?: number }): Promise<Paginated<Settlement>> {
  return http.get<Paginated<Settlement>>('/salary/settlements', params);
}

/** 发起结算：把期间内未结算登记锁定为结算单（全员或指定员工） */
export function createSettlement(period_start: string, period_end: string, user_id?: number): Promise<Settlement> {
  return http.post<Settlement>('/salary/settlements', { period_start, period_end, user_id });
}

/** 撤销结算单（解锁登记记录；期间已发放则拒绝） */
export function revokeSettlement(id: number): Promise<{ id: number; status: string }> {
  return http.post<{ id: number; status: string }>(`/salary/settlements/${id}/revoke`);
}

// ============ 结算申请流 ============

export function fetchSettleApplies(params?: { user_id?: number; status?: number; date_from?: string; date_to?: string; page?: number; page_size?: number }): Promise<Paginated<SettleApply>> {
  return http.get<Paginated<SettleApply>>('/salary/settle-applies', params);
}

/** 员工发起结算申请 */
export function createSettleApply(period_start: string, period_end: string, note?: string): Promise<SettleApply> {
  return http.post<SettleApply>('/salary/settle-applies', { period_start, period_end, note });
}

/** 审核：action = approve / reject */
export function reviewSettleApply(id: number, action: 'approve' | 'reject', reason?: string): Promise<SettleApply> {
  return http.put<SettleApply>(`/salary/settle-applies/${id}/review`, { action, reason });
}

/** 发放（生成结算单并锁定登记） */
export function paySettleApply(id: number, payer?: string): Promise<SettleApply> {
  return http.put<SettleApply>(`/salary/settle-applies/${id}/pay`, { payer });
}

/** 作废（管理员：待审/待发；员工：本人待审核） */
export function voidSettleApply(id: number, reason?: string): Promise<SettleApply> {
  return http.put<SettleApply>(`/salary/settle-applies/${id}/void`, { reason });
}

// ============ 完成情况三级钻取 ============

export function fetchCompletion(params?: {
  date_from?: string; date_to?: string; quick?: string;
  user_id?: number; customer_id?: number;
  style_id?: number; process_id?: number;
}): Promise<CompletionSummary> {
  return http.get<CompletionSummary>('/salary/summary/completion', params);
}
