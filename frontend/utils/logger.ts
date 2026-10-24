/**
 * 客户端日志（两条通道，厂→用户隔离）：
 * 1. 微信实时日志 wx.getRealtimeLogManager —— 即时写微信后台，小程序管理页
 *    「运维中心→实时日志」按用户/OpenID 查询，免费、不经自有服务器；
 * 2. 自建上报 POST /client-logs —— 本地队列攒批（≥5 条或立即级触发），
 *    登录态自动带 company/user（后端按厂隔离存储），失败留队列下次补传。
 *
 * 原则：日志绝不打断业务——全部 try/catch 静默；上报用原生 wx.request，
 * 不走 utils/request.ts（避免循环依赖与其 toast/401 逻辑）。
 */
import { BASE_URL } from './config';
import { getCompanyId, getToken } from './auth';

type Level = 'info' | 'warn' | 'error';

interface LogEntry {
  level: Level;
  page: string;
  message: string;
  detail: string;
  /** 记录现场的厂标（批量补传时不串厂；后端校验成员关系后才落款） */
  company_id: number;
}

const QUEUE_KEY = 'yisao_log_queue';
const MAX_QUEUE = 50;      // 本地队列上限（防无限膨胀）
const FLUSH_AT = 5;        // 队列满 5 条触发上报
const BATCH_MAX = 20;      // 与后端约定单批上限

/** 实时日志管理器单例（基础库 2.7.1+；低版本/异常时返回 null 降级） */
let rtManager: { info: (...a: unknown[]) => void; warn: (...a: unknown[]) => void; error: (...a: unknown[]) => void } | null | undefined;

function realtime(): typeof rtManager {
  if (rtManager === undefined) {
    try {
      rtManager = typeof wx.getRealtimeLogManager === 'function' ? wx.getRealtimeLogManager() : null;
    } catch (e) {
      rtManager = null;
    }
  }
  return rtManager;
}

function currentPage(): string {
  try {
    const pages = getCurrentPages();
    return pages.length ? (pages[pages.length - 1] as { route?: string }).route || '' : '';
  } catch (e) {
    return '';
  }
}

function readQueue(): LogEntry[] {
  try {
    return (wx.getStorageSync(QUEUE_KEY) as LogEntry[]) || [];
  } catch (e) {
    return [];
  }
}

function writeQueue(q: LogEntry[]): void {
  try {
    wx.setStorageSync(QUEUE_KEY, q);
  } catch (e) { /* storage 满等异常：放弃最旧日志也不影响业务 */ }
}

function record(level: Level, message: string, detail?: string): void {
  const entry: LogEntry = {
    level,
    page: currentPage(),
    message: String(message).slice(0, 200),
    detail: detail === undefined ? '' : String(detail).slice(0, 3000),
    company_id: getCompanyId() || 0
  };

  // 通道一：微信实时日志（即时）
  const rt = realtime();
  if (rt) {
    try {
      rt[level]('[衣计]', entry.page, entry.message, entry.detail);
    } catch (e) { /* 实时日志异常静默 */ }
  }

  // 通道二：入队待上报
  const q = readQueue();
  q.push(entry);
  while (q.length > MAX_QUEUE) q.shift();
  writeQueue(q);

  if (q.length >= FLUSH_AT || level === 'error') {
    flushLogs();
  }
}

let flushing = false;

/** 把本地队列批量上报到服务端（厂→用户隔离由后端按登录态+公司头落款） */
export function flushLogs(): void {
  if (flushing) return;
  const batch = readQueue();
  if (!batch.length) return;
  const body = batch.slice(0, BATCH_MAX).map((e) => ({
    level: e.level, page: e.page, message: e.message, detail: e.detail,
    company_id: e.company_id || undefined
  }));
  const token = getToken();
  flushing = true;
  try {
    wx.request({
      url: `${BASE_URL}/client-logs`,
      method: 'POST',
      data: body,
      timeout: 10000,
      header: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {})
      },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          // 只移除已发送的前 N 条（发送期间新日志继续追加在尾部）
          const cur = readQueue();
          writeQueue(cur.slice(body.length));
        }
        // 失败保留队列，下次 flushLogs 再试
      },
      complete() {
        flushing = false;
      }
    });
  } catch (e) {
    flushing = false;
  }
}

/** 清空本地待上报队列（测试/登出场景用） */
export function clearLogs(): void {
  writeQueue([]);
}

export const logger = {
  info(message: string, detail?: string): void {
    record('info', message, detail);
  },
  warn(message: string, detail?: string): void {
    record('warn', message, detail);
  },
  error(message: string, detail?: string): void {
    record('error', message, detail);
  }
};
