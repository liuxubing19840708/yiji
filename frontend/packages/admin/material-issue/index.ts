/**
 * 按床领料（warehouse 域 P1，决策表 C1/C2/C3）：
 * - 单床次上下文：选床次（带款式/计划件数）→ 领料模式自动带出 active BOM 定额行
 *   （material_name/unit/qty_req 只读展示，qty_req=净用量×(1+损耗率)×床次件数，
 *   与后端 quota_qty_for 同口径）；
 * - 手选物料行：无 BOM/补料用，物料选择器（搜 /tech/materials，启用过滤+单位展示，
 *   抄 SF1 tech-boms 子弹层），qty_req 可手填（留空=无定额，进无定额占比）；
 * - 超领零拦截（C2）：qty>qty_req 不拦，行标「超领」橙色徽标 + over_reason 选填
 *   （缩率超差/避疵/缸差排版/其他）；服务端按主单位折算做最终判定；
 * - 退料（type=return）：数量填正数=退回量，确认时冲减已领量（文案已写明）；
 * - 缸号（Lot）逐行选择留痕，行上展示 lot_no。
 * 权限：页面挂 warehouse.manage（宫格同点），无点防御态。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { fetchProductions } from '../../../services/production';
import {
  fetchBoms, fetchMaterials, Material
} from '../../../services/tech';
import {
  fetchLots, fetchProductionLotLinks, createMaterialIssue, confirmMaterialIssue,
  MaterialIssueType
} from '../../../services/warehouse';

interface ProductionVM {
  id: number;
  style: number;
  bedNo: string;
  styleNo: string;
  label: string;
  /** 床次件数基数：plan_qty 回落 total_qty（与后端 bed_base_qty 同口径） */
  baseQty: number;
}

/** 行视图模型（领料定额行 / 手选物料行 / 退料行 共用） */
interface RowVM {
  kind: 'quota' | 'manual';
  bomItemId: number | null;
  material: number | null;
  materialCode: string;
  materialName: string;
  /** 主单位（=物料主单位；qty_req 的单位） */
  unit: string;
  /** 定额（只读展示=定额行；手选行可填，空=无定额） */
  qtyReq: string;
  /** 数量（批次单位） */
  qty: string;
  lotId: number | null;
  lotLabel: string;
  lotIndex: number;
  /** 前端预判超领（qty>qty_req 时橙色徽标；零拦截，服务端终判） */
  over: boolean;
  overReason: string;
  overReasonIndex: number;
  /** 缸差提示（评审 Top10 #8）：所选缸号不在本床已用缸号集合 → 行标警示（零拦截） */
  lotMismatch: boolean;
  /** BOM 色/尺码附注 */
  variant: string;
}

const OVER_REASONS = ['缩率超差', '避疵', '缸差排版', '其他'];

/** 定额=净用量×(1+损耗率%)×床次件数（镜像后端 quota_qty_for，2 位截断） */
function computeQtyReq(unitUsage: string, lossRate: string | number | null | undefined, baseQty: number): string {
  const usage = Number(unitUsage);
  if (!Number.isFinite(usage) || usage <= 0) return '';
  const loss = Number(lossRate ?? 0);
  const req = usage * (1 + (Number.isFinite(loss) ? loss : 0) / 100) * baseQty;
  return String(Math.round(req * 100) / 100);
}

function rowOver(qty: string, qtyReq: string): boolean {
  const q = Number(qty);
  const r = Number(qtyReq);
  return qty !== '' && Number.isFinite(q) && qtyReq !== '' && Number.isFinite(r) && q > r;
}

