/**
 * 超额领料差异报表（warehouse 域 P1，决策表 C2 挂账面）：
 * - 过滤：日期范围（领料日期）+ 订单/床次/物料（后端 date_from/date_to/order_id/
 *   production_id/material_id，全可选）；
 * - 双口径列（D4 同思路、领料侧）：实领主单位 / 退回 / 发料快照定额 / 当前 BOM 应发
 *   （qty_req_current=null 显示「无生效BOM」——改版差额一目了然）；
 * - summary 卡：超领占比 over_ratio / 无定额占比 no_req_ratio；
 * - over_rows 明细（单号/原因）点行展开；
 * - pending_units 待补参数清单（批次单位≠主单位且物料缺克重/幅宽）→ 跳物料档补参数。
 * 权限：挂 warehouse.manage（宫格「轻量仓库」同点入口的子页）。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { monthRange } from '../../../utils/format';
import { fetchOrders } from '../../../services/orders';
import { fetchProductions } from '../../../services/production';
import { fetchMaterials } from '../../../services/tech';
import {
  fetchIssueDivergence, DivergenceRow
} from '../../../services/warehouse';

/** 展开态 + 展示字段的行 VM */
interface RowVM extends DivergenceRow {
  expanded: boolean;
  /** 当前应发展示值（null → 「无生效BOM」） */
  reqCurrentText: string;
  hasReturn: boolean;
}

Page({
  data: {
    denied: false,
    // ---- 过滤 ----
    start: '',
    end: '',
    quick: 'month',
    orders: [] as Array<{ id: number; label: string }>,
    orderNames: ['全部订单'],
    orderIndex: 0,
    productions: [] as Array<{ id: number; label: string }>,
    productionNames: ['全部床次'],
    productionIndex: 0,
    materials: [] as Array<{ id: number; label: string }>,
    materialNames: ['全部物料'],
    materialIndex: 0,
    loading: false,
    error: false,
    // ---- 数据 ----
    summary: null as import('../../../services/warehouse').DivergenceReport['summary'] | null,
    rows: [] as RowVM[],
    pendingUnits: [] as import('../../../services/warehouse').DivergencePendingUnit[],
    // 首次进入不自动拉（过滤条件多为空时全量太大）：显示引导态
    searched: false
  },

  onShow() {
    if (!requireLogin()) return;
    this.setData({ denied: !hasPerm('warehouse.manage') });
    if (!this.data.denied) {
      if (!this.data.start) {
        const r = monthRange();
        this.setData({ start: r.start, end: r.end, quick: 'month' });
      }
      this.loadFilters();
    }
  },

  onPullDownRefresh() {
    this.search();
    wx.stopPullDownRefresh();
  },

  // ============ 过滤器 ============

  loadFilters() {
    // 订单/床次/物料三组 picker 数据；失败静默（过滤器缺省=不过滤）
    fetchOrders({ page: 1, page_size: 200 })
      .then((res) => {
        const orders = (res.results || []).map((o) => ({ id: o.id, label: o.order_no }));
        this.setData({
          orders,
          orderNames: ['全部订单'].concat(orders.map((o) => o.label))
        });
      })
      .catch(() => undefined);
    fetchProductions({ page: 1, page_size: 200 })
      .then((res) => {
        const productions = (res.results || []).map((p) => ({
          id: p.id,
          label: `${p.bed_no}${p.style_no ? ' · ' + p.style_no : ''}`
        }));
        this.setData({
          productions,
          productionNames: ['全部床次'].concat(productions.map((p) => p.label))
        });
      })
      .catch(() => undefined);
    fetchMaterials({ page: 1, page_size: 200 })
      .then((res) => {
        const materials = (res.results || []).map((m) => ({ id: m.id, label: `${m.code} ${m.name}` }));
        this.setData({
          materials,
          materialNames: ['全部物料'].concat(materials.map((m) => m.label))
        });
      })
      .catch(() => undefined);
  },

  onRangeChange(e: WechatMiniprogram.CustomEvent<{ start: string; end: string; quick: string }>) {
    this.setData({ start: e.detail.start, end: e.detail.end, quick: e.detail.quick || '' });
  },

  onOrderChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ orderIndex: Number(e.detail.value) });
  },

  onProductionChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ productionIndex: Number(e.detail.value) });
  },

  onMaterialChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ materialIndex: Number(e.detail.value) });
  },

  onSearch() {
    this.search();
  },

  // ============ 查询 ============

  search() {
    const { start, end, orderIndex, orders, productionIndex, productions,
      materialIndex, materials } = this.data;
    const params: Record<string, unknown> = {};
    if (start) params.date_from = start;
    if (end) params.date_to = end;
    if (orderIndex > 0 && orders[orderIndex - 1]) params.order_id = orders[orderIndex - 1].id;
    if (productionIndex > 0 && productions[productionIndex - 1]) {
      params.production_id = productions[productionIndex - 1].id;
    }
    if (materialIndex > 0 && materials[materialIndex - 1]) {
      params.material_id = materials[materialIndex - 1].id;
    }
    this.setData({ loading: true, error: false, searched: true });
    fetchIssueDivergence(params as Parameters<typeof fetchIssueDivergence>[0])
      .then((rep) => {
        this.setData({
          loading: false,
          summary: rep.summary,
          rows: (rep.rows || []).map((r) => ({
            ...r,
            expanded: false,
            reqCurrentText: r.qty_req_current === null || r.qty_req_current === undefined
              ? '无生效BOM' : r.qty_req_current,
            hasReturn: Number(r.qty_returned) > 0
          })),
          pendingUnits: rep.pending_units || []
        });
      })
      .catch(() => this.setData({ loading: false, error: true }));
  },

  /** 展开/收起 over_rows 明细（单号/原因） */
  onToggleRow(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    if (idx < 0 || idx >= this.data.rows.length) return;
    this.setData({ [`rows[${idx}].expanded`]: !this.data.rows[idx].expanded });
  },

  /** 待补参数 → 跳物料档补克重/幅宽 */
  onGoMaterials() {
    wx.navigateTo({ url: '/packages/admin/tech-materials/index' });
  }
});
