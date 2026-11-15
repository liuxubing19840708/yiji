/**
 * 物料管理（tech 域 P1，A1/A2/A4 口径）：
 * - 列表：keyword/category 过滤；主/副单位、克重、幅宽、缩率展示；
 *   conversion_ready=false（缺克重或幅宽）标「待补参数」徽标；
 * - 编辑弹层：code/name/category/unit/unit_alt/weight_gsm/width_cm/shrink_rate/
 *   legacy_code；填副单位缺克重/幅宽前端预校验（后端 400 中文报错由 http 层透出）；
 *   attrs/price_reference 透传不丢（后端 PUT 全量语义，不传会被清空）；
 * - 停用/启用代替删除（PROTECT：被 BOM 引用的物料不可删）；
 * - Excel 导入：xlsx 上传 → {created,updated,errors[{row,code,reason}]}，
 *   all-or-nothing（任一行失败整批不入库），错误行可复制。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import {
  fetchMaterials, createMaterial, updateMaterial, importMaterialsFile,
  Material, MaterialPayload, ImportResult, ImportRowError
} from '../../../services/tech';

const PAGE_SIZE = 20;

/** 分类筛选 chips（'' = 全部；值与 Material.Category 一致） */
const CATEGORY_FILTERS = [
  { label: '全部', value: '' },
  { label: '面料', value: 'fabric' },
  { label: '里料', value: 'lining' },
  { label: '辅料', value: 'accessory' },
  { label: '包装', value: 'packing' }
];

/** 编辑弹层分类 picker（与后端 CATEGORY_BY_NAME 中文名对应） */
const CATEGORY_OPTIONS = [
  { label: '面料', value: 'fabric' },
  { label: '里料', value: 'lining' },
  { label: '辅料', value: 'accessory' },
  { label: '包装', value: 'packing' }
];
const CATEGORY_TEXT: Record<string, string> = {
  fabric: '面料', lining: '里料', accessory: '辅料', packing: '包装'
};

/** 单价单位下拉（D19）：''=按主单位；物料级缺省，存储值恒为主单位规范化 */
const PRICE_UNIT_OPTIONS = [
  { label: '按主单位', value: '' },
  { label: '元/公斤', value: 'kg' },
  { label: '元/米', value: 'm' }
];

/** Decimal 展示修剪："190.00" → "190"；"6.50" → "6.5"；空值 → '—' */
function trimDec(raw: string | null | undefined, dash = '—'): string {
  if (raw === null || raw === undefined || raw === '') return dash;
  const n = Number(raw);
  return Number.isFinite(n) ? String(n) : dash;
}

interface MaterialForm {
  id: number;
  code: string;
  name: string;
  categoryIndex: number;
  unit: string;
  unit_alt: string;
  weight_gsm: string;
  width_cm: string;
  shrink_rate: string;
  legacy_code: string;
  price_reference: string | number; // 透传（后端 PUT 全量语义防清空）
  priceUnitIndex: number; // 单价单位（D19）：0=按主单位 1=元/公斤 2=元/米
  safety_stock: string; // P2.5 安全库存（主单位；空=0 不设水位）
  attrs: Record<string, unknown> | null;
}

interface MaterialVM extends Material {
  categoryText: string;
  unitAltText: string;
  weightText: string;
  widthText: string;
  shrinkText: string;
  pendingParams: boolean; // conversion_ready=false → 「待补参数」
  disabled: boolean;
}

function toVM(m: Material): MaterialVM {
  return {
    ...m,
    categoryText: CATEGORY_TEXT[m.category] || m.category,
    unitAltText: m.unit_alt || '—',
    weightText: trimDec(m.weight_gsm),
    widthText: trimDec(m.width_cm),
    shrinkText: m.shrink_rate === null || m.shrink_rate === '' ? '—' : `${trimDec(m.shrink_rate)}%`,
    pendingParams: !m.conversion_ready,
    disabled: m.status === 'disabled'
  };
}

