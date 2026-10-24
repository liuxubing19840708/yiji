/**
 * 组织与员工接口（员工管理扩展）：邀请员工、指定工序、注销账号。
 * 端点以后端 apps/accounts/urls.py 为准；wx.request 无 PATCH，新端点一律 POST/PUT。
 */
import { http } from '../utils/request';
import { Employee } from './types';

/** 员工数据 + 指定工序（后端 EmployeeListSerializer 已返回 assigned_processes；types.ts 冻结故在此扩展） */
export interface EmployeeWithProcesses extends Employee {
  assigned_processes?: number[];
}

export interface InviteResult {
  message: string;
  employee: EmployeeWithProcesses;
}

/** 邀请员工（POST /employees/invite）：手机号 + 姓名（可选）+ 岗位（可选），创建待审核成员 */
export function inviteEmployee(phone: string, realName: string, postId?: number | null): Promise<InviteResult> {
  const data: Record<string, unknown> = { phone, real_name: realName || '' };
  if (postId) {
    data.post_id = postId;
  }
  return http.post<InviteResult>('/employees/invite', data);
}

/** 保存指定工序（PUT /employees/{id}/assigned-processes）：返回更新后的员工数据 */
export function setAssignedProcesses(employeeId: number, processIds: number[]): Promise<EmployeeWithProcesses> {
  return http.put<EmployeeWithProcesses>(`/employees/${employeeId}/assigned-processes`, { process_ids: processIds });
}

/**
 * 注销账号（POST /auth/deactivate，本人登录态）：
 * 设置过密码时需携带 password 二次校验；最后一个在职管理员会被后端 400 拒绝。
 * 成功后本地登录态已失效（token 被后端拒收），调用方应 clearAuth 并回登录页。
 */
export function deactivateAccount(password?: string): Promise<{ message: string }> {
  return http.post<{ message: string }>('/auth/deactivate', password ? { password } : {});
}
