/**
 * 报价新建/编辑页（quotation 域 P2，quotation.manage）：
 * - 新建：选款式/客户/数量/日期；用工行缺省带出（工价快照/倍率 2.3/小单溢价按
 *   qty 规则 <300 → +30%）——labor_base/rate/small_lot_addon 留空即走后端缺省；
 * - 用料行两级用量：「从BOM带出」（active BOM 定额行，bom_item 带 material 与
 *   参照单耗）或「手选物料」；单耗/单价行内可改（单价缺省取物料参考价）；
 * - 倍率超 1.8-3.0 客户端软提示直显不改值（后端 rate_warning 同口径）；
 * - 附加费 addon_amount（上机费/制版费）单独项；
 * - 金额均为字符串透传，前端不自行汇总合计（合计由服务端重算下发）。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import {
  fetchQuotation, createQuotation, updateQuotation, QuotationDetail
} from '../../../services/quotation';
import { fetchStyles, fetchCustomers } from '../../../services/catalog';
import { fetchMaterials, fetchBoms, Material } from '../../../services/tech';

/** D2 倍率常规区间（与后端 services RATE_MIN/RATE_MAX 一致，仅提示不拦截） */
const RATE_MIN = 1.8;
const RATE_MAX = 3.0;

interface MatRow {
  material: number;
  bomItemId: number | null;
  title: string;
  unit: string;
  usage: string;
  price: string;
}

