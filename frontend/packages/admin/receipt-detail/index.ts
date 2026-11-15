/**
 * 暂收单详情/编辑页（warehouse 域 P2）：
 * - create 模式：选供应商/采购单（可选）+ 行录入——支持「从采购行带入」
 *   （物料/颜色/单位自动带出，数量可手改）或纯手工；单号留空后端自动 ZS；
 * - 草稿编辑：PUT 整表重建（IQC 合格量/拒收量拆量在此落定）；
 * - IQC 拆量零拦截：合格+拒收可超到料量（让步收货），前端仅拦
 *   「合格+拒收=0」与 lot_no/面料缺失；确认按钮仅 draft，确认后只读；
 * - 确认即入库（合格量自动生成采购入库单，P1 价链带价）。
 * 权限：warehouse.manage（列表同点）。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { ApiError } from '../../../utils/request';
import {
  fetchSuppliers, fetchPurchaseOrders, fetchReceipt, createReceipt,
  updateReceipt, deleteReceipt, confirmReceipt,
  Supplier, PurchaseOrder, Receipt, ReceiptItemPayload
} from '../../../services/warehouse';
import { fetchMaterials, Material } from '../../../services/tech';

interface RowVM {
  purchase_item: number | null;
  purchaseLabel: string;   // 带出来源（PO 单号），空=手工行
  material_id: number | null;
  materialIndex: number;
  fabric_name: string;
  color: string;
  lot_no: string;
  unit: string;
  arrived_qty: string;
  qualified_qty: string;
  rejected_qty: string;
  remark: string;
}

function emptyRow(): RowVM {
  return {
    purchase_item: null,
    purchaseLabel: '',
    material_id: null,
    materialIndex: 0,
    fabric_name: '',
    color: '',
    lot_no: '',
    unit: '米',
    arrived_qty: '',
    qualified_qty: '',
    rejected_qty: '',
    remark: ''
  };
}

Page({
  data: {
    denied: false,
    id: 0,
    isCreate: false,
    loading: true,
    error: false,
    gone: false,
    doc: null as Receipt | null,
    // ---- 编辑态（create / draft） ----
    isDraft: false,
    saving: false,
    suppliers: [] as Supplier[],
    supplierNames: ['不选（无单直收）'],
    supplierIndex: 0,
    supplierName: '',
    purchases: [] as PurchaseOrder[],
    purchaseNames: ['不挂采购单'],
    purchaseIndex: 0,
    /** 当前采购单可选行（「从采购行带入」picker 数据源） */
    poItemLabels: [] as string[],
    materials: [] as Material[],
    materialNames: ['不关联物料主档'],
    remark: '',
    items: [] as RowVM[]
  },

  onLoad(query: Record<string, string | undefined>) {
    const isCreate = query.mode === 'create' || !query.id;
    this.setData({
      id: Number(query.id || 0),
      isCreate,
      denied: !hasPerm('warehouse.manage')
    });
  },

  onShow() {
    if (!requireLogin()) return;
    if (this.data.denied) return;
    if (this.data.isCreate) {
      this.initCreate();
    } else {
      this.load();
    }
  },

  onPullDownRefresh() {
    if (this.data.isCreate) this.initCreate();
    else this.load();
    wx.stopPullDownRefresh();
  },

  onRetry() {
    if (this.data.isCreate) this.initCreate();
    else this.load();
  },

  // ============ create 模式初始化 ============

  initCreate() {
    this.setData({ loading: true, error: false });
    Promise.all([
      fetchSuppliers({ page: 1, page_size: 200 }),
      fetchPurchaseOrders({ page: 1, page_size: 200 }),
      fetchMaterials({ page: 1, page_size: 200 })
    ])
      .then(([supRes, poRes, matRes]) => {
        const suppliers = supRes.results || [];
        const purchases = poRes.results || [];
        const materials = matRes.results || [];
        this.setData({
          loading: false,
          suppliers,
          supplierNames: ['不选（无单直收）'].concat(suppliers.map((s) => `${s.code} ${s.name || ''}`)),
          purchases,
          purchaseNames: ['不挂采购单'].concat(purchases.map((p) => `${p.order_no}（${p.supplier_name || '无供应商'}）`)),
          materials,
          materialNames: ['不关联物料主档'].concat(materials.map((m) => `${m.code} ${m.name}`)),
          items: [emptyRow()]
        });
      })
      .catch(() => this.setData({ loading: false, error: true }));
  },

  // ============ 详情加载 ============

  load() {
    this.setData({ loading: true, error: false, gone: false });
    fetchReceipt(this.data.id)
      .then((doc) => {
        this.setData({
          loading: false,
          doc,
          isDraft: doc.status === 'draft',
          remark: doc.remark || ''
        }, () => {
          if (doc.status === 'draft') this.hydrateDraft(doc);
        });
      })
      .catch(() => this.setData({ loading: false, error: true }));
  },

  /** 草稿进编辑态：供应商/采购单选择器 + 行数据回填 */
  hydrateDraft(doc: Receipt) {
    Promise.all([
      fetchSuppliers({ page: 1, page_size: 200 }),
      fetchPurchaseOrders({ page: 1, page_size: 200 }),
      fetchMaterials({ page: 1, page_size: 200 })
    ])
      .then(([supRes, poRes, matRes]) => {
        const suppliers = supRes.results || [];
        const purchases = poRes.results || [];
        const materials = matRes.results || [];
        const supplierIndex = doc.supplier
          ? suppliers.findIndex((s) => s.id === doc.supplier) + 1 : 0;
        const supplierName = doc.supplier_name || '';
        const purchaseIndex = doc.purchase
          ? purchases.findIndex((p) => p.id === doc.purchase) + 1 : 0;
        const items: RowVM[] = (doc.items || []).map((it) => ({
          purchase_item: it.purchase_item,
          purchaseLabel: it.purchase_item_no || '',
          material_id: it.material || null,
          materialIndex: it.material ? materials.findIndex((m) => m.id === it.material) + 1 : 0,
          fabric_name: it.fabric_name,
          color: it.color || '',
          lot_no: it.lot_no || '',
          unit: it.unit || '米',
          arrived_qty: it.arrived_qty || '',
          qualified_qty: it.qualified_qty || '',
          rejected_qty: it.rejected_qty || '',
          remark: it.remark || ''
        }));
        this.setData({
          suppliers,
          supplierNames: ['不选（无单直收）'].concat(suppliers.map((s) => `${s.code} ${s.name || ''}`)),
          purchases,
          purchaseNames: ['不挂采购单'].concat(purchases.map((p) => `${p.order_no}（${p.supplier_name || '无供应商'}）`)),
          materials,
          materialNames: ['不关联物料主档'].concat(materials.map((m) => `${m.code} ${m.name}`)),
          supplierIndex: Math.max(supplierIndex, 0),
          supplierName,
          purchaseIndex: Math.max(purchaseIndex, 0),
          items: items.length ? items : [emptyRow()]
        }, () => this.syncPoItems());
      })
      .catch(() => {
        // 选择器拉取失败不阻塞草稿编辑：行数据已回填，供应商/采购单保持原值
        const items: RowVM[] = (doc.items || []).map((it) => ({
          purchase_item: it.purchase_item,
          purchaseLabel: it.purchase_item_no || '',
          material_id: it.material || null,
          materialIndex: 0,
          fabric_name: it.fabric_name,
          color: it.color || '',
          lot_no: it.lot_no || '',
          unit: it.unit || '米',
          arrived_qty: it.arrived_qty || '',
          qualified_qty: it.qualified_qty || '',
          rejected_qty: it.rejected_qty || '',
          remark: it.remark || ''
        }));
        this.setData({
          supplierNames: ['不选（无单直收）'],
          purchaseNames: ['不挂采购单'],
          items: items.length ? items : [emptyRow()]
        });
      });
  },

  // ============ 表单事件 ============

  onSupplierChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ supplierIndex: Number(e.detail.value) });
  },

  onSupplierNameInput(e: WechatMiniprogram.Input) {
    this.setData({ supplierName: e.detail.value });
  },

  onPurchaseChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ purchaseIndex: Number(e.detail.value) }, () => this.syncPoItems());
  },

  /** 切换采购单后刷新「从采购行带入」可选项 */
  syncPoItems() {
    const idx = this.data.purchaseIndex;
    const po = idx > 0 ? this.data.purchases[idx - 1] : null;
    const labels = po
      ? (po.items || []).map((it, i) =>
        `${i + 1}. ${it.fabric_name}${it.color ? '/' + it.color : ''} `
        + `${it.qty}${it.unit || ''}（已到 ${it.arrived_qty || 0}）`)
      : [];
    this.setData({ poItemLabels: labels });
  },

  onRemark(e: WechatMiniprogram.Input) {
    this.setData({ remark: e.detail.value });
  },

  onRowMaterialChange(e: WechatMiniprogram.PickerChange) {
    const index = Number(e.currentTarget.dataset.index);
    const materialIndex = Number(e.detail.value);
    const items = this.data.items.slice();
    const row = items[index];
    if (!row) return;
    const material = materialIndex > 0 ? this.data.materials[materialIndex - 1] : null;
    row.materialIndex = materialIndex;
    row.material_id = material ? material.id : null;
    if (material) {
      row.fabric_name = material.name;
      row.unit = material.unit || row.unit;
    }
    items[index] = row;
    this.setData({ items });
  },

  onAddFromPo(e: WechatMiniprogram.PickerChange) {
    const idx = Number(e.detail.value);
    const po = this.data.purchaseIndex > 0
      ? this.data.purchases[this.data.purchaseIndex - 1] : null;
    const pi = po && po.items ? po.items[idx] : null;
    if (!pi) return;
    const remaining = Number(pi.qty || 0) - Number(pi.arrived_qty || 0);
    const row = emptyRow();
    row.purchase_item = pi.id;
    row.purchaseLabel = po ? po.order_no : '';
    row.material_id = pi.material || null;
    row.materialIndex = pi.material ? this.data.materials.findIndex((m) => m.id === pi.material) + 1 : 0;
    row.fabric_name = pi.fabric_name;
    row.color = pi.color || '';
    row.unit = pi.unit || '米';
    row.arrived_qty = remaining > 0 ? String(remaining) : '';
    this.setData({ items: this.data.items.concat([row]) });
  },

  onAddManual() {
    this.setData({ items: this.data.items.concat([emptyRow()]) });
  },

  onRemoveRow(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const items = this.data.items.slice();
    items.splice(idx, 1);
    this.setData({ items: items.length ? items : [emptyRow()] });
  },

  onRowField(e: WechatMiniprogram.Input) {
    const idx = Number(e.currentTarget.dataset.index);
    const field = e.currentTarget.dataset.field as keyof RowVM;
    if (typeof field !== 'string') return;
    const items = this.data.items.slice();
    const row = { ...items[idx] } as Record<string, unknown>;
    row[field] = e.detail.value;
    items[idx] = row as unknown as RowVM;
    this.setData({ items });
  },

  noop() { /* 阻止冒泡 */ },

  // ============ 保存（create POST / draft PUT 整表重建） ============

  buildPayload(): { ok: boolean; msg: string; items: ReceiptItemPayload[] } {
    const payload: ReceiptItemPayload[] = [];
    for (let i = 0; i < this.data.items.length; i++) {
      const r = this.data.items[i];
      const fabric = r.fabric_name.trim();
      const lot = r.lot_no.trim();
      if (!fabric) return { ok: false, msg: `第 ${i + 1} 行面料名称不能为空`, items: [] };
      if (!lot) return { ok: false, msg: `第 ${i + 1} 行批次号不能为空`, items: [] };
      const arrived = Number(r.arrived_qty || 0);
      const qualified = Number(r.qualified_qty || 0);
      const rejected = Number(r.rejected_qty || 0);
      if (arrived < 0 || qualified < 0 || rejected < 0) {
        return { ok: false, msg: `第 ${i + 1} 行到料量、合格量和拒收量不能为负数`, items: [] };
      }
      if (qualified + rejected === 0) {
        return { ok: false, msg: `第 ${i + 1} 行合格量+拒收量不能同时为 0`, items: [] };
      }
      payload.push({
        purchase_item: r.purchase_item || null,
        material_id: r.material_id,
        fabric_name: fabric,
        color: r.color.trim(),
        lot_no: lot,
        unit: r.unit.trim() || '米',
        arrived_qty: r.arrived_qty === '' ? 0 : r.arrived_qty,
        qualified_qty: r.qualified_qty === '' ? 0 : r.qualified_qty,
        rejected_qty: r.rejected_qty === '' ? 0 : r.rejected_qty,
        remark: r.remark.trim()
      });
    }
    return { ok: true, msg: '', items: payload };
  },

  onSave() {
    const built = this.buildPayload();
    if (!built.ok) {
      wx.showToast({ title: built.msg, icon: 'none' });
      return;
    }
    const supplier = this.data.supplierIndex > 0
      ? this.data.suppliers[this.data.supplierIndex - 1].id : null;
    const purchase = this.data.purchaseIndex > 0
      ? this.data.purchases[this.data.purchaseIndex - 1].id : null;
    const body = {
      supplier,
      supplier_name: !supplier ? this.data.supplierName.trim() : '',
      purchase,
      remark: this.data.remark.trim(),
      items: built.items
    };
    this.setData({ saving: true });
    const req = this.data.isCreate
      ? createReceipt(body)
      : updateReceipt(this.data.id, body);
    req
      .then((doc) => {
        this.setData({
          saving: false,
          id: doc.id,
          isCreate: false,
          isDraft: doc.status === 'draft',
          doc
        });
        wx.showToast({ title: '已保存', icon: 'success' });
      })
      .catch(() => this.setData({ saving: false }));
  },

  // ============ 确认（仅 draft；确认即入库） ============

  onConfirm() {
    if (!this.data.isDraft) return;
    wx.showModal({
      title: '确认暂收',
      content: '确认后按合格量自动生成采购入库单（价链自动带出），拒收量留痕不进库存；确认后单据只读。确定？',
      success: (m) => {
        if (!m.confirm) return;
        wx.showLoading({ title: '确认中…' });
        confirmReceipt(this.data.id)
          .then((doc) => {
            wx.hideLoading();
            this.setData({ doc, isDraft: false });
            // 缺口 E（INT 2026-10-24）：确认响应回带 in_no/in_id，toast 直显入库单号
            if (doc.in_no) {
              wx.showToast({ title: `已入库 ${doc.in_no}`, icon: 'none' });
            } else {
              wx.showToast({ title: '已确认（无合格量，未生成入库单）', icon: 'none' });
            }
          })
          .catch(() => wx.hideLoading());
      }
    });
  },

  // ============ 删除草稿 ============

  onDelete() {
    if (!this.data.isDraft || this.data.isCreate) return;
    wx.showModal({
      title: '删除暂收单',
      content: `删除草稿 ${this.data.doc ? this.data.doc.receipt_no : ''}？删除后不可恢复。`,
      confirmColor: '#F53F3F',
      success: (m) => {
        if (!m.confirm) return;
        // silent：仅 draft 可删，400 文案弹窗完整直显
        deleteReceipt(this.data.id, true)
          .then(() => {
            wx.showToast({ title: '已删除', icon: 'success' });
            setTimeout(() => wx.navigateBack(), 500);
          })
          .catch((err: unknown) => {
            const msg = err instanceof ApiError && err.message ? err.message : '删除失败';
            wx.showModal({ title: '无法删除', content: msg, showCancel: false });
          });
      }
    });
  }
});
