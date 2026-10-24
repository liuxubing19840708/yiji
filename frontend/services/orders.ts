/**
 * 订单管理接口（orders，P1 新增）。
 * 契约=docs/系统重设计实施方案.md §四（冻结）：
 *   GET/POST /orders、GET/PUT/DELETE /orders/<id>、PUT /orders/<id>/status、GET /orders/<id>/progress；
 *   列表带 total_qty/registered_qty/progress/days_left；建单单号留空自动 SO 顺延（软删防撞）；
 *   DELETE 有挂单床次 400（只可取消）；items 全量替换。
 * 注意：后端由并行 SA1 开发中——调用方（页面）一律 try/catch 兜底，
 *   接口 404/未就绪时提示「服务升级中」，不白屏（见 packages/admin/orders、order-detail）。
 */
import { http } from '../utils/request';
import { Order, OrderProgress, OrderItem, Paginated } from './types';

/** 订单写入载荷（新建/编辑共用；items 全量提交）。
 *  F1/B1 契约：单号由后端自动生成（SO+日期+序号），POST 不传 order_no（传了也被忽略）；
 *  style 必填（第一步选款式；后端序列化器字段名就是 style——传 style_id 后端不认，
 *  会 400「请关联款式」）；images=备注图片相对路径数组；process_items=关联工序。 */
export interface OrderPayload {
  order_no?: string;
  style?: number;
  /** 旧键名，仅兼容保留；后端不识别，勿再使用 */
  style_id?: number;
  customer?: number | null;
  delivery_date?: string | null;
  order_type?: string;
  remark?: string;
  /** 备注图片（相对路径数组，wx.uploadFile /uploads 返回的 url） */
  images?: string[];
  /** 关联工序（可选填，模板/手工添加） */
  process_items?: Array<{ process_id: number; price: string }>;
  items: Array<{ color: string; size: string; qty: number }>;
}

/** 订单列表：status=producing/done/cancelled（缺省全部）+ keyword（单号/客户模糊） */
export function fetchOrders(params?: {
  status?: string; keyword?: string; page?: number; page_size?: number;
}): Promise<Paginated<Order>> {
  return http.get<Paginated<Order>>('/orders', params);
}

export function fetchOrder(id: number): Promise<Order> {
  return http.get<Order>(`/orders/${id}`);
}

/** 新建订单：order_no 留空由后端自动 SO0001 顺延；校验：同单同色码唯一、总数量>0 */
export function createOrder(data: OrderPayload): Promise<Order> {
  return http.post<Order>('/orders', data as unknown as Record<string, unknown>);
}

/** 编辑订单：items 全量替换（PUT /orders/<id>） */
export function updateOrder(id: number, data: OrderPayload): Promise<Order> {
  return http.put<Order>(`/orders/${id}`, data as unknown as Record<string, unknown>);
}

/** 删除订单（已有挂单床次后端 400 拦截：已挂床次不可删只可取消） */
export function deleteOrder(id: number): Promise<unknown> {
  return http.del(`/orders/${id}`);
}

/** 状态机：producing → done / cancelled（<100% 允许完成，管理员知情操作，前端弹确认） */
export function updateOrderStatus(id: number, status: 'done' | 'cancelled'): Promise<Order> {
  return http.put<Order>(`/orders/${id}/status`, { status });
}

/** 色码矩阵完成度（后端内部调 stats.py 唯一口径，前端不自算口径） */
export function fetchOrderProgress(id: number): Promise<OrderProgress> {
  return http.get<OrderProgress>(`/orders/${id}/progress`);
}

/**
 * 订单关联床次列表（GET /productions?order_id=，契约 §四 POST/GET /productions 改造）。
 * 落在本文件以避免改动 services/production.ts（SF1 只动订单域文件）。
 */
export function fetchOrderProductions(orderId: number, params?: {
  page?: number; page_size?: number;
}): Promise<Paginated<import('./types').Production>> {
  return http.get<import('./types').Paginated<import('./types').Production>>(
    '/productions', { order_id: orderId, ...(params || {}) });
}