Page({
  data: {
    denied: false,          // 无 tech.material 点（防御态，后端 403 兜底）
    keyword: '',
    categoryIndex: 0,
    categoryLabels: CATEGORY_FILTERS.map((c) => c.label),
    materials: [] as MaterialVM[],
    page: 1,
    hasMore: false,
    loading: false,
    error: false,
    // ---- 编辑弹层 ----
    formVisible: false,
    saving: false,
    priceUnitLabels: PRICE_UNIT_OPTIONS.map((o) => o.label),
    form: {
      id: 0,
      code: '',
      name: '',
      categoryIndex: 0,
      unit: '',
      unit_alt: '',
      weight_gsm: '',
      width_cm: '',
      shrink_rate: '',
      legacy_code: '',
      price_reference: '',
      priceUnitIndex: 0,
      safety_stock: '',
      attrs: null
    } as MaterialForm,
    // ---- 导入弹层 ----
    importVisible: false,
    importing: false,
    importResult: null as ImportResult | null,
    importErrorLines: [] as string[] // 「第N行 编码 原因」逐行，可复制
  },

  onShow() {
    if (!requireLogin()) return;
    this.setData({ denied: !hasPerm('tech.material') });
    if (!this.data.denied) this.reload();
  },

  onPullDownRefresh() {
    this.reload();
    wx.stopPullDownRefresh();
  },

  // ============ 列表 ============

  reload() {
    const params: Record<string, unknown> = { page: 1, page_size: PAGE_SIZE };
    const kw = this.data.keyword.trim();
    const category = CATEGORY_FILTERS[this.data.categoryIndex].value;
    if (kw) params.keyword = kw;
    if (category) params.category = category;
    this.setData({ loading: true, error: false, page: 1, materials: [] });
    fetchMaterials(params as Parameters<typeof fetchMaterials>[0])
      .then((res) => {
        this.setData({
          materials: (res.results || []).map(toVM),
          hasMore: !!res.next,
          loading: false
        });
      })
      .catch(() => this.setData({ loading: false, error: true }));
  },

  loadMore() {
    if (!this.data.hasMore || this.data.loading) return;
    const nextPage = this.data.page + 1;
    const params: Record<string, unknown> = { page: nextPage, page_size: PAGE_SIZE };
    const kw = this.data.keyword.trim();
    const category = CATEGORY_FILTERS[this.data.categoryIndex].value;
    if (kw) params.keyword = kw;
    if (category) params.category = category;
    this.setData({ loading: true });
    fetchMaterials(params as Parameters<typeof fetchMaterials>[0])
      .then((res) => {
        this.setData({
          materials: this.data.materials.concat((res.results || []).map(toVM)),
          hasMore: !!res.next,
          page: nextPage,
          loading: false
        });
      })
      .catch(() => this.setData({ loading: false }));
  },

  onReachBottom() {
    this.loadMore();
  },

  onKeyword(e: WechatMiniprogram.Input) {
    this.setData({ keyword: e.detail.value });
  },

  onSearch() {
    this.reload();
  },

  onCategory(e: WechatMiniprogram.TouchEvent) {
    const idx = Number((e.currentTarget.dataset as { index?: number }).index || 0);
    if (idx === this.data.categoryIndex) return;
    this.setData({ categoryIndex: idx }, () => this.reload());
  },

  // ============ 停用 / 启用（代替删除：PROTECT 被 BOM 引用不可删） ============

  onCardMenu(e: WechatMiniprogram.TouchEvent) {
    const m = this.data.materials[Number(e.currentTarget.dataset.index)];
    if (!m) return;
    const actions: Array<{ label: string; run: () => void }> = [
      { label: '编辑物料', run: () => this.openForm(m) }
    ];
    if (m.disabled) {
      actions.push({ label: '启用', run: () => this.toggleStatus(m, 'active') });
    } else {
      actions.push({ label: '停用', run: () => this.toggleStatus(m, 'disabled') });
    }
    wx.showActionSheet({
      itemList: actions.map((a) => a.label),
      success: (res) => {
        const act = actions[res.tapIndex];
        if (act) act.run();
      }
    });
  },

  /** 停用/启用：PUT 全量语义，必须回传全部字段（防 attrs/price_reference 被清空） */
  toggleStatus(m: MaterialVM, status: 'active' | 'disabled') {
    const payload = this.buildPayload({
      code: m.code,
      name: m.name,
      categoryIndex: Math.max(0, CATEGORY_OPTIONS.findIndex((c) => c.value === m.category)),
      unit: m.unit,
      unit_alt: m.unit_alt,
      weight_gsm: m.weight_gsm === null ? '' : String(m.weight_gsm),
      width_cm: m.width_cm === null ? '' : String(m.width_cm),
      shrink_rate: m.shrink_rate === null ? '' : String(m.shrink_rate),
      legacy_code: m.legacy_code,
      price_reference: m.price_reference === null ? '' : String(m.price_reference),
      priceUnitIndex: Math.max(0, PRICE_UNIT_OPTIONS.findIndex((o) => o.value === (m.price_unit || ''))),
      safety_stock: m.safety_stock === null ? '' : String(m.safety_stock),
      attrs: m.attrs || null
    });
    updateMaterial(m.id, { ...payload, status })
      .then(() => {
        wx.showToast({ title: status === 'disabled' ? '已停用' : '已启用', icon: 'success' });
        this.reload();
      })
      .catch(() => { /* toast 已由请求层弹出 */ });
  },

  // ============ 新建 / 编辑 ============

  onAdd() {
    this.openForm(null);
  },

  openForm(m: MaterialVM | null) {
    const raw = (m || {}) as unknown as Record<string, unknown>;
    const s = (v: unknown) => (v === null || v === undefined ? '' : String(v));
    this.setData({
      formVisible: true,
      form: {
        id: m ? m.id : 0,
        code: s(raw.code),
        name: s(raw.name),
        categoryIndex: m
          ? Math.max(0, CATEGORY_OPTIONS.findIndex((c) => c.value === m.category))
          : 0,
        unit: s(raw.unit),
        unit_alt: s(raw.unit_alt),
        weight_gsm: s(raw.weight_gsm),
        width_cm: s(raw.width_cm),
        shrink_rate: s(raw.shrink_rate),
        legacy_code: s(raw.legacy_code),
        price_reference: s(raw.price_reference),
        priceUnitIndex: Math.max(0, PRICE_UNIT_OPTIONS.findIndex((o) => o.value === s(raw.price_unit))),
        safety_stock: s(raw.safety_stock),
        attrs: (raw.attrs as Record<string, unknown>) || null
      }
    });
  },

  onFormCode(e: WechatMiniprogram.Input) { this.setData({ 'form.code': e.detail.value }); },
  onFormName(e: WechatMiniprogram.Input) { this.setData({ 'form.name': e.detail.value }); },
  onFormUnit(e: WechatMiniprogram.Input) { this.setData({ 'form.unit': e.detail.value }); },
  onFormUnitAlt(e: WechatMiniprogram.Input) { this.setData({ 'form.unit_alt': e.detail.value }); },
  onFormWeight(e: WechatMiniprogram.Input) { this.setData({ 'form.weight_gsm': e.detail.value }); },
  onFormWidth(e: WechatMiniprogram.Input) { this.setData({ 'form.width_cm': e.detail.value }); },
  onFormShrink(e: WechatMiniprogram.Input) { this.setData({ 'form.shrink_rate': e.detail.value }); },
  onFormLegacy(e: WechatMiniprogram.Input) { this.setData({ 'form.legacy_code': e.detail.value }); },

  onFormSafety(e: WechatMiniprogram.Input) { this.setData({ 'form.safety_stock': e.detail.value }); },

  onFormCategory(e: WechatMiniprogram.PickerChange) {
    this.setData({ 'form.categoryIndex': Number(e.detail.value) });
  },

  onFormPriceUnit(e: WechatMiniprogram.PickerChange) {
    this.setData({ 'form.priceUnitIndex': Number(e.detail.value) });
  },

  onCloseForm() {
    this.setData({ formVisible: false });
  },

  noop() { /* 阻止冒泡 */ },

  /** 组装提交体（编辑回传 attrs/price_reference 防 PUT 全量清空） */
  buildPayload(f: Omit<MaterialForm, 'id'>): MaterialPayload {
    const num = (v: string | number) => (v === '' || v === null || v === undefined ? null : v);
    return {
      code: f.code.trim(),
      name: f.name.trim(),
      category: CATEGORY_OPTIONS[f.categoryIndex].value,
      unit: f.unit.trim(),
      unit_alt: f.unit_alt.trim(),
      weight_gsm: num(f.weight_gsm),
      width_cm: num(f.width_cm),
      shrink_rate: num(f.shrink_rate),
      price_reference: num(f.price_reference),
      price_unit: PRICE_UNIT_OPTIONS[f.priceUnitIndex].value,
      // P2.5 安全库存：空串=0（不设水位），非负数
      safety_stock: f.safety_stock === '' ? 0 : num(f.safety_stock),
      legacy_code: f.legacy_code.trim(),
      attrs: f.attrs || {}
    };
  },

  onSave() {
    const f = this.data.form;
    if (!f.code.trim()) {
      wx.showToast({ title: '请填写物料编码', icon: 'none' });
      return;
    }
    if (!f.name.trim()) {
      wx.showToast({ title: '请填写品名', icon: 'none' });
      return;
    }
    if (!f.unit.trim()) {
      wx.showToast({ title: '请填写主单位', icon: 'none' });
      return;
    }
    // 双单位换算防线（A 组）：填了副单位即声明公斤↔米换算，克重/幅宽必填
    if (f.unit_alt.trim() && (!f.weight_gsm.trim() || !f.width_cm.trim())) {
      wx.showToast({ title: '已填副单位，须补录克重与幅宽', icon: 'none' });
      return;
    }
    const payload = this.buildPayload(f);
    this.setData({ saving: true });
    const done = () => {
      this.setData({ saving: false, formVisible: false });
      wx.showToast({ title: '已保存', icon: 'success' });
      this.reload();
    };
    if (f.id) {
      updateMaterial(f.id, payload).then(done).catch(() => this.setData({ saving: false }));
    } else {
      createMaterial(payload).then(done).catch(() => this.setData({ saving: false }));
    }
  },

  // ============ Excel 导入（all-or-nothing） ============

  onOpenImport() {
    this.setData({ importVisible: true, importResult: null, importErrorLines: [] });
  },

  onCloseImport() {
    this.setData({ importVisible: false });
  },

  /** 从微信聊天记录选 .xlsx（小程序无文件系统选择器，聊天文件是标准入口） */
  onPickImportFile() {
    if (this.data.importing) return;
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['xlsx'],
      success: (res) => {
        const file = res.tempFiles && res.tempFiles[0];
        if (!file) return;
        if (!/\.xlsx$/i.test(file.name || '')) {
          wx.showToast({ title: '仅支持 .xlsx 文件', icon: 'none' });
          return;
        }
        this.doImport(file.path);
      },
      fail: () => { /* 用户取消 */ }
    });
  },

  doImport(filePath: string) {
    this.setData({ importing: true });
    wx.showLoading({ title: '导入中…', mask: true });
    importMaterialsFile(filePath)
      .then((result: ImportResult) => {
        const lines: string[] = (result.errors || []).map((err: ImportRowError) => (
          `第${err.row}行 ${err.code || ''} ${err.reason}`.trim()
        ));
        this.setData({
          importResult: result,
          importErrorLines: lines
        });
        if (!lines.length) {
          wx.showToast({ title: `导入成功：新建${result.created} 更新${result.updated}`, icon: 'success' });
          this.reload();
        }
      })
      .catch(() => { /* toast 已在 services 层弹出 */ })
      .then(() => {
        wx.hideLoading();
        this.setData({ importing: false });
      });
  },

  /** 错误行列表复制（对账修正用） */
  onCopyErrors() {
    if (!this.data.importErrorLines.length) return;
    wx.setClipboardData({
      data: this.data.importErrorLines.join('\n'),
      success: () => wx.showToast({ title: '错误行已复制', icon: 'success' })
    });
  }
});
