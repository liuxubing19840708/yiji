/**
 * 工序模板接口（F1/B1 契约）：GET/POST /process-templates、GET/PUT/DELETE /process-templates/{id}。
 * 模板对象 {id, name, items:[{process_id, price, process_name?}]}——
 * process_name 后端可能缺省，调用方用 /processes 列表自行映射兜底（见订单页工具 fillProcessNames）。
 * 后端由并行 B1 开发中：调用方一律 try/catch，404 时不白屏（模板区降级提示「服务升级中」）。
 */
import { http } from '../utils/request';
import { ProcessTemplate } from './types';

export function fetchProcessTemplates(): Promise<ProcessTemplate[]> {
  return http.get<ProcessTemplate[]>('/process-templates');
}

export function createProcessTemplate(data: { name: string; items: Array<{ process_id: number; price: string }> }): Promise<ProcessTemplate> {
  return http.post<ProcessTemplate>('/process-templates', data as unknown as Record<string, unknown>);
}

export function updateProcessTemplate(id: number, data: { name: string; items: Array<{ process_id: number; price: string }> }): Promise<ProcessTemplate> {
  return http.put<ProcessTemplate>(`/process-templates/${id}`, data as unknown as Record<string, unknown>);
}

export function deleteProcessTemplate(id: number): Promise<unknown> {
  return http.del(`/process-templates/${id}`);
}
