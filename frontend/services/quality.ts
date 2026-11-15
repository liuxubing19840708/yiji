/**
 * 质检管理服务（竞品对齐 GAP-1，2026-11-04）。
 * 权限：GET/导出 = quality.view；登记/收货/完成/删除 = quality.manage。
 */
import { BASE_URL } from '../utils/config';
import { http } from '../utils/request';
import { getToken, getCompanyId } from '../utils/auth';

export type QualityStatus = 'rework' | 'received' | 'done';

export interface QualityRecordRow {
  id: number;
  ticket: number;
  ticket_bed_no: string;
  ticket_bundle_no: string;
  ticket_color: string;
  ticket_size: string;
  ticket_qty: number;
  style_no: string;
  process: number | null;
  process_name: string | null;
  defect_qty: number;
  reason: string;
  status: QualityStatus;
  status_display: string;
  received_qty: number;
  received_at: string | null;
  producer: number | null;
  producer_name: string | null;
  inspector: number | null;
  inspector_name: string | null;
  inspected_at: string;
  remark: string;
}

export function fetchQualityRecords(params?: {
  status?: QualityStatus;
  ticket_id?: number;
  producer_id?: number;
  bed_no?: string;
  page?: number;
  page_size?: number;
}): Promise<{ count: number; results: QualityRecordRow[] }> {
  return http.get<{ count: number; results: QualityRecordRow[] }>('/quality/records', params);
}

export function createQualityRecord(data: {
  ticket: number;
  process?: number | null;
  defect_qty: number;
  reason: string;
  producer?: number | null;
  remark?: string;
}): Promise<QualityRecordRow> {
  return http.post<QualityRecordRow>('/quality/records', data as unknown as Record<string, unknown>);
}

/** 动作端点（wx.request 不支持 PATCH，统一 PUT）：action=receive|finish */
export function qualityRecordAction(id: number, data: {
  action: 'receive' | 'finish';
  received_qty?: number;
  producer?: number | null;
  remark?: string;
}): Promise<QualityRecordRow> {
  return http.put<QualityRecordRow>(`/quality/records/${id}`, data as unknown as Record<string, unknown>);
}

export function deleteQualityRecord(id: number): Promise<void> {
  return http.del<void>(`/quality/records/${id}`);
}

/** 导出 xlsx（wx.downloadFile 直连，需带鉴权头） */
export function qualityExportUrl(params?: {
  status?: QualityStatus;
  bed_no?: string;
}): string {
  const qs: string[] = [];
  if (params?.status) qs.push(`status=${params.status}`);
  if (params?.bed_no) qs.push(`bed_no=${encodeURIComponent(params.bed_no)}`);
  return `${BASE_URL}/quality/export${qs.length ? '?' + qs.join('&') : ''}`;
}

/** 下载并打开导出的 xlsx（与 records 页 onExport 同法） */
export function downloadQualityExport(url: string): Promise<void> {
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
