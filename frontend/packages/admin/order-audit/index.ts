/**
 * 订单变更留痕时间线（差距分析 #3，2026-11-03）：
 * GET /orders/{id}/audit-logs（挂 orders.view）——订单域审计按「【订单号】」文案锚定
 * 聚合：谁（操作人）、何时、改了什么（类型 + 服务端已插值的展示文案）、旧值→新值
 * （改价等敏感操作带 before/after JSON）。
 * 权限：orders.view 即可看（只读；无点防御态）。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { fetchOrderAuditLogs } from '../../../services/orders';
import { AuditLog } from '../../../services/types';

interface LogVM {
  id: number;
  typeText: string;
  typeClass: string;
  operatorName: string;
  targetName: string;
  message: string;
  time: string;
  /** before/after 摘要（改价等敏感操作有值；JSON 转短文案） */
  diffText: string;
}

const TYPE_CLASS: Record<string, string> = {
  price: 'badge-red',
  qty: 'badge-blue',
  process: 'badge-blue',
  revoke: 'badge-red',
  assist: 'badge-green',
  other: 'badge-gray'
};

/** before/after JSON → 一行摘要（取键值对平铺，最多 3 组防长文） */
function diffSummary(log: AuditLog): string {
  const parts: string[] = [];
  const dump = (label: string, v: unknown) => {
    if (v === null || v === undefined) return;
    if (typeof v === 'object') {
      const flat = Object.entries(v as Record<string, unknown>)
        .slice(0, 3)
        .map(([k, val]) => `${k}:${typeof val === 'object' ? JSON.stringify(val) : String(val)}`);
      if (flat.length) parts.push(`${label} {${flat.join('，')}}`);
    } else {
      parts.push(`${label} ${String(v)}`);
    }
  };
  dump('前', (log as unknown as { before?: unknown }).before);
  dump('后', (log as unknown as { after?: unknown }).after);
  return parts.join('；');
}

function toVM(l: AuditLog): LogVM {
  return {
    id: l.id,
    typeText: l.type_display || l.type,
    typeClass: TYPE_CLASS[l.type] || 'badge-gray',
    operatorName: l.operator_name || '—',
    targetName: l.target_user_name || '',
    message: l.message || '',
    time: (l.created_at || '').replace('T', ' ').slice(0, 16),
    diffText: diffSummary(l)
  };
}

Page({
  data: {
    denied: false,
    orderId: 0,
    rows: [] as LogVM[],
    loading: false,
    error: false,
    page: 1,
    hasMore: false,
    loaded: false
  },

  onLoad(options: Record<string, string | undefined>) {
    this.setData({ orderId: Number(options.id) || 0 });
  },

  onShow() {
    if (!requireLogin()) return;
    const denied = !hasPerm('orders.view');
    this.setData({ denied });
    if (!denied && !this.data.loaded) this.reload();
  },

  onPullDownRefresh() {
    this.reload();
    wx.stopPullDownRefresh();
  },

  onReachBottom() {
    if (this.data.hasMore && !this.data.loading) this.loadPage(this.data.page + 1);
  },

  reload() {
    this.loadPage(1);
  },

  loadPage(page: number) {
    if (!this.data.orderId) {
      this.setData({ error: true, loaded: true });
      return;
    }
    this.setData({ loading: true, error: false });
    fetchOrderAuditLogs(this.data.orderId, { page, page_size: 20 })
      .then((res) => {
        const vms = (res.results || []).map(toVM);
        this.setData({
          loading: false,
          loaded: true,
          rows: page === 1 ? vms : this.data.rows.concat(vms),
          page,
          hasMore: !!res.next
        });
      })
      .catch(() => this.setData({ loading: false, error: true, loaded: true }));
  }
});
