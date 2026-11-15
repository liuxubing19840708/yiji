/**
 * BOM 管理（tech 域 P1，B1-B5 口径）：
 * - 按款式查版本列表（version/status/item_count），active 徽标；一款式仅一个 active；
 * - 明细编辑（草稿）：物料选择器（搜 /tech/materials）+ 颜色/尺码（空=通码）+
 *   净用量 unit_usage + 损耗率 loss_rate（品类默认值预填：梭织6/针织8/辅料0.4）+
 *   自动算 gross_usage=净×(1+损耗) 展示；保存走 PUT items 全量替换；
 * - activate：草稿→生效（确认弹窗注明「旧生效版将自动停用」）；
 * - fork：另存新草稿版本（旧 active 自动停用留痕）；
 * - references 反查：P1 恒 {count:0}，按钮置灰（P2 采购/领料接入后开放）；
 * - 缩率在物料档（不在 BOM 行）：页面说明「定额=净用量×(1+缩率)×(1+损耗)，
 *   缩率按物料批次实测」。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { fetchStyles } from '../../../services/catalog';
import { Style } from '../../../services/types';
import {
  fetchBoms, fetchBom, createBom, updateBom, deleteBom, activateBom, forkBom,
  fetchMaterials, Material, Bom, BomStatus
} from '../../../services/tech';

/** 明细行编辑视图模型 */
interface EditorItem {
  material: number;
  material_code: string;
  material_name: string;
  unit: string;      // 主单位快照展示（=物料主单位）
  kind: string;      // main/lining/accessory
  kindIndex: number;
  color: string;
  size: string;
  unit_usage: string; // 净用量（输入串）
  loss_rate: string;  // 损耗率%（输入串）
  position: string;   // 部位（选填）
  gross: string;      // 定额展示 = 净×(1+损耗)（4 位截断）
}

interface BomVM {
  id: number;
  version: number;
  status: BomStatus;
  statusText: string;
  statusClass: string;
  itemCount: number;
  remark: string;
  draft: boolean;
  active: boolean;
}

const KIND_OPTIONS = [
  { label: '面料', value: 'main' },
  { label: '里料', value: 'lining' },
  { label: '辅料', value: 'accessory' }
];

const STATUS_TEXT: Record<string, string> = {
  draft: '草稿', active: '生效', deactivated: '已停用'
};

/** gross_usage 展示：净×(1+损耗)，最多 4 位小数去尾零 */
function computeGross(usage: string, loss: string): string {
  const u = Number(usage);
  const l = Number(loss);
  if (!Number.isFinite(u) || usage === '' || u <= 0) return '—';
  const gross = u * (1 + (Number.isFinite(l) ? l : 0) / 100);
  return String(Math.round(gross * 10000) / 10000);
}

/** 损耗率品类默认值预填（镜像后端 Material.default_loss_rate，仅预填、后端兜底） */
function defaultLossRate(m: Material): string {
  const attrs = (m.attrs || {}) as Record<string, unknown>;
  if (attrs.loss_rate !== undefined && attrs.loss_rate !== null && attrs.loss_rate !== '') {
    const v = Number(attrs.loss_rate);
    if (Number.isFinite(v)) return String(v);
  }
  if (m.category === 'accessory') return '0.4';
  if ((m.category === 'fabric' || m.category === 'lining')
    && String(attrs.fabric_type || '').toLowerCase() === 'knit') {
    return '8';
  }
  return '6';
}

function toVM(b: Bom): BomVM {
  return {
    id: b.id,
    version: b.version,
    status: b.status,
    statusText: STATUS_TEXT[b.status] || b.status,
    statusClass: b.status === 'active' ? 'badge-active'
      : (b.status === 'draft' ? 'badge-draft' : 'badge-gray'),
    itemCount: b.item_count || (b.items ? b.items.length : 0),
    remark: b.remark || '',
    draft: b.status === 'draft',
    active: b.status === 'active'
  };
}

