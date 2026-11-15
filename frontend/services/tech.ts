/**
 * 技术管理接口（tech 域，P1）：物料主档 / 版式BOM。
 * 契约唯一真源：backend/contract_check.py 16 条 tech 路由 + apps/tech/views.py
 * （2026-10-24 逐条核对）。权限：全部端点挂 tech.material / tech.bom 两点
 * （导入并入对应点，后端无独立 import 点）。
 *
 * 口径：
 *  - 物料 PUT 为全量语义（后端 _clean_material_payload 必填 code/name/unit，
 *    未传 attrs/price_reference 会被清空 → 页面编辑时必须回传原值）；
 *  - 物料 DELETE 为软删（PROTECT：被 BOM 引用的行前端一律走「停用」不调删除）；
 *  - BOM PUT 不传 items 保持原明细，传 items 全量替换；style 不传保持原款式（改版走 fork）；
 *  - 导入 all-or-nothing：任一行失败整批不入库，errors[{row,code,reason}] 带行号。
 */
import { http } from '../utils/request';
import { BASE_URL } from '../utils/config';
import { getToken, getCompanyId } from '../utils/auth';
import { logger } from '../utils/logger';
import { Paginated } from './types';

// ============ 类型（与 apps/tech/serializers.py 字段一一对应） ============

export type MaterialCategory = 'fabric' | 'lining' | 'accessory' | 'packing';
export type MaterialStatus = 'active' | 'disabled';
export type BomStatus = 'draft' | 'active' | 'deactivated';
export type BomItemKind = 'main' | 'lining' | 'accessory';

export interface Material {
  id: number;
  code: string;
  name: string;
  category: MaterialCategory;
  unit: string;
  unit_alt: string;
  weight_gsm: string | null;
  width_cm: string | null;
  shrink_rate: string | null;
  price_reference: string | null;
  /** 单价单位缺省（D19）：''=按主单位 / 'kg'=元/公斤 / 'm'=元/米 */
  price_unit: string | null;
  /** P2.5 主单位安全库存/补货水位（0=未设；需求汇总补货点口径用） */
  safety_stock: string;
  status: MaterialStatus;
  legacy_code: string;
  attrs: Record<string, unknown>;
  /** 双单位换算参数是否齐备（克重×幅宽；false 时列表标「待补参数」） */
  conversion_ready: boolean;
  created_at: string;
  updated_at: string;
}

export interface MaterialPayload {
  code: string;
  name: string;
  category?: MaterialCategory | string;
  unit: string;
  unit_alt?: string;
  weight_gsm?: string | number | null;
  width_cm?: string | number | null;
  shrink_rate?: string | number | null;
  price_reference?: string | number | null;
  price_unit?: string;
  safety_stock?: string | number | null;
  status?: MaterialStatus;
  legacy_code?: string;
  attrs?: Record<string, unknown>;
}

export interface BomItem {
  id: number;
  material: number;
  material_code: string;
  material_name: string;
  kind: BomItemKind;
  /** 主单位快照（=物料主单位，只读） */
  unit: string;
  color: string;
  size: string;
  unit_usage: string;
  loss_rate: string;
  position: string;
  remark: string;
}

/** BOM 明细行提交格式（PUT/POST items 元素；unit 由后端按物料快照，不传） */
export interface BomItemPayload {
  material: number;
  kind?: BomItemKind | string;
  color?: string;
  size?: string;
  unit_usage: string | number;
  loss_rate?: string | number | null;
  position?: string;
  remark?: string;
}

export interface Bom {
  id: number;
  style: number;
  style_no: string;
  style_name: string;
  version: number;
  status: BomStatus;
  remark: string;
  items: BomItem[];
  item_count: number;
  created_at: string;
  updated_at: string;
}

/** 导入错误行（row=Excel 实际行号，表头占第 1 行、数据从 2 起） */
export interface ImportRowError {
  row: number;
  code?: string;
  reason: string;
}

export interface ImportResult {
  created: number;
  updated: number;
  errors: ImportRowError[];
}

// ============ 物料主档（tech.material） ============

export function fetchMaterials(params?: {
  keyword?: string; category?: string; page?: number; page_size?: number;
}): Promise<Paginated<Material>> {
  return http.get<Paginated<Material>>('/tech/materials', params);
}

export function fetchMaterial(id: number): Promise<Material> {
  return http.get<Material>(`/tech/materials/${id}`);
}

export function createMaterial(data: MaterialPayload): Promise<Material> {
  return http.post<Material>('/tech/materials', data as unknown as Record<string, unknown>);
}

/** 全量更新（code/name/unit 必填；attrs/price_reference 不传会被后端清空） */
export function updateMaterial(id: number, data: MaterialPayload): Promise<Material> {
  return http.put<Material>(`/tech/materials/${id}`, data as unknown as Record<string, unknown>);
}

/**
 * 软删（后端 PROTECT：被 BOM 引用会报保护错）。业务上优先走「停用」
 * （status=disabled），删除仅供确认无引用的档案清理，页面默认不露出。
 */
export function deleteMaterial(id: number): Promise<unknown> {
  return http.del(`/tech/materials/${id}`);
}

/**
 * 物料批量导入（all-or-nothing）：JSON rows 路径，键=模板中文列名
 * （编码/品名/分类/主单位/副单位/克重/幅宽/缩率/旧编码，分类填中文）。
 */
