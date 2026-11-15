/**
 * 出货管理接口（shipment 域，P2 §4.2）。
 * 契约唯一真源：backend/contract_check.py 出货 13 条路由 + apps/shipment/
 * serializers.py / views.py / services.py（2026-10-24 逐字段核对）。权限：
 * 列表/详情/未交矩阵/装箱行读/打印历史挂 shipment.view；建单/改单/删单/
 * 审核/作废/装箱行写/打印上报挂 shipment.manage（上报 print.manage 亦可，
 * 两点 legacy=False）。
 *
 * 口径（后端注释为准）：
 *  - 金额不落出货侧：应收金额由审核服务按 OrderItem.price 现算并自动建应收；
 *  - 审核三道 400 硬拦：超未交量（真口径 OrderItem.qty−Σ已出）/成品库存不足
 *    （完工结存口径）/色码未定价（提示去订单补价）——前端 400 文案直显，
 *    命中「补价」时给订单编辑跳转；
 *  - 作废=软删，响应含 warning（应收号/已收金额文案后端拼好）+ 挂单应收清单，
 *    前端只展示不加工；
 *  - 未交矩阵：items=[{color,size,qty,shipped_qty,undelivered_qty}] + 三合计；
 *  - 打印上报 print_key 幂等（shipment:{id}:{tpl}）：duplicated=[key] → toast 已打过；
 *    is_reprint=True → 补打徽标；
 *  - status：draft 草稿 / confirmed 全部出货 / partial 部分出货 /
 *    shipped / signed（预留态，暂无按钮）。
 */
import { http } from '../utils/request';
import { BASE_URL } from '../utils/config';
import { getCompanyId, getToken } from '../utils/auth';
import { Paginated } from './types';

// ============ 类型（与 apps/shipment/serializers.py 字段一一对应） ============

export type ShipmentStatus = 'draft' | 'confirmed' | 'partial' | 'shipped' | 'signed';
export type ShipmentTpl = 'shipment' | 'packing';

/** 出货明细行（读侧内联；写侧 items=[{color,size,qty}]） */
export interface ShipmentItem {
  id: number;
  color: string;
  size: string;
  qty: number;
}

/** 装箱行（毛净重/箱规；boxes/total_qty 由后端自动汇总） */
export interface PackingBox {
  id: number;
  box_no: string;
  color: string;
  size: string;
  qty: number;
  gross_wt: string;
  net_wt: string;
  box_spec: string;
  created_at: string;
}

export interface Shipment {
  id: number;
  shipment_no: string;
  order: number | null;
  order_no: string;
  customer: number | null;
  customer_name: string;
  delivery_date: string | null;
  ship_date: string | null;
  status: ShipmentStatus;
  status_display: string;
  /** 装箱行数（自动汇总） */
  boxes: number;
  total_qty: number;
  logistics_no: string;
  audited_by: number | null;
  audited_by_name: string;
  audited_at: string | null;
  remark: string;
  items: ShipmentItem[];
  created_at: string;
}

export interface ShipmentPrintLog {
  id: number;
  shipment: number;
  template: ShipmentTpl;
  template_display: string;
  print_key: string | null;
  is_reprint: boolean;
  operator: number | null;
  operator_name: string;
  created_at: string;
}

/** POST /shipments/{id}/audit 响应：{shipment, receivable} 双块 */
export interface ShipmentAuditResult {
  shipment: Shipment;
  receivable: {
    id: number;
    receivable_no: string;
    amount: string;
    status: string;
    shipment_id: number;
  };
}

/** POST /shipments/{id}/void 响应：warning 后端拼好，前端直显 */
export interface ShipmentVoidResult {
  id: number;
  shipment_no: string;
  voided: boolean;
  warning: string;
  receivables: Array<{
    id: number;
    receivable_no: string;
    amount: string;
    received_amount: string;
    status: string;
  }>;
}

/** GET /shipments/{id}/undelivered 未交矩阵（真口径，services.undelivered_matrix） */
export interface UndeliveredMatrix {
  order_id: number;
  order_no: string;
  items: Array<{
    order_item_id: number;
    color: string;
    size: string;
    qty: number;
    shipped_qty: number;
    undelivered_qty: number;
  }>;
  total_qty: number;
  total_shipped: number;
  total_undelivered: number;
  note: string;
}

/** POST /shipments/{id}/print-logs 打印上报响应（print_key 幂等） */
export interface ShipmentPrintReport {
  reported: number;
  duplicated: string[];
  is_reprint?: boolean;
}

// ============ 出货单 CRUD ============

/** GET /shipments?status=&order_id=&customer_id=&keyword=
 * keyword 命中 出货单号/客户名/订单号/物流单号 */
export function fetchShipments(params?: {
  status?: ShipmentStatus | string; order_id?: number; customer_id?: number;
  keyword?: string; page?: number; page_size?: number;
}): Promise<Paginated<Shipment>> {
  return http.get<Paginated<Shipment>>('/shipments',
    (params || {}) as Record<string, unknown>);
}

/** POST /shipments 建单（customer 必传；单号留空自动 SH+日期+3 位；
 * items 校验：非空/色码齐全/qty>0/同单同色码唯一——前端预校验同口径） */
