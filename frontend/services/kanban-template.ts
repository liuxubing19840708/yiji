/**
 * 可配置看板模板接口（后端 apps/kanban_template，设计文档 docs/kanban-template-design.md）。
 * 类型在本文件内维护（勿改 services/types.ts，避免与并行改动冲突）。
 */
import { http } from '../utils/request';
import { DashboardRange, DashboardPlan } from './dashboard';

/** 模板列（渲染接口下发，含展示配置） */
export interface KanbanColumnDef {
  id: number;              // 列 id（内置默认模板为负数占位）
  seq: number;
  title: string;
  bind_type: 'field' | 'formula' | 'static';
  field_key?: string;      // bind_type=field 时的系统字段 key
  merge: 'block' | 'value' | 'none';  // 纵向合并：block=随款块整块 / value=块内连续同值
  group: string;           // 两级表头父级（空=无组）
  width: number;           // 列宽 rpx
  align: 'left' | 'right' | 'center';
  placeholder: string;     // 无数据占位（todo 字段必显）
  /** 行 tap 下钻目标（§2.9 P0）：白名单 production|ticket|order，非法值忽略。
   *  后端在 KanbanColumn.extra.link 下发（SA2），旧后端无此键 → undefined=不可点 */
  link?: string;
  /** 原始 extra 兜底（后端把 link 放 extra 时前端自行取出，防御旧/新两种下发形态） */
  extra?: Record<string, unknown>;
}

/** 下钻 link 白名单（§四 契约：extra.link 白名单 production|ticket|order，非法值忽略） */
export const COLUMN_LINK_WHITELIST = ['production', 'ticket', 'order'] as const;

/** 列标题显示别名：后端新旧字典兼容（「订单数量」→「菲票数量」，公式字段 order_qty 语义不动，仅显示端改名） */
export const COLUMN_TITLE_ALIAS: Record<string, string> = {
  '订单数量': '菲票数量'
};

/** 订单页签数据走 SF1 的 services/orders.ts（fetchOrders，契约 §四），本文件不再重复定义订单接口 */

/** 行单元格（后端已按列序对齐） */
export interface KanbanCell {
  col: number;
  text: string;
}

/** 颜色行 */
export interface KanbanRow {
  key: string;
  cells: KanbanCell[];
}

/** 款块：同 (款号,下单日期) 的颜色行合一块 */
export interface KanbanBlock {
  key: string;
  alt: boolean;
  rows: KanbanRow[];
}

/** 日期分组 */
export interface KanbanGroup {
  date: string;
  label: string;
  blocks: KanbanBlock[];
}

/** 渲染接口响应 */
export interface KanbanRenderResp {
  template: { id: number | null; name: string; version: number; is_default: boolean };
  columns: KanbanColumnDef[];
  groups: KanbanGroup[];
}

/** 看板渲染数据：template_id 缺省用公司默认模板；
 *  无模板/渲染失败由后端回落内置默认模板（绝不允许白屏）。
 *  range：时间范围（与 /api/dashboard 同枚举） */
export function fetchKanbanRender(
  range: DashboardRange,
  opts?: { start?: string; end?: string; templateId?: number }
): Promise<KanbanRenderResp> {
  const params: Record<string, string> = { range };
  if (range === 'custom' && opts) {
    if (opts.start) params.start_date = opts.start;
    if (opts.end) params.end_date = opts.end;
  }
  if (opts && opts.templateId) params.template_id = String(opts.templateId);
  return http.get<KanbanRenderResp>('/kanban/render', params);
}

/** 内置默认列（客户端回落用，等价旧写死 10 列；与后端 services.builtin_default_columns 对齐）。
 *  - P1 列名统一：默认列「订单数量」→「菲票数量」（显示端别名，公式 order_qty 语义不动）；
 *  - P0 下钻：款号列挂 link=production（行 tap → 床次列表预过滤跳转）；
 *  - 补齐 field_key（此前缺失导致客户端回落渲染全为「—」占位，顺手修复） */
