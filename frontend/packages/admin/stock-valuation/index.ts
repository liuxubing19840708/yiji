/**
 * 库存估值报表页（finance 域 P2，D4 双口径第二口径，finance.manage）：
 * - 双口径说明卡：车间用批次价（Lot.unit_cost 与缸号绑定）、财务对账用移动加权
 *   （本表：Σ入库额/Σ入库量 逐笔递推；退料按当前加权价回冲；qty 归零清残差；
 *   负结存沿用最近加权价原样示出）；
 * - 过滤：物料 picker + 截止日 date_to（缺省=至今）；
 * - 物料组卡片：加权均价/结存量/结存额/累计入库/净领用/缺价行数徽标；
 *   点开看批次明细（lot_no/缸色/单位/结存）；
 * - 混合单位组 unit=''（以批次明细为准，后端口径）。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { fetchStockValuation, StockValRow } from '../../../services/finance';
import { fetchMaterials } from '../../../services/tech';

interface GroupVM extends StockValRow {
  title: string;
  expanded: boolean;
  missingCnt: number;
}

Page({
  data: {
    denied: false,
    loading: false,
    error: false,
    searched: false,
    // ---- 过滤 ----
    materials: [] as Array<{ id: number; label: string }>,
    materialNames: ['全部物料'],
    materialIndex: 0,
    dateTo: '',
    // ---- 数据 ----
    note: '',
    rows: [] as GroupVM[]
  },

  onShow() {
    if (!requireLogin()) return;
    const denied = !hasPerm('finance.manage');
    this.setData({ denied });
    if (!denied && !this.data.materials.length) {
      fetchMaterials({ page: 1, page_size: 200 })
        .then((res) => {
          const materials = (res.results || []).map((m) => ({
            id: m.id, label: `${m.code} ${m.name}`
          }));
          this.setData({
            materials,
            materialNames: ['全部物料'].concat(materials.map((m) => m.label))
          });
        })
        .catch(() => undefined);
    }
  },

  onPullDownRefresh() {
    if (this.data.searched) this.search();
    wx.stopPullDownRefresh();
  },

  // ============ 过滤 ============

  onMaterialChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ materialIndex: Number(e.detail.value) });
  },

  onDateChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ dateTo: String(e.detail.value) });
  },

  onSearch() {
    this.search();
  },

  search() {
    const { materialIndex, materials, dateTo } = this.data;
    const params: Record<string, unknown> = {};
    if (materialIndex > 0 && materials[materialIndex - 1]) {
      params.material_id = materials[materialIndex - 1].id;
    }
    if (dateTo) params.date_to = dateTo;
    this.setData({ loading: true, error: false, searched: true });
    fetchStockValuation(params)
      .then((rep) => {
        const rows: GroupVM[] = (rep.rows || []).map((r) => ({
          ...r,
          title: r.material_id
            ? `${r.material_code} ${r.material_name}`.trim()
            : '未归集物料（入库/领料行均无物料）',
          expanded: false,
          missingCnt: r.missing_price_rows || 0
        }));
        this.setData({ loading: false, note: rep.note || '', rows });
      })
      .catch(() => this.setData({ loading: false, error: true }));
  },

  /** 展开/收起批次明细 */
  onToggleRow(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    if (idx < 0 || idx >= this.data.rows.length) return;
    this.setData({ [`rows[${idx}].expanded`]: !this.data.rows[idx].expanded });
  }
});