export function importMaterialsRows(rows: Array<Record<string, string | number>>): Promise<ImportResult> {
  return http.post<ImportResult>('/tech/materials/import', { rows } as unknown as Record<string, unknown>);
}

/**
 * 物料批量导入：xlsx 文件路径（multipart，字段名 file；后端仅认 .xlsx）。
 * wx.uploadFile 不走 request 层，错误提示/日志在此对齐 http 惯例。
 */
export function importMaterialsFile(filePath: string): Promise<ImportResult> {
  return uploadImport('/tech/materials/import', filePath);
}

// ============ 版式BOM（tech.bom） ============

export function fetchBoms(params?: {
  style_id?: number; version?: number; status?: string; page?: number; page_size?: number;
}): Promise<Paginated<Bom>> {
  return http.get<Paginated<Bom>>('/tech/boms', params);
}

export function fetchBom(id: number): Promise<Bom> {
  return http.get<Bom>(`/tech/boms/${id}`);
}

/** 新建草稿 BOM；version 缺省由后端自动取该款式最大版本+1；items 可选（一般建空草稿后再编辑明细） */
export function createBom(data: {
  style: number; version?: number; remark?: string; items?: BomItemPayload[];
}): Promise<Bom> {
  return http.post<Bom>('/tech/boms', data as unknown as Record<string, unknown>);
}

/** 保存明细（items 全量替换）与备注；status 只能经 activate 变更 */
export function updateBom(id: number, data: {
  remark?: string; items?: BomItemPayload[];
}): Promise<Bom> {
  return http.put<Bom>(`/tech/boms/${id}`, data as unknown as Record<string, unknown>);
}

/** 软删（明细随行保留）；页面仅对草稿露出 */
export function deleteBom(id: number): Promise<unknown> {
  return http.del(`/tech/boms/${id}`);
}

/** 草稿→生效；同款式旧 active 事务内自动降级 deactivated */
export function activateBom(id: number): Promise<Bom> {
  return http.post<Bom>(`/tech/boms/${id}/activate`);
}

/** 改版：以本版为底稿另存新草稿（版本=历史最大+1），旧 active 自动停用留痕 */
export function forkBom(id: number): Promise<Bom> {
  return http.post<Bom>(`/tech/boms/${id}/fork`);
}

/** 按版本反查在途单据引用（P1 恒 {count:0}，P2 采购/领料接入后聚合） */
export function fetchBomReferences(id: number): Promise<{ count: number; bom_id: number; note: string }> {
  return http.get<{ count: number; bom_id: number; note: string }>(`/tech/boms/${id}/references`);
}

/**
 * BOM 批量导入（JSON rows 路径）：键=模板中文列名
 * （款号/版本/物料编码/类别/颜色/尺码/单耗/损耗率/部位/备注）；
 * 同（款号,版本）多行归并为一个草稿。P1 页面暂不露出入口（明细走编辑器）。
 */
export function importBomsRows(rows: Array<Record<string, string | number>>): Promise<ImportResult> {
  return http.post<ImportResult>('/tech/boms/import', { rows } as unknown as Record<string, unknown>);
}

// ============ 导入上传（xlsx multipart，对齐 utils/upload 惯例） ============

/** 从统一错误格式提取提示（镜像 utils/request.ts extractMessage，该函数未导出） */
function extractImportMessage(data: Record<string, unknown>, fallback: string): string {
  if (typeof data.message === 'string') return data.message;
  if (typeof data.detail === 'string') return data.detail;
  if (typeof data.error === 'string') return data.error;
  for (const key of Object.keys(data)) {
    const val = data[key];
    if (Array.isArray(val) && val.length && typeof val[0] === 'string') {
      return `${key}:${val[0]}`;
    }
  }
  return fallback;
}

function uploadImport(url: string, filePath: string): Promise<ImportResult> {
  const header: Record<string, string> = { Authorization: `Bearer ${getToken()}` };
  const companyId = getCompanyId();
  if (companyId) {
    header['X-Company-Id'] = String(companyId);
  }
  return new Promise<ImportResult>((resolve, reject) => {
    wx.uploadFile({
      url: `${BASE_URL}${url}`,
      filePath,
      name: 'file',
      header,
      timeout: 30000,
      success(res) {
        const status = res.statusCode || 0;
        let body: Record<string, unknown> | null = null;
        try {
          body = JSON.parse(res.data) as Record<string, unknown>;
        } catch (e) {
          body = null;
        }
        if (status >= 200 && status < 300) {
          resolve((body || { created: 0, updated: 0, errors: [] }) as unknown as ImportResult);
          return;
        }
        const msg = body ? extractImportMessage(body, `导入失败（${status}）`) : `导入失败（${status}）`;
        logger.warn('tech导入', `POST ${url} ${status} ${String(res.data || '').slice(0, 200)}`);
        wx.showToast({ title: msg.length > 20 ? msg.slice(0, 20) + '…' : msg, icon: 'none' });
        reject(new Error(msg));
      },
      fail(err) {
        logger.warn('tech导入', `网络失败：${(err && err.errMsg) || 'unknown'}`);
        wx.showToast({ title: '网络异常，导入失败', icon: 'none' });
        reject(new Error('网络异常，导入失败'));
      }
    });
  });
}
