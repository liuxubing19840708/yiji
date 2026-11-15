/**
 * 外发管理服务（竞品对齐 GAP-5，2026-11-04）。
 * 权限：GET/导出 = outsource.view；建单/收回/结算/删除 = outsource.manage。
 */
import { BASE_URL } from '../utils/config';
import { http } from '../utils/request';
import { getToken, getCompanyId } from '../utils/auth';

export type OutsourceStatus = 'sent' | 'partial' | 'returned' | 'settled';

export interface OutsourceOrderRow {
  id: number;
  no: string;
  supplier: number | null;
  supplier_name: string;
  factory_name: string;
  order: number | null;
  order_no: string;
  style: number | null;
  style_no: string;
  process_desc: string;
  send_qty: number;
  received_qty: number;
  unit_price: string;
  amount: string;
  send_date: string;
  due_date: string | null;
  return_date: string | null;
  status: OutsourceStatus;
  status_display: string;
  remark: string;
  creator_name: string | null;
  receive_count: number;
}

export function fetchOutsourceOrders(params?: {
  status?: OutsourceStatus;
  supplier_id?: number;
  page?: number;
  page_size?: number;
}): Promise<{ count: number; results: OutsourceOrderRow[] }> {
  return http.get<{ count: number; results: OutsourceOrderRow[] }>('/outsource/orders', params);
}

export function createOutsourceOrder(data: {
  supplier?: number | null;
  factory_name?: string;
  style?: number | null;
  process_desc?: string;
  send_qty: number;
  unit_price?: string;
  due_date?: string;
  remark?: string;
}): Promise<OutsourceOrderRow> {
  return http.post<OutsourceOrderRow>('/outsource/orders', data as unknown as Record<string, unknown>);
}

/** 动作端点（wx.request 不支持 PATCH，统一 PUT）：action=receive|settle */
export function outsourceAction(id: number, data: {
  action: 'receive' | 'settle';
  qty?: number;
  remark?: string;
}): Promise<OutsourceOrderRow> {
  return http.put<OutsourceOrderRow>(`/outsource/orders/${id}`, data as unknown as Record<string, unknown>);
}

export function deleteOutsourceOrder(id: number): Promise<void> {
  return http.del<void>(`/outsource/orders/${id}`);
}

/** 导出 xlsx（wx.downloadFile 直连，需带鉴权头） */
export function outsourceExportUrl(params?: { status?: OutsourceStatus }): string {
  const qs = params?.status ? `?status=${params.status}` : '';
  return `${BASE_URL}/outsource/export${qs}`;
}

export function downloadOutsourceExport(url: string): Promise<void> {
  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url,
      header: {
        Authorization: `Bearer ${getToken()}`,
        'X-Company-Id': String(getCompanyId())
      },
      success: (res) => {
        if (res.statusCode !== 200) { reject(new Error(`下载失败 ${res.statusCode}`)); return; }
        wx.openDocument({
          filePath: res.tempFilePath,
          fileType: 'xlsx',
          showMenu: true,
          success: () => resolve(),
          fail: (e) => reject(e)
        });
      },
      fail: (e) => reject(e)
    });
  });
}