Page({
  data: {
    denied: false, // 无 tech.bom 点（防御态，后端 403 兜底）
    // ---- 款式选择 ----
    styles: [] as Style[],
    styleNames: ['请选择款式'],
    styleIndex: 0,
    styleId: 0,
    // ---- 版本列表 ----
    boms: [] as BomVM[],
    loading: false,
    error: false,
    // ---- 明细编辑弹层 ----
    editorVisible: false,
    saving: false,
    readOnly: false,          // 非 draft 只读（修改走「改版」）
    editorBomId: 0,
    editorTitle: '',
    editorVersion: 0,
    items: [] as EditorItem[],
    // ---- 物料选择器（子弹层） ----
    pickerVisible: false,
    matKeyword: '',
    matOptions: [] as Material[],
    matLoading: false,
    matPickingIndex: -1       // -1 = 新增行，>=0 = 替换该行物料
  },

  onShow() {
    if (!requireLogin()) return;
    this.setData({ denied: !hasPerm('tech.bom') });
    if (!this.data.denied) this.loadStyles();
  },

  onPullDownRefresh() {
    this.loadStyles();
    wx.stopPullDownRefresh();
  },

  // ============ 款式选择 ============

  loadStyles() {
    fetchStyles({ page: 1, page_size: 200 })
      .then((res) => {
        const styles = res.results || [];
        const names = ['请选择款式'].concat(
          styles.map((s) => `${s.style_no} ${s.name}`.trim())
        );
        // 选中款式可能被删：保持原 id，找不到就回退「请选择」
        const kept = styles.findIndex((s) => s.id === this.data.styleId);
        this.setData({
          styles,
          styleNames: names,
          styleIndex: kept >= 0 ? kept + 1 : 0,
          styleId: kept >= 0 ? this.data.styleId : 0
        });
        this.loadBoms();
      })
      .catch(() => this.loadBoms());
  },

  onStyleChange(e: WechatMiniprogram.PickerChange) {
    const idx = Number(e.detail.value);
    const style = idx > 0 ? this.data.styles[idx - 1] : null;
    this.setData({ styleIndex: idx, styleId: style ? style.id : 0, boms: [] });
    this.loadBoms();
  },

  // ============ 版本列表 ============

  loadBoms() {
    if (!this.data.styleId) {
      this.setData({ boms: [], loading: false, error: false });
      return;
    }
    this.setData({ loading: true, error: false });
    fetchBoms({ style_id: this.data.styleId, page_size: 100 })
      .then((res) => {
        this.setData({ boms: (res.results || []).map(toVM), loading: false });
      })
      .catch(() => this.setData({ loading: false, error: true }));
  },

  /** 新建草稿 BOM（版本由后端自动取该款式最大版本+1；明细建后编辑） */
  onCreateBom() {
    if (!this.data.styleId) {
      wx.showToast({ title: '请先选择款式', icon: 'none' });
      return;
    }
    createBom({ style: this.data.styleId })
      .then((created: Bom) => {
        wx.showToast({ title: `已创建 v${created.version} 草稿`, icon: 'none' });
        this.loadBoms();
        this.openEditor(created.id);
      })
      .catch(() => { /* toast 已由请求层弹出 */ });
  },

  // ============ 卡片操作 ============

  onEditDetail(e: WechatMiniprogram.TouchEvent) {
    const bom = this.data.boms[Number(e.currentTarget.dataset.index)];
    if (bom) this.openEditor(bom.id);
  },

  onActivate(e: WechatMiniprogram.TouchEvent) {
    const bom = this.data.boms[Number(e.currentTarget.dataset.index)];
    if (!bom) return;
    wx.showModal({
      title: '激活生效',
      content: `确定将 v${bom.version} 设为生效版本？旧生效版将自动停用（一款式仅一个生效版）。`,
      success: (m) => {
        if (!m.confirm) return;
        activateBom(bom.id)
          .then(() => {
            wx.showToast({ title: '已激活', icon: 'success' });
            this.loadBoms();
          })
          .catch(() => { /* toast 已由请求层弹出 */ });
      }
    });
  },

  /** fork 改版：另存新草稿版本，旧 active 自动停用留痕（B4） */
  onFork(e: WechatMiniprogram.TouchEvent) {
    const bom = this.data.boms[Number(e.currentTarget.dataset.index)];
    if (!bom) return;
    wx.showModal({
      title: '改版',
      content: `将以 v${bom.version} 为底稿另存新草稿版本（旧生效版自动停用留痕），确定？`,
      success: (m) => {
        if (!m.confirm) return;
        forkBom(bom.id)
          .then((created: Bom) => {
            wx.showToast({ title: `已另存 v${created.version} 草稿`, icon: 'none' });
            this.loadBoms();
            this.openEditor(created.id);
          })
          .catch(() => { /* toast 已由请求层弹出 */ });
      }
    });
  },

  onDeleteBom(e: WechatMiniprogram.TouchEvent) {
    const bom = this.data.boms[Number(e.currentTarget.dataset.index)];
    if (!bom || !bom.draft) return;
    wx.showModal({
      title: '删除草稿',
      content: `确定删除 v${bom.version} 草稿？仅草稿可删除，生效/停用版本留痕不可删。`,
      confirmColor: '#F53F3F',
      success: (m) => {
        if (!m.confirm) return;
        deleteBom(bom.id)
          .then(() => {
            wx.showToast({ title: '已删除', icon: 'success' });
            this.loadBoms();
          })
          .catch(() => { /* toast 已由请求层弹出 */ });
      }
    });
  },

  /** 引用反查：P1 恒 {count:0}，P2（采购/领料）接入后开放（按钮置灰，此处只提示） */
  onReferences() {
    wx.showToast({ title: '引用反查将于 P2 接入后开放', icon: 'none' });
  },

  // ============ 明细编辑 ============

  openEditor(bomId: number) {
    this.setData({ editorVisible: true, editorBomId: bomId, items: [] });
    fetchBom(bomId)
      .then((bom: Bom) => {
        this.setData({
          readOnly: bom.status !== 'draft',
          editorTitle: `${bom.style_no} ${bom.style_name}`.trim(),
          editorVersion: bom.version,
          items: (bom.items || []).map((it) => this.toEditorItem(it))
        });
      })
      .catch(() => {
        this.setData({ editorVisible: false });
      });
  },

  toEditorItem(it: {
    material: number; material_code?: string; material_name?: string; unit?: string;
    kind?: string; color?: string; size?: string; unit_usage?: string; loss_rate?: string;
    position?: string;
  }): EditorItem {
    const kind = it.kind || 'main';
    const kindIndex = Math.max(0, KIND_OPTIONS.findIndex((k) => k.value === kind));
    return {
      material: it.material,
      material_code: it.material_code || '',
      material_name: it.material_name || '',
      unit: it.unit || '',
      kind,
      kindIndex,
      color: it.color || '',
      size: it.size || '',
      unit_usage: it.unit_usage ? String(it.unit_usage) : '',
      loss_rate: it.loss_rate ? String(it.loss_rate) : '',
      position: it.position || '',
      gross: computeGross(String(it.unit_usage || ''), String(it.loss_rate || '0'))
    };
  },

  onCloseEditor() {
    this.setData({ editorVisible: false });
  },

  noop() { /* 阻止冒泡 */ },

  recalcGross(idx: number) {
    const row = this.data.items[idx];
    if (!row) return;
    this.setData({
      [`items[${idx}].gross`]: computeGross(row.unit_usage, row.loss_rate)
    });
  },

  onItemKind(e: WechatMiniprogram.PickerChange) {
    const idx = Number(e.currentTarget.dataset.index);
    this.setData({
      [`items[${idx}].kindIndex`]: Number(e.detail.value),
      [`items[${idx}].kind`]: KIND_OPTIONS[Number(e.detail.value)].value
    });
  },

  onItemColor(e: WechatMiniprogram.Input) {
    const idx = Number(e.currentTarget.dataset.index);
    this.setData({ [`items[${idx}].color`]: e.detail.value });
  },

  onItemSize(e: WechatMiniprogram.Input) {
    const idx = Number(e.currentTarget.dataset.index);
    this.setData({ [`items[${idx}].size`]: e.detail.value });
  },

  onItemUsage(e: WechatMiniprogram.Input) {
    const idx = Number(e.currentTarget.dataset.index);
    this.setData({ [`items[${idx}].unit_usage`]: e.detail.value });
    this.recalcGross(idx);
  },

  onItemLoss(e: WechatMiniprogram.Input) {
    const idx = Number(e.currentTarget.dataset.index);
    this.setData({ [`items[${idx}].loss_rate`]: e.detail.value });
    this.recalcGross(idx);
  },

  onItemPosition(e: WechatMiniprogram.Input) {
    const idx = Number(e.currentTarget.dataset.index);
    this.setData({ [`items[${idx}].position`]: e.detail.value });
  },

  onRemoveItem(e: WechatMiniprogram.TouchEvent) {
    if (this.data.readOnly) return;
    const idx = Number(e.currentTarget.dataset.index);
    const items = this.data.items.slice();
    if (idx < 0 || idx >= items.length) return;
    items.splice(idx, 1);
    this.setData({ items });
  },

  // ---- 物料选择器（子弹层，搜 /tech/materials） ----

  openPicker(matPickingIndex: number) {
    this.setData({ pickerVisible: true, matPickingIndex, matKeyword: '', matOptions: [] });
    this.searchMaterials('');
  },

  onAddItem() {
    if (this.data.readOnly) return;
    this.openPicker(-1);
  },

  /** 点某行物料区 → 打开选择器替换该行物料 */
  onRowPickMaterial(e: WechatMiniprogram.TouchEvent) {
    if (this.data.readOnly) return;
    const idx = Number(e.currentTarget.dataset.index);
    this.openPicker(idx);
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
    const target = this.data.matPickingIndex;
    const item: EditorItem = {
      material: m.id,
      material_code: m.code,
      material_name: m.name,
      unit: m.unit,
      kind: 'main',
      kindIndex: 0,
      color: '',
      size: '',
      unit_usage: '',
      loss_rate: defaultLossRate(m), // 品类默认值预填（后端兜底同口径）
      position: '',
      gross: '—'
    };
    if (target >= 0 && target < this.data.items.length) {
      this.setData({ [`items[${target}]`]: item });
    } else {
      this.setData({ items: this.data.items.concat([item]) });
    }
    this.setData({ pickerVisible: false });
  },

  onClosePicker() {
    this.setData({ pickerVisible: false });
  },

  // ---- 保存（PUT items 全量替换） ----

  onSaveEditor() {
    const items = this.data.items;
    if (!items.length) {
      wx.showToast({ title: '请至少添加一条明细', icon: 'none' });
      return;
    }
    const seen = new Set<string>();
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (!it.material) {
        wx.showToast({ title: `第${i + 1}行请选择物料`, icon: 'none' });
        return;
      }
      const usage = Number(it.unit_usage);
      if (!it.unit_usage || !Number.isFinite(usage) || usage <= 0) {
        wx.showToast({ title: `第${i + 1}行单耗必须大于0`, icon: 'none' });
        return;
      }
      const loss = it.loss_rate === '' ? NaN : Number(it.loss_rate);
      if (Number.isFinite(loss) && loss < 0) {
        wx.showToast({ title: `第${i + 1}行损耗率不能为负数`, icon: 'none' });
        return;
      }
      const key = `${it.material}|${it.color.trim()}|${it.size.trim()}`;
      if (seen.has(key)) {
        wx.showToast({ title: `第${i + 1}行同物料同色码重复`, icon: 'none' });
        return;
      }
      seen.add(key);
    }
    const payload = items.map((it) => ({
      material: it.material,
      kind: it.kind,
      color: it.color.trim(),
      size: it.size.trim(),
      unit_usage: it.unit_usage,
      loss_rate: it.loss_rate === '' ? null : it.loss_rate,
      position: it.position || ''
    }));
    this.setData({ saving: true });
    updateBom(this.data.editorBomId, { items: payload })
      .then(() => {
        this.setData({ saving: false, editorVisible: false });
        wx.showToast({ title: '明细已保存', icon: 'success' });
        this.loadBoms();
      })
      .catch(() => this.setData({ saving: false }));
  }
});