export function createShipment(body: {
  shipment_no?: string;
  order: number;
  customer: number;
  delivery_date?: string | null;
  ship_date?: string | null;
  logistics_no?: string;
  remark?: string;
  items: Array<{ color: string; size: string; qty: number }>;
}): Promise<Shipment> {
  return http.post<Shipment>('/shipments', body as unknown as Record<string, unknown>);
}

export function fetchShipment(id: number): Promise<Shipment> {
  return http.get<Shipment>(`/shipments/${id}`);
}

/** PUT /shipments/{id}：草稿可全量改（order/customer/items 全量替换）；
 * 已审核仅允许 logistics_no/ship_date/remark（传其他字段 400） */
export function updateShipment(id: number, body: Partial<{
  order: number; customer: number;
  delivery_date?: string | null; ship_date?: string | null;
  logistics_no?: string; remark?: string;
  items: Array<{ color: string; size: string; qty: number }>;
}>): Promise<Shipment> {
  return http.put<Shipment>(`/shipments/${id}`,
    body as unknown as Record<string, unknown>);
}

/** DELETE /shipments/{id}：仅草稿（已审核走作废，400 引导） */
export function deleteShipment(id: number): Promise<void> {
  return http.del<void>(`/shipments/${id}`);
}

// ============ 审核 / 作废 / 未交矩阵 ============

/** POST /shipments/{id}/audit：三道校验 → 自动建应收。
 * 400 三类文案直显：超未交量 / 成品库存不足 / 「以下色码未定价，请先在订单中补价：色/码」 */
export function auditShipment(id: number): Promise<ShipmentAuditResult> {
  return http.post<ShipmentAuditResult>(`/shipments/${id}/audit`);
}

/** POST /shipments/{id}/void：作废（撤单不自动撤应收，warning 直显） */
export function voidShipment(id: number): Promise<ShipmentVoidResult> {
  return http.post<ShipmentVoidResult>(`/shipments/${id}/void`);
}

/** GET /shipments/{id}/undelivered：本订单未交矩阵（本单草稿量不计入已出） */
export function fetchUndelivered(id: number): Promise<UndeliveredMatrix> {
  return http.get<UndeliveredMatrix>(`/shipments/${id}/undelivered`);
}

// ============ 装箱行 ============

export function fetchBoxes(shipmentId: number): Promise<PackingBox[]> {
  return http.get<PackingBox[]>(`/shipments/${shipmentId}/boxes`);
}

/** POST /shipments/{id}/boxes：box_no 必填（400「请填写箱号」） */
export function addBox(shipmentId: number, body: {
  box_no: string; color?: string; size?: string; qty?: number | string;
  gross_wt?: string | number; net_wt?: string | number; box_spec?: string;
}): Promise<PackingBox> {
  return http.post<PackingBox>(`/shipments/${shipmentId}/boxes`,
    body as unknown as Record<string, unknown>);
}

export function deleteBox(shipmentId: number, boxId: number): Promise<void> {
  return http.del<void>(`/shipments/${shipmentId}/boxes/${boxId}`);
}

// ============ 打印（出货单 / 装箱单） ============

/** GET /shipments/{id}/print-logs?template= 打印历史（is_reprint=补打） */
export function fetchPrintLogs(shipmentId: number, template?: ShipmentTpl): Promise<ShipmentPrintLog[]> {
  return http.get<ShipmentPrintLog[]>(`/shipments/${shipmentId}/print-logs`,
    template ? { template } : undefined);
}

/** POST /shipments/{id}/print-logs 打印上报。
 * 每次实际打开 PDF 后调用，打印键由页面在用户触发时生成；同一键重试不重复计数。
 * 权限：shipment.manage 或 print.manage。 */
export function reportShipmentPrint(
  shipmentId: number,
  template: ShipmentTpl,
  printKey: string
): Promise<ShipmentPrintReport> {
  return http.post<ShipmentPrintReport>(`/shipments/${shipmentId}/print-logs`, {
    template,
    print_key: printKey
  } as unknown as Record<string, unknown>);
}

/** 每次用户实际执行打印生成独立键；同一请求重试沿用该键，补打仍由后端按历史识别。 */
export function createShipmentPrintKey(shipmentId: number, template: ShipmentTpl): string {
  const random = Math.random().toString(36).slice(2, 10);
  return `shipment:${shipmentId}:${template}:${Date.now()}:${random}`.slice(0, 64);
}

/** 下载并打开后端生成的真实中文 PDF；打开失败不得上报打印。 */
export function openShipmentPrintFile(shipmentId: number, template: ShipmentTpl): Promise<void> {
  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url: `${BASE_URL}/shipments/${shipmentId}/print-file?template=${template}`,
      header: {
        Authorization: `Bearer ${getToken()}`,
        'X-Company-Id': String(getCompanyId())
      },
      success(download) {
        if (download.statusCode !== 200 || !download.tempFilePath) {
          reject(new Error(`出货单据下载失败（${download.statusCode}）`));
          return;
        }
        wx.openDocument({
          filePath: download.tempFilePath,
          fileType: 'pdf',
          showMenu: true,
          success: () => resolve(),
          fail: () => reject(new Error('出货 PDF 打开失败，请检查微信文档能力'))
        });
      },
      fail: () => reject(new Error('出货 PDF 下载失败，请检查网络'))
    });
  });
}