Page({
  /** 私有态：档案选项收敛计数 + 编辑态详情缓存（非渲染字段） */
  _optionsLeft: 0,
  _quote: null as QuotationDetail | null,

  data: {
    denied: false,
    id: 0,
    saving: false,
    // ---- 档案选项 ----
    styles: [] as Array<{ id: number; label: string }>,
    styleNames: ['请选择款式'],
    styleIndex: 0,
    customers: [] as Array<{ id: number; label: string }>,
    customerNames: ['不关联客户'],
    customerIndex: 0,
    materials: [] as Material[],
    // ---- 表单 ----
    qty: '',
    date: '',
    remark: '',
    rows: [] as MatRow[],
    laborBase: '',
    rate: '2.3',
    smallLotAddon: '',
    addonAmount: '',
    rateWarning: '',
    // ---- 加物料行弹层 ----
    pickVisible: false,
    materialNames: [] as string[],
    materialIndex: 0
  },

  onLoad(query: Record<string, string | undefined>) {
    const id = Number(query.id || 0);
    this.setData({
      id,
      denied: !hasPerm('quotation.manage'),
      date: this.today()
    });
    if (!id) wx.setNavigationBarTitle({ title: '新建报价' });
  },

  onShow() {
    if (!requireLogin()) return;
    if (this.data.denied) return;
    this.loadOptions();
    if (this.data.id && !this._quote) this.loadQuote();
  },

  /** 编辑态回填：等款式/客户选项与详情都就绪再填（避免索引映射落空） */
  tryPrefill() {
    if (!this._quote || this._optionsLeft > 0) return;
    const q = this._quote;
    wx.setNavigationBarTitle({ title: `编辑 ${q.quote_no}` });
    const styleIndex = this.data.styles.findIndex((s) => s.id === q.style) + 1;
    const customerIndex = q.customer
      ? this.data.customers.findIndex((c) => c.id === q.customer) + 1 : 0;
    const rows: MatRow[] = (q.materials || []).map((m) => ({
      material: m.material,
      bomItemId: m.bom_item_id,
      title: `${m.material_code || ''} ${m.material_name || ''}`.trim(),
      unit: m.material_unit || '',
      usage: m.quote_unit_usage,
      price: m.price
    }));
    this.setData({
      styleIndex: Math.max(styleIndex, 0),
      customerIndex: Math.max(customerIndex, 0),
      qty: String(q.qty),
      date: (q.quote_date || '').slice(0, 10) || this.data.date,
      remark: q.remark || '',
      rows,
      laborBase: q.labor ? q.labor.labor_base : '',
      rate: q.labor ? q.labor.rate : '2.3',
      smallLotAddon: q.labor ? q.labor.small_lot_addon : '',
      addonAmount: q.labor ? q.labor.addon_amount : ''
    });
    this.checkRate(this.data.rate);
  },

  loadQuote() {
    fetchQuotation(this.data.id)
      .then((q: QuotationDetail) => {
        this._quote = q;
        this.tryPrefill();
      })
      .catch(() => {
        wx.showToast({ title: '报价加载失败', icon: 'none' });
      });
  },

  today(): string {
    const d = new Date();
    const m = `${d.getMonth() + 1}`.padStart(2, '0');
    const day = `${d.getDate()}`.padStart(2, '0');
    return `${d.getFullYear()}-${m}-${day}`;
  },

  /** 三个档案选项各自收敛后统一置就绪（编辑回填依赖三个列表映射） */
  settleOptions() {
    this._optionsLeft -= 1;
    if (this._optionsLeft <= 0) {
      this.setData({ optionsReady: true });
      this.tryPrefill();
    }
  },

  loadOptions() {
    this._optionsLeft = 3;
    fetchStyles({ page: 1, page_size: 200 })
      .then((res) => {
        const styles = (res.results || []).map((s) => ({
          id: s.id, label: `${s.style_no}${s.name ? ' ' + s.name : ''}`
        }));
        this.setData({
          styles,
          styleNames: ['请选择款式'].concat(styles.map((s) => s.label))
        });
      })
      .catch(() => undefined)
      .then(() => this.settleOptions());
    fetchCustomers({ page: 1, page_size: 200 })
      .then((res) => {
        const customers = (res.results || []).map((c) => ({ id: c.id, label: c.name }));
        this.setData({
          customers,
          customerNames: ['不关联客户'].concat(customers.map((c) => c.label))
        });
      })
      .catch(() => undefined)
      .then(() => this.settleOptions());
    fetchMaterials({ page: 1, page_size: 200 })
      .then((res) => {
        const materials = res.results || [];
        this.setData({
          materials,
          materialNames: materials.map((m) => `${m.code} ${m.name}${m.unit ? '（' + m.unit + '）' : ''}`)
        });
      })
      .catch(() => undefined)
      .then(() => this.settleOptions());
  },

  // ============ 表单输入 ============

  onStyleChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ styleIndex: Number(e.detail.value) });
  },

  onCustomerChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ customerIndex: Number(e.detail.value) });
  },

  onQty(e: WechatMiniprogram.Input) {
    this.setData({ qty: e.detail.value });
  },

  onDateChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ date: String(e.detail.value) });
  },

  onRemark(e: WechatMiniprogram.Input) {
    this.setData({ remark: e.detail.value });
  },

  onLaborBase(e: WechatMiniprogram.Input) {
    this.setData({ laborBase: e.detail.value });
  },

  onRate(e: WechatMiniprogram.Input) {
    this.setData({ rate: e.detail.value });
    this.checkRate(e.detail.value);
  },

  /** 倍率软提示：区间外直显不改值（D2 口径，后端 rate_warning 同步兜底） */
  checkRate(v: string) {
    const n = Number(v);
    if (v === '' || !Number.isFinite(n)) {
      this.setData({ rateWarning: '' });
      return;
    }
    if (n < RATE_MIN) {
      this.setData({ rateWarning: `倍率 ${v} 低于常规区间下限 ${RATE_MIN}（小厂淡季口径，请确认）` });
    } else if (n > RATE_MAX) {
      this.setData({ rateWarning: `倍率 ${v} 高于常规区间上限 ${RATE_MAX}（请确认）` });
    } else {
      this.setData({ rateWarning: '' });
    }
  },

  onSmallLotAddon(e: WechatMiniprogram.Input) {
    this.setData({ smallLotAddon: e.detail.value });
  },

  onAddonAmount(e: WechatMiniprogram.Input) {
    this.setData({ addonAmount: e.detail.value });
  },

  // ============ 用料行 ============

  /** 从 BOM 带出：该款式 active BOM 的定额行（bom_item 带出 material 与参照单耗） */
  onPullFromBom() {
    const idx = this.data.styleIndex;
    if (idx <= 0 || !this.data.styles[idx - 1]) {
      wx.showToast({ title: '请先选款式', icon: 'none' });
      return;
    }
    const styleId = this.data.styles[idx - 1].id;
    fetchBoms({ style_id: styleId, status: 'active', page: 1, page_size: 5 })
      .then((res) => {
        const bom = (res.results || [])[0];
        if (!bom || !(bom.items || []).length) {
          wx.showToast({ title: '该款式无生效BOM明细', icon: 'none' });
          return;
        }
        const rows = (bom.items || []).map((it) => ({
          material: it.material,
          bomItemId: it.id,
          title: `${it.material_code} ${it.material_name}`,
          unit: it.unit || '',
          usage: it.unit_usage,
          price: this.defaultPrice(it.material)
        }));
        this.setData({ rows: this.data.rows.concat(rows) });
      })
      .catch(() => wx.showToast({ title: 'BOM 带出失败', icon: 'none' }));
  },

  /** 单价缺省：物料参考价（无则 0，可改） */
  defaultPrice(materialId: number): string {
    const m = this.data.materials.find((x) => x.id === materialId);
    return m && m.price_reference ? String(m.price_reference) : '0';
  },

  onOpenPick() {
    if (!this.data.materials.length) {
      wx.showToast({ title: '物料档为空，请先建物料', icon: 'none' });
      return;
    }
    this.setData({ pickVisible: true, materialIndex: 0 });
  },

  onMaterialChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ materialIndex: Number(e.detail.value) });
  },

  onClosePick() {
    this.setData({ pickVisible: false });
  },

  noop() { /* 阻止冒泡 */ },

  onConfirmPick() {
    const idx = this.data.materialIndex;
    const m = this.data.materials[idx];
    if (!m) return;
    this.setData({
      pickVisible: false,
      rows: this.data.rows.concat({
        material: m.id,
        bomItemId: null,
        title: `${m.code} ${m.name}`,
        unit: m.unit || '',
        usage: '1',
        price: m.price_reference ? String(m.price_reference) : '0'
      })
    });
  },

  onRowUsage(e: WechatMiniprogram.Input) {
    const i = Number(e.currentTarget.dataset.index);
    this.setData({ [`rows[${i}].usage`]: e.detail.value });
  },

  onRowPrice(e: WechatMiniprogram.Input) {
    const i = Number(e.currentTarget.dataset.index);
    this.setData({ [`rows[${i}].price`]: e.detail.value });
  },

  onRemoveRow(e: WechatMiniprogram.TouchEvent) {
    const i = Number(e.currentTarget.dataset.index);
    const rows = this.data.rows.slice();
    rows.splice(i, 1);
    this.setData({ rows });
  },

  // ============ 保存 ============

  onSave() {
    if (this.data.saving) return;
    const { id, styleIndex, styles, customerIndex, customers, qty, date,
      remark, rows, laborBase, rate, smallLotAddon, addonAmount } = this.data;
    const styleId = styleIndex > 0 && styles[styleIndex - 1] ? styles[styleIndex - 1].id : 0;
    if (!styleId) {
      wx.showToast({ title: '请选择款式', icon: 'none' });
      return;
    }
    const qtyNum = Number(qty);
    if (!qty || !Number.isInteger(qtyNum) || qtyNum <= 0) {
      wx.showToast({ title: '请填写大于 0 的报价数量', icon: 'none' });
      return;
    }
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      const usage = Number(r.usage);
      const price = Number(r.price);
      if (!r.usage || !Number.isFinite(usage) || usage <= 0) {
        wx.showToast({ title: `第 ${i + 1} 行报价单耗须大于 0`, icon: 'none' });
        return;
      }
      if (r.price === '' || !Number.isFinite(price) || price < 0) {
        wx.showToast({ title: `第 ${i + 1} 行单价不能为负`, icon: 'none' });
        return;
      }
    }
    const labor: Record<string, string | number> = {};
    if (laborBase !== '') labor.labor_base = laborBase;
    if (rate !== '') labor.rate = rate;
    if (smallLotAddon !== '') labor.small_lot_addon = smallLotAddon;
    if (addonAmount !== '') labor.addon_amount = addonAmount;
    const payload = {
      style: styleId,
      customer: customerIndex > 0 && customers[customerIndex - 1]
        ? customers[customerIndex - 1].id : null,
      qty: qtyNum,
      quote_date: date || undefined,
      remark: remark.trim() || '',
      materials: rows.map((r) => ({
        material: r.material,
        ...(r.bomItemId ? { bom_item: r.bomItemId } : {}),
        quote_unit_usage: r.usage,
        price: r.price
      })),
      ...(Object.keys(labor).length ? { labor } : {})
    };
    this.setData({ saving: true });
    const req = id
      ? updateQuotation(id, payload)
      : createQuotation(payload as Parameters<typeof createQuotation>[0]);
    req
      .then((q) => {
        this.setData({ saving: false });
        wx.showToast({ title: '已保存', icon: 'success' });
        setTimeout(() => {
          wx.redirectTo({ url: `/packages/admin/quote-detail/index?id=${q.id}` });
        }, 500);
      })
      .catch(() => this.setData({ saving: false }));
  }
});