Page({
  data: {
    denied: false,
    mode: 'issue' as MaterialIssueType | string,
    saving: false,
    // ---- 床次选择 ----
    productions: [] as ProductionVM[],
    productionNames: ['请选择床次'],
    productionIndex: 0,
    production: null as ProductionVM | null,
    // ---- 行 ----
    rows: [] as RowVM[],
    noBom: false,           // 领料模式且该款式无生效 BOM
    // ---- 退料提示（C4 退库入口文案：正数=退回量） ----
    // ---- 物料选择器（子弹层，抄 tech-boms） ----
    pickerVisible: false,
    matKeyword: '',
    matOptions: [] as Material[],
    matLoading: false,
    matPickingIndex: -1,
    // ---- 超领原因 ----
    overReasons: OVER_REASONS,
    // ---- 批次（缸号）picker range（onShow 预载） ----
    lotsNames: ['请选择批次'] as string[],
    // ---- 本床已用缸号（评审 Top10 #8）：选床后拉床次×批次血缘展示；选新缸号给缸差提示 ----
    bedLotNos: [] as string[],
    bedLotText: ''
  },

  onShow() {
    if (!requireLogin()) return;
    this.setData({ denied: !hasPerm('warehouse.manage') });
    if (!this.data.denied) {
      this.loadProductions();
      this.ensureLots();
    }
  },

  // ============ 床次选择 ============

  loadProductions() {
    fetchProductions({ page: 1, page_size: 200 })
      .then((res) => {
        const productions = (res.results || []).map((p) => {
          const styleNo = p.style_no || '';
          const baseQty = Number(p.plan_qty ?? p.total_qty ?? 0) || 0;
          return {
            id: p.id,
            style: p.style,
            bedNo: p.bed_no,
            styleNo,
            label: `${p.bed_no}${styleNo ? ' · ' + styleNo : ''}（${baseQty}件）`,
            baseQty
          };
        });
        const names = ['请选择床次'].concat(productions.map((p) => p.label));
        const kept = productions.findIndex((p) => p.id === (this.data.production || {} as ProductionVM).id);
        this.setData({
          productions,
          productionNames: names,
          productionIndex: kept >= 0 ? kept + 1 : 0,
          production: kept >= 0 ? productions[kept] : null
        });
      })
      .catch(() => { /* toast 已由请求层弹出 */ });
  },

  onPickProduction(e: WechatMiniprogram.PickerChange) {
    const idx = Number(e.detail.value);
    const production = idx > 0 ? this.data.productions[idx - 1] : null;
    this.setData({ productionIndex: idx, production, rows: [], noBom: false,
      bedLotNos: [], bedLotText: '' });
    if (production) {
      this.loadBedLots(production);
      if (this.data.mode === 'issue') this.loadQuotaRows(production);
    }
  },

  /** 本床已用缸号（评审 Top10 #8）：床次×批次血缘去重 lot_no；
   *  拉取失败静默（提示是增强，不阻塞领料主流程） */
  loadBedLots(production: ProductionVM) {
    fetchProductionLotLinks(production.id)
      .then((links) => {
        const nos: string[] = [];
        (links || []).forEach((l) => {
          if (l.lot_no && nos.indexOf(l.lot_no) < 0) nos.push(l.lot_no);
        });
        this.setData({
          bedLotNos: nos,
          bedLotText: nos.length ? `本床已用缸号：${nos.join('、')}` : '本床尚未用缸'
        });
      })
      .catch(() => { /* 血缘拉取失败不阻塞领料 */ });
  },

  /** 领料模式选床后：带出 active BOM 定额行（无生效 BOM → 提示手选物料） */
  loadQuotaRows(production: ProductionVM) {
    fetchBoms({ style_id: production.style, status: 'active', page_size: 50 })
      .then((res) => {
        const bom = (res.results || [])[0];
        if (!bom || !(bom.items || []).length) {
          this.setData({ noBom: true, rows: [] });
          return;
        }
        const rows: RowVM[] = bom.items.map((it) => {
          const qtyReq = computeQtyReq(it.unit_usage, it.loss_rate, production.baseQty);
          const variant = [it.color, it.size].filter(Boolean).join('/');
          return {
            kind: 'quota' as const,
            bomItemId: it.id,
            material: it.material,
            materialCode: it.material_code || '',
            materialName: it.material_name || '',
            unit: it.unit || '',
            qtyReq,
            qty: '',
            lotId: null,
            lotIndex: 0,
            lotLabel: '',
            over: false,
            overReason: '',
            overReasonIndex: -1,
            lotMismatch: false,
            variant
          };
        });
        this.setData({ noBom: false, rows });
      })
      .catch(() => this.setData({ noBom: true, rows: [] }));
  },

  // ============ 模式切换（领料/退料） ============

  onMode(e: WechatMiniprogram.TouchEvent) {
    const mode = String(e.currentTarget.dataset.mode);
    if (mode === this.data.mode) return;
    this.setData({ mode, rows: [], noBom: false });
    if (mode === 'issue' && this.data.production) {
      this.loadQuotaRows(this.data.production);
    }
  },

  // ============ 行编辑 ============

  onRowQty(e: WechatMiniprogram.Input) {
    const idx = Number(e.currentTarget.dataset.index);
    this.updateRow(idx, { qty: e.detail.value, over: rowOver(e.detail.value, this.data.rows[idx].qtyReq) });
  },

  /** 手选行的定额（可填；留空=无定额） */
  onRowQtyReq(e: WechatMiniprogram.Input) {
    const idx = Number(e.currentTarget.dataset.index);
    this.updateRow(idx, { qtyReq: e.detail.value, over: rowOver(this.data.rows[idx].qty, e.detail.value) });
  },

  onRowOverReason(e: WechatMiniprogram.PickerChange) {
    const idx = Number(e.currentTarget.dataset.index);
    const i = Number(e.detail.value);
    this.updateRow(idx, { overReasonIndex: i, overReason: OVER_REASONS[i] || '' });
  },

  onRemoveRow(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const rows = this.data.rows.slice();
    if (idx < 0 || idx >= rows.length) return;
    rows.splice(idx, 1);
    this.setData({ rows });
  },

  updateRow(idx: number, patch: Partial<RowVM>) {
    if (idx < 0 || idx >= this.data.rows.length) return;
    this.setData({ [`rows[${idx}]`]: { ...this.data.rows[idx], ...patch } });
  },

  // ============ 批次（缸号）选择 ============

  onPickLot(e: WechatMiniprogram.PickerChange) {
    const idx = Number(e.currentTarget.dataset.index);
    const lotIdx = Number(e.detail.value);
    const lot = this.lotsCache[lotIdx];
    if (!lot) return;
    // 缸差提示（评审 Top10 #8）：缸号不在本床已用集合 → 行标警示（零拦截，提示语不弹窗）
    const mismatch = this.data.bedLotNos.length > 0
      && this.data.bedLotNos.indexOf(lot.lotNo) < 0;
    this.updateRow(idx, { lotId: lot.id, lotLabel: lot.label, lotIndex: lotIdx,
      lotMismatch: mismatch });
  },

  /** 批次缓存（picker range 数据源；onShow 预载一次） */
  lotsCache: [] as Array<{ id: number; label: string; lotNo: string }>,

  ensureLots(): Promise<Array<{ id: number; label: string; lotNo: string }>> {
    if (this.lotsCache.length) return Promise.resolve(this.lotsCache);
    return fetchLots({ page: 1, page_size: 200 }).then((res) => {
      const lots = (res.results || []).map((l) => ({
        id: l.id,
        lotNo: l.lot_no,
        label: `${l.lot_no}${l.fabric_name ? ' ' + l.fabric_name : ''}${l.color ? ' ' + l.color : ''}（${l.qty || 0}${l.unit || ''}）`
      }));
      this.lotsCache = lots;
      this.setData({ lotsNames: ['请选择批次'].concat(lots.map((l) => l.label)) });
      return lots;
    }).catch(() => []);
  },

  // ============ 手选物料（子弹层，抄 tech-boms 物料选择器） ============

  onAddManualRow() {
    this.setData({ matPickingIndex: -1 });
    this.openPicker();
  },

  openPicker() {
    this.setData({ pickerVisible: true, matKeyword: '', matOptions: [] });
    this.searchMaterials('');
  },

  onMatKeyword(e: WechatMiniprogram.Input) {
    const kw = e.detail.value;
    this.setData({ matKeyword: kw });
    const that = this as unknown as { matTimer?: number };
    if (that.matTimer) clearTimeout(that.matTimer);
    that.matTimer = setTimeout(() => this.searchMaterials(kw), 400) as unknown as number;
  },

  onMatSearch() {
    this.searchMaterials(this.data.matKeyword);
  },

  searchMaterials(keyword: string) {
    this.setData({ matLoading: true });
    const params: Record<string, unknown> = { page: 1, page_size: 50 };
    if (keyword.trim()) params.keyword = keyword.trim();
    fetchMaterials(params as Parameters<typeof fetchMaterials>[0])
      .then((res) => {
        // 只可选启用物料（停用档案不再用于新单）
        this.setData({
          matOptions: (res.results || []).filter((m) => m.status === 'active'),
          matLoading: false
        });
      })
      .catch(() => this.setData({ matLoading: false }));
  },

  onSelectMaterial(e: WechatMiniprogram.TouchEvent) {
    const optIdx = Number(e.currentTarget.dataset.index);
    const m = this.data.matOptions[optIdx];
    if (!m) return;
    const row: RowVM = {
      kind: 'manual',
      bomItemId: null,
      material: m.id,
      materialCode: m.code,
      materialName: m.name,
      unit: m.unit,
      qtyReq: '',
      qty: '',
      lotId: null,
      lotIndex: 0,
      lotLabel: '',
      over: false,
      overReason: '',
      overReasonIndex: -1,
      lotMismatch: false,
      variant: ''
    };
    this.setData({ rows: this.data.rows.concat([row]), pickerVisible: false });
  },

  onClosePicker() {
    this.setData({ pickerVisible: false });
  },

  noop() { /* 阻止冒泡 */ },

  // ============ 提交（保存草稿 → 可选立即确认生效） ============

  onSubmit() {
    const { mode, production, rows } = this.data;
    if (!production) {
      wx.showToast({ title: '请先选择床次', icon: 'none' });
      return;
    }
    if (!rows.length) {
      wx.showToast({ title: mode === 'return' ? '请添加退料行' : '请先选择床次带出定额行', icon: 'none' });
      return;
    }
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      if (!r.lotId) {
        wx.showToast({ title: `第${i + 1}行请选择批次（缸号）`, icon: 'none' });
        return;
      }
      const qty = Number(r.qty);
      if (!r.qty || !Number.isFinite(qty) || qty <= 0) {
        wx.showToast({ title: `第${i + 1}行数量必须大于0`, icon: 'none' });
        return;
      }
      if (r.kind === 'manual' && !r.material) {
        wx.showToast({ title: `第${i + 1}行请选择物料`, icon: 'none' });
        return;
      }
    }
    const items = rows.map((r) => ({
      production_id: production.id,
      lot_id: r.lotId as number,
      qty: r.qty,
      bom_item_id: r.bomItemId || null,
      material_id: r.bomItemId ? null : r.material,
      // 定额行服务端按 bom_item 重算；手选行回传自报定额（空=null=无定额）
      qty_req: r.bomItemId ? null : (r.qtyReq === '' ? null : r.qtyReq),
      over_reason: r.overReason || ''
    }));
    const isReturn = mode === 'return';
    this.setData({ saving: true });
    createMaterialIssue({ type: isReturn ? 'return' : 'issue', items })
      .then((doc) => {
        this.setData({ saving: false });
        wx.showModal({
          title: isReturn ? '退料单已保存' : '领料单已保存',
          content: `单号 ${doc.issue_no}（草稿）。确认生效后${isReturn ? '冲减已领量' : '自动写入床次×批次血缘'}，是否立即确认？`,
          success: (m) => {
            if (!m.confirm) {
              this.resetForm();
              return;
            }
            confirmMaterialIssue(doc.id)
              .then(() => {
                wx.showToast({ title: '已确认生效', icon: 'success' });
                this.resetForm();
              })
              .catch(() => { /* toast 已由请求层弹出 */ });
          }
        });
      })
      .catch(() => this.setData({ saving: false }));
  },

  resetForm() {
    this.setData({ rows: [] });
    if (this.data.mode === 'issue' && this.data.production) {
      this.loadQuotaRows(this.data.production);
    }
  },

  /** 差异报表入口（挂 warehouse.manage 同点，页面已过门） */
  onGoDivergence() {
    wx.navigateTo({ url: '/packages/admin/issue-divergence/index' });
  }
});