export const FALLBACK_COLUMNS: KanbanColumnDef[] = [
  { id: -1, seq: 0, title: '款号', bind_type: 'field', field_key: 'style_no', merge: 'block', group: '', width: 190, align: 'left', placeholder: '—', link: 'production' },
  { id: -2, seq: 1, title: '颜色', bind_type: 'field', field_key: 'color', merge: 'none', group: '', width: 110, align: 'left', placeholder: '—' },
  { id: -3, seq: 2, title: '菲票数量', bind_type: 'field', field_key: 'order_qty', merge: 'none', group: '', width: 130, align: 'right', placeholder: '—' },
  { id: -4, seq: 3, title: '床次', bind_type: 'field', field_key: 'bed_no', merge: 'none', group: '', width: 180, align: 'left', placeholder: '—' },
  { id: -5, seq: 4, title: '已裁数量', bind_type: 'field', field_key: 'cut_qty', merge: 'none', group: '', width: 130, align: 'right', placeholder: '—' },
  { id: -6, seq: 5, title: '已裁合计', bind_type: 'field', field_key: 'cut_total', merge: 'block', group: '', width: 130, align: 'right', placeholder: '—' },
  { id: -7, seq: 6, title: '排单情况', bind_type: 'field', merge: 'block', group: '', width: 120, align: 'center', placeholder: '—' },
  { id: -8, seq: 7, title: '下线数量', bind_type: 'field', merge: 'block', group: '', width: 120, align: 'center', placeholder: '—' },
  { id: -9, seq: 8, title: '出货', bind_type: 'field', merge: 'block', group: '', width: 120, align: 'center', placeholder: '—' },
  { id: -10, seq: 9, title: '尾部成品', bind_type: 'field', merge: 'block', group: '', width: 120, align: 'center', placeholder: '—' }
];

/** 旧 dashboard plans → 渲染结构（接口异常时的客户端回落，字段值与后端同口径） */
export function fallbackRenderFromPlans(plans: DashboardPlan[]): KanbanRenderResp {
  const rows = (plans || []).map((p) => ({
    key: `${p.style_id}-${p.color}`,
    style_id: p.style_id,
    order_date: p.order_date || '',
    cells: FALLBACK_COLUMNS.map((c) => {
      let text = '';
      switch (c.field_key) {
        case 'style_no': text = p.style_no; break;
        case 'color': text = p.color || '未分色'; break;
        case 'order_qty': text = String(p.order_qty); break;
        case 'bed_no': text = (p.beds || []).map((b) => `${b.bed_no}床×${b.qty}`).join('\n'); break;
        case 'cut_qty': text = String(p.cut_qty); break;
        case 'cut_total': text = String(p.cut_total); break;
        default: text = '—';
      }
      return { col: c.id, text };
    })
  }));
  // 按 (款号id,下单日期) 分块 → 按日期分组（与后端渲染同构）
  const groups: KanbanGroup[] = [];
  const gIndex: Record<string, number> = {};
  const blockMap: Record<string, KanbanBlock> = {};
  let serial = 0;
  rows.forEach((r, i) => {
    const p = (plans || [])[i];
    const date = r.order_date;
    if (gIndex[date] === undefined) {
      gIndex[date] = groups.length;
      groups.push({ date, label: date, blocks: [] });
    }
    const bkey = `${p.style_id}-${date}`;
    let blk = blockMap[bkey];
    if (!blk) {
      blk = { key: bkey, alt: serial % 2 === 1, rows: [] };
      serial += 1;
      blockMap[bkey] = blk;
      groups[gIndex[date]].blocks.push(blk);
    }
    blk.rows.push({ key: r.key, cells: r.cells });
  });
  return {
    template: { id: null, name: '默认看板', version: 0, is_default: true },
    columns: FALLBACK_COLUMNS,
    groups
  };
}
