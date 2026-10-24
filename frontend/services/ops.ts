/**
 * 运营支撑接口（ops）：公司设置、操作日志、站内通知。
 */
import { http } from '../utils/request';
import { CompanySettings, AuditLog, ClientLogItem, Notification, Paginated } from './types';

// ============ 公司设置 ============

/** 一次拉取全部设置（13 项开关，见规格 §15） */
export function fetchSettings(): Promise<CompanySettings> {
  return http.get<CompanySettings>('/settings');
}

/** 批量保存设置 */
export function saveSettings(data: CompanySettings): Promise<CompanySettings> {
  return http.put<CompanySettings>('/settings', data);
}

// ============ 操作日志（管理员） ============

/** type: price/assist/qty/process/revoke/other；date 单日或 date_from/date_to 区间；keyword 模糊；
 *  scope=mine：员工视角「与我相关的变更」（target_user 或 operator 是本人），全量视图仅管理员 */
export function fetchAuditLogs(params?: {
  type?: string; date?: string; date_from?: string; date_to?: string; keyword?: string; scope?: string;
  page?: number; page_size?: number;
}): Promise<Paginated<AuditLog>> {
  return http.get<Paginated<AuditLog>>('/audit-logs', params);
}

// ============ 客户端日志（管理员，厂→用户隔离） ============

/** level: info/warn/error；date_from/date_to 区间；user_id 下钻某员工 */
export function fetchClientLogs(params?: {
  user_id?: number; level?: string; date_from?: string; date_to?: string;
  page?: number; page_size?: number;
}): Promise<Paginated<ClientLogItem>> {
  return http.get<Paginated<ClientLogItem>>('/client-logs', params);
}

// ============ 站内通知 ============

/** is_read 用 0/1（后端字符串比较，wx.request 会把 boolean 序列化成 "true" 而被忽略） */
export function fetchNotifications(params?: { is_read?: 0 | 1; page?: number; page_size?: number }): Promise<Paginated<Notification>> {
  const p = { ...(params || {}) } as Record<string, unknown>;
  if (p.is_read !== undefined) {
    p.is_read = String(p.is_read);
  }
  return http.get<Paginated<Notification>>('/notifications', p);
}

export function markNotificationRead(id: number): Promise<unknown> {
  return http.post(`/notifications/${id}/read`);
}

export function markAllNotificationsRead(): Promise<{ message: string }> {
  return http.post('/notifications/read-all');
}
