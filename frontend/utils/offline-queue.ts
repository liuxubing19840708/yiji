/**
 * 离线队列：车间弱网时登记失败的请求落盘，onLaunch/onShow 补发。
 * 只收登记类 POST（request.ts 控制），请求头已带 Idempotency-Key 幂等。
 */
import { BASE_URL } from './config';

const KEY_QUEUE = 'yisao_offline_queue';

export interface OfflineItem {
  url: string;
  method: 'POST';
  data: Record<string, unknown> | null;
  header: Record<string, string>;
  ts: number;
}

function load(): OfflineItem[] {
  return wx.getStorageSync(KEY_QUEUE) || [];
}

function save(items: OfflineItem[]): void {
  wx.setStorageSync(KEY_QUEUE, items);
}

export function enqueueOffline(item: Omit<OfflineItem, 'ts'>): void {
  const items = load();
  items.push({ ...item, ts: Date.now() });
  // 防积压：最多 200 条，丢弃最旧的
  save(items.slice(-200));
}

export function offlineCount(): number {
  return load().length;
}

/** 显式暂存列表（P3 增强）：只读快照，供扫菲页查看条目摘要 */
export function offlineItems(): OfflineItem[] {
  return load();
}

/** 逐条补发；成功的移除，失败的保留（下次再试）。由 app.onLaunch/onShow 调用。 */
export function flushOfflineQueue(): void {
  const items = load();
  if (!items.length) {
    return;
  }
  let pending = items.length;
  const itemsCopy = [...items];
  const settle = (success: boolean, item: OfflineItem) => {
    if (success) {
      const idx = itemsCopy.indexOf(item);
      if (idx >= 0) {
        itemsCopy.splice(idx, 1);
      }
    }
    pending -= 1;
    if (pending === 0) {
      save(itemsCopy);
    }
  };
  itemsCopy.forEach((item) => {
    wx.request({
      url: `${BASE_URL}${item.url}`,
      method: 'POST',
      data: item.data || undefined,
      header: item.header,
      timeout: 15000,
      success: (res) => settle(!!res.statusCode && res.statusCode < 300, item),
      fail: () => settle(false, item)
    });
  });
}
