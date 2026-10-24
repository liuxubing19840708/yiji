/**
 * 请求层（唯一出口）：自动携带 JWT 与公司上下文，统一错误提示，401 重登录，
 * 网络失败可入离线队列（登记类接口用，见 utils/offline-queue.ts）。
 *
 * 页面一律 import services/*，不要直接用 wx.request。
 */
import { BASE_URL } from './config';
import { getToken, getCompanyId, clearAuth } from './auth';
import { enqueueOffline } from './offline-queue';
import { logger } from './logger';

type Method = 'GET' | 'POST' | 'PUT' | 'DELETE'; // wx.request 运行时不支持 PATCH

export interface RequestOptions {
  url: string;
  method?: Method;
  data?: Record<string, unknown> | null;
  /** 关闭自动错误 toast（页面想自己处理错误时） */
  silent?: boolean;
  /** 弱网时入离线队列（仅登记类 POST），重试以 Idempotency-Key 幂等 */
  offline?: boolean;
}

export class ApiError extends Error {
  status: number;
  data: Record<string, unknown> | null;

  constructor(status: number, message: string, data: Record<string, unknown> | null) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

/** 从统一错误格式中提取提示：{message} / {detail} / {error} / {字段: [错误]} */
function extractMessage(data: unknown, fallback: string): string {
  if (!data || typeof data !== 'object') {
    return fallback;
  }
  const obj = data as Record<string, unknown>;
  if (typeof obj.message === 'string') return obj.message;
  if (typeof obj.detail === 'string') return obj.detail;
  if (typeof obj.error === 'string') return obj.error;
  for (const key of Object.keys(obj)) {
    const val = obj[key];
    if (Array.isArray(val) && val.length && typeof val[0] === 'string') {
      return `${val[0]}`;
    }
  }
  return fallback;
}

function toast(msg: string): void {
  wx.showToast({ title: msg.length > 20 ? msg.slice(0, 20) + '…' : msg, icon: 'none' });
}

/** GET 查询参数清洗：wx.request 会把 undefined 序列化成字面量 "undefined" 传给后端（坑：int 参数 500） */
function cleanParams(data: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(data)) {
    if (data[key] !== undefined && data[key] !== null) {
      out[key] = data[key];
    }
  }
  return out;
}

function redirectToLogin(): void {
  clearAuth();
  wx.reLaunch({ url: '/pages/login/index' });
}

export function request<T = Record<string, unknown>>(options: RequestOptions): Promise<T> {
  const { url, method = 'GET', data = null, silent = false, offline = false } = options;
  const header: Record<string, string> = {
    'Content-Type': 'application/json'
  };
  const token = getToken();
  if (token) {
    header.Authorization = `Bearer ${token}`;
  }
  const companyId = getCompanyId();
  if (companyId) {
    header['X-Company-Id'] = String(companyId);
  }
  // 幂等键：离线补发时后端可依此去重（当前后端以记录唯一约束兜底）
  if (method !== 'GET' && offline) {
    header['Idempotency-Key'] = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  }

  return new Promise<T>((resolve, reject) => {
    wx.request({
      url: `${BASE_URL}${url}`,
      method,
      data: method === 'GET' && data ? cleanParams(data) : (data || undefined),
      header,
      timeout: 15000,
      success(res) {
        const status = res.statusCode || 0;
        const body = res.data;
        if (status >= 200 && status < 300) {
          resolve(body as T);
          return;
        }
        if (status === 401) {
          redirectToLogin();
          reject(new ApiError(status, '登录已过期，请重新登录', null));
          return;
        }
        const msg = extractMessage(body, `请求失败（${status}）`);
        // 接口错误上报（跳过日志接口自身防循环；401 已重定向不再记）
        if (status !== 401 && !url.startsWith('/client-logs')) {
          logger.warn('接口错误', `${method} ${url} ${status} ${msg}`);
        }
        if (!silent) {
          toast(msg);
        }
        reject(new ApiError(status, msg, (body as Record<string, unknown>) || null));
      },
      fail() {
        if (offline && method === 'POST') {
          enqueueOffline({ url, method, data, header });
          if (!silent) {
            toast('网络不稳定，已存入本地待同步');
          }
          reject(new ApiError(0, '网络失败，已入离线队列', null));
          return;
        }
        const msg = '网络异常，请检查网络';
        if (!url.startsWith('/client-logs')) {
          logger.warn('网络失败', `${method} ${url}`);
        }
        if (!silent) {
          toast(msg);
        }
        reject(new ApiError(0, msg, null));
      }
    });
  });
}

export const http = {
  get: <T = Record<string, unknown>>(url: string, params?: Record<string, unknown>, silent = false) =>
    request<T>({ url, method: 'GET', data: params, silent }),
  post: <T = Record<string, unknown>>(url: string, data?: Record<string, unknown>, opts?: { silent?: boolean; offline?: boolean }) =>
    request<T>({ url, method: 'POST', data: data || null, silent: opts?.silent, offline: opts?.offline }),
  put: <T = Record<string, unknown>>(url: string, data?: Record<string, unknown>, silent = false) =>
    request<T>({ url, method: 'PUT', data: data || null, silent }),
  del: <T = Record<string, unknown>>(url: string, silent = false) =>
    request<T>({ url, method: 'DELETE', silent })
};
