/**
 * 轻量仓库（P1 最小可用版，2026-10-18）：
 * 三个单据 Tab（采购/入库/领料）× 列表 + 新建 + 确认，样式从简，先跑通数据链：
 * - 入库行批次号（供应商原始编号）自动 get_or_create 批次（Lot 在入库诞生）；
 * - 领料确认钩子自动写「床次×批次×用量」血缘（追溯只读血缘表）。
 * 只记流水不记结存；单人确认即生效。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { fetchProductions } from '../../../services/production';
import {
  fetchLots, fetchPurchaseOrders, createPurchaseOrder, confirmPurchaseOrder,
  fetchStockIns, createStockIn, confirmStockIn,
  fetchMaterialIssues, createMaterialIssue, confirmMaterialIssue,
  Lot, PurchaseOrder, StockIn, MaterialIssue
} from '../../../services/warehouse';

type Tab = 'purchase' | 'stockin' | 'issue';

function unwrap<T>(res: T[] | { results?: T[] }): T[] {
  return Array.isArray(res) ? res : (res.results || []);
}

Page({
  data: {
    tab: 'purchase' as Tab,
    /** 权限点体系二期：新建/确认按钮按 warehouse.manage 点显隐（入口已过门，此处兜底） */
    canManage: false,
    loading: false,
    purchaseOrders: [] as PurchaseOrder[],
    stockIns: [] as StockIn[],
    issues: [] as MaterialIssue[],
    // ---- 新建表单（单据通用：单行明细，够跑通数据链） ----
    showForm: false,
    saving: false,
    // 采购
    supplier: '',
    orderedDate: '',
    // 入库 / 采购共用
    fabric: '',
    color: '',
    // 入库
    lotNo: '',
    // 领料
    productions: [] as Array<{ id: number; label: string }>,
    productionIndex: -1,
    lots: [] as Array<{ id: number; label: string }>,
    lotIndex: -1,
    qty: ''
  },

  onShow() {
    if (!requireLogin()) return;
    this.setData({ canManage: hasPerm('warehouse.manage') });
    this.reload();
  },

  reload() {
    const { tab } = this.data;
    this.setData({ loading: true });
    const done = () => this.setData({ loading: false });
    if (tab === 'purchase') {
      fetchPurchaseOrders().then((res) => {
        this.setData({ purchaseOrders: unwrap(res) }); done();
      }).catch(done);
    } else if (tab === 'stockin') {
      fetchStockIns().then((res) => {
        this.setData({ stockIns: unwrap(res) }); done();
      }).catch(done);
    } else {
      fetchMaterialIssues().then((res) => {
        this.setData({ issues: unwrap(res) }); done();
      }).catch(done);
    }
  },

  onTab(e: WechatMiniprogram.TouchEvent) {
    const tab = String(e.currentTarget.dataset.tab) as Tab;
    if (tab === this.data.tab) return;
    this.setData({ tab, showForm: false });
    this.reload();
  },

  // ============ 新建表单 ============

  onToggleForm() {
    const opening = !this.data.showForm;
    this.setData({ showForm: opening });
    if (opening && this.data.tab === 'issue') {
      // 领料表单需要床次与批次选择器
      if (!this.data.productions.length) {
        fetchProductions({ page_size: 50 }).then((res) => {
          this.setData({
            productions: (res.results || []).map((p) => ({
              id: p.id, label: `${p.bed_no}（${p.style_no || ''}）`
            }))
          });
        }).catch(() => { /* toast 已由请求层弹出 */ });
      }
      if (!this.data.lots.length) {
        fetchLots({ page_size: 100 }).then((res) => {
          this.setData({
            lots: unwrap(res).map((l: Lot) => ({ id: l.id, label: l.lot_no }))
          });
        }).catch(() => { /* toast 已由请求层弹出 */ });
      }
    }
  },

  onSupplier(e: WechatMiniprogram.Input) { this.setData({ supplier: e.detail.value }); },
  onFabric(e: WechatMiniprogram.Input) { this.setData({ fabric: e.detail.value }); },
  onColor(e: WechatMiniprogram.Input) { this.setData({ color: e.detail.value }); },
  onLotNo(e: WechatMiniprogram.Input) { this.setData({ lotNo: e.detail.value }); },
  onQty(e: WechatMiniprogram.Input) { this.setData({ qty: e.detail.value }); },

  onOrderedDate(e: WechatMiniprogram.PickerChange) {
    this.setData({ orderedDate: String(e.detail.value) });
  },

  onPickProduction(e: WechatMiniprogram.PickerChange) {
    this.setData({ productionIndex: Number(e.detail.value) });
  },

  onPickLot(e: WechatMiniprogram.PickerChange) {
    this.setData({ lotIndex: Number(e.detail.value) });
  },

  // ============ 提交 ============

  onSubmit() {
    if (this.data.saving) return;
    const { tab } = this.data;
    const fabric = this.data.fabric.trim();
    if (!fabric) {
      wx.showToast({ title: '请填面料名称', icon: 'none' });
      return;
    }
    const qty = this.data.qty.trim();
    this.setData({ saving: true });
    const finish = (msg: string) => {
      wx.showToast({ title: msg, icon: 'success' });
      this.setData({
        saving: false, showForm: false, fabric: '', color: '', lotNo: '', qty: '',
        supplier: '', productionIndex: -1, lotIndex: -1
      });
      this.reload();
    };
    const fail = () => this.setData({ saving: false });

    if (tab === 'purchase') {
      const orderedDate = this.data.orderedDate
        || new Date().toISOString().slice(0, 10);
      createPurchaseOrder({
        supplier_name: this.data.supplier.trim(),
        ordered_at: orderedDate,
        items: [{ fabric_name: fabric, color: this.data.color.trim(), qty: qty || '0' }]
      }).then(() => finish('采购单已保存')).catch(fail);
    } else if (tab === 'stockin') {
      const lotNo = this.data.lotNo.trim();
      if (!lotNo) {
        wx.showToast({ title: '请填批次号', icon: 'none' });
        this.setData({ saving: false });
        return;
      }
      createStockIn({
        source_type: 'purchase',
        items: [{ fabric_name: fabric, color: this.data.color.trim(), lot_no: lotNo, qty: qty || '0' }]
      }).then(() => finish('入库成功，批次已生成')).catch(fail);
    } else {
      const p = this.data.productions[this.data.productionIndex];
      const l = this.data.lots[this.data.lotIndex];
      if (!p || !l) {
        wx.showToast({ title: '请选床次与批次', icon: 'none' });
        this.setData({ saving: false });
        return;
      }
      createMaterialIssue({
        items: [{ production_id: p.id, lot_id: l.id, qty: qty || '0' }]
      }).then(() => finish('领料单已保存')).catch(fail);
    }
  },

  // ============ 确认（草稿 → 生效；领料确认即写血缘） ============

  onConfirm(e: WechatMiniprogram.TouchEvent) {
    const { tab, index } = e.currentTarget.dataset;
    const idx = Number(index);
    const doConfirm = (p: Promise<unknown>, msg: string) => {
      p.then(() => {
        wx.showToast({ title: msg, icon: 'success' });
        this.reload();
      }).catch(() => { /* toast 已由请求层弹出 */ });
    };
    if (tab === 'purchase') {
      const doc = this.data.purchaseOrders[idx];
      if (doc) doConfirm(confirmPurchaseOrder(doc.id), '采购单已确认');
    } else if (tab === 'stockin') {
      const doc = this.data.stockIns[idx];
      if (doc) doConfirm(confirmStockIn(doc.id), '入库单已确认');
    } else {
      const doc = this.data.issues[idx];
      if (doc) doConfirm(confirmMaterialIssue(doc.id), '领料已确认，血缘已写入');
    }
  },

  statusText(s: string): string {
    return s === 'done' || s === 'confirmed' ? '已生效' : '草稿';
  }
});
