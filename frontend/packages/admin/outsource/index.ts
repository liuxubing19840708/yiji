/**
 * 外发管理页（竞品对齐 GAP-5，2026-11-04）。
 *
 * - 列表按状态 tab（全部/已发出/部分收回/已收回/已结算）；
 * - 「新建外发单」：外协厂（主档或手填）+ 数量/单价/交回日期；
 * - 行内动作：收回（partial 自动派生）/ 结算（returned 后）/ 删除（无收回时）；
 * - 导出 xlsx（outsource.view 点即可），动作需 outsource.manage 点。
 * 入口：首页宫格「外发管理」。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import {
  OutsourceOrderRow, OutsourceStatus,
  fetchOutsourceOrders, createOutsourceOrder, outsourceAction,
  deleteOutsourceOrder, downloadOutsourceExport, outsourceExportUrl
} from '../../../services/outsource';
import { fetchSuppliers, Supplier } from '../../../services/warehouse';
import { fetchStyles } from '../../../services/catalog';

const STATUS_TABS: Array<{ key: string; label: string }> = [
  { key: '', label: '全部' },
  { key: 'sent', label: '已发出' },
  { key: 'partial', label: '部分收回' },
  { key: 'returned', label: '已收回' },
  { key: 'settled', label: '已结算' }
];

Page({
  data: {
    tabs: STATUS_TABS,
    statusIndex: 0,
    canManage: false,
    canExport: false,
    exporting: false,
    loading: false,
    list: [] as OutsourceOrderRow[],
    // ---- 新建外发单表单 ----
    formOpen: false,
    formSaving: false,
    factories: ['手填厂名'],
    factoryNames: ['手填厂名'],
    factoryIndex: 0,
    suppliers: [] as Supplier[],
    styles: [] as Array<{ id: number; label: string }>,
    styleNames: ['款式（可选）'],
    styleIndex: 0,
    sendQty: '',
    unitPrice: '',
    processDesc: '',
    dueDate: ''
  },

  onShow() {
    requireLogin();
    const app = getApp<IAppOption>();
    this.setData({
      canManage: !!app.globalData.isAdmin || hasPerm('outsource.manage'),
      canExport: !!app.globalData.isAdmin || hasPerm('outsource.view')
    });
    this.reload();
  },

  onStatusTap(e: WechatMiniprogram.TouchEvent) {
    this.setData({ statusIndex: Number(e.currentTarget.dataset.index) });
    this.reload();
  },

  reload() {
    this.setData({ loading: true });
    const status = (STATUS_TABS[this.data.statusIndex] || {}).key || undefined;
    fetchOutsourceOrders({ status: status as OutsourceStatus | undefined, page_size: 100 })
      .then((res) => this.setData({ list: res.results || [], loading: false }))
      .catch(() => this.setData({ loading: false }));
  },

  // ================= 新建外发单 =================
  onOpenForm() {
    if (!this.data.factoryNames.length || this.data.factoryNames.length === 1) {
      fetchSuppliers({ page_size: 200 })
        .then((res) => {
          const suppliers = res.results || [];
          this.setData({
            suppliers,
            factoryNames: ['手填厂名'].concat(suppliers.map((s) => s.name))
          });
        })
        .catch(() => undefined);
      fetchStyles({ page_size: 200 })
        .then((res) => {
          const styles = (res.results || []).map((s) => ({
            id: s.id, label: `${s.style_no} ${s.name}`
          }));
          this.setData({ styles, styleNames: ['款式（可选）'].concat(styles.map((x) => x.label)) });
        })
        .catch(() => undefined);
    }
    this.setData({ formOpen: true });
  },

  onCloseForm() {
    this.setData({
      formOpen: false, factoryIndex: 0, styleIndex: 0,
      sendQty: '', unitPrice: '', processDesc: '', dueDate: ''
    });
  },

  onFactoryChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ factoryIndex: Number(e.detail.value) });
  },
  onStyleChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ styleIndex: Number(e.detail.value) });
  },
  onQtyInput(e: WechatMiniprogram.Input) { this.setData({ sendQty: e.detail.value }); },
  onPriceInput(e: WechatMiniprogram.Input) { this.setData({ unitPrice: e.detail.value }); },
  onDescInput(e: WechatMiniprogram.Input) { this.setData({ processDesc: e.detail.value }); },
  onDueChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ dueDate: String(e.detail.value) });
  },

  async onSubmitForm() {
    if (this.data.formSaving) return;
    const { suppliers, factoryIndex, styles, styleIndex, sendQty, unitPrice, processDesc, dueDate } = this.data;
    const qty = Number(sendQty);
    if (!Number.isInteger(qty) || qty <= 0) {
      wx.showToast({ title: '外发数量须为正整数', icon: 'none' }); return;
    }
    const price = unitPrice.trim() === '' ? '0' : unitPrice.trim();
    if (Number(price) < 0 || Number.isNaN(Number(price))) {
      wx.showToast({ title: '加工单价不能为负', icon: 'none' }); return;
    }
    const payload: Parameters<typeof createOutsourceOrder>[0] = {
      send_qty: qty, unit_price: price
    };
    if (factoryIndex > 0) payload.supplier = suppliers[factoryIndex - 1].id;
    else { wx.showToast({ title: '请选外协厂或先建主档', icon: 'none' }); return; }
    if (styleIndex > 0) payload.style = styles[styleIndex - 1].id;
    if (processDesc.trim()) payload.process_desc = processDesc.trim();
    if (dueDate) payload.due_date = dueDate;

    this.setData({ formSaving: true });
    try {
      await createOutsourceOrder(payload);
      wx.showToast({ title: '外发单已建', icon: 'success' });
      this.onCloseForm();
      this.reload();
    } catch (e) {
      // 错误由请求层 toast
    } finally {
      this.setData({ formSaving: false });
    }
  },

  // ================= 行内动作 =================
  async onReceive(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const row = this.data.list.find((r) => r.id === id);
    if (!row) return;
    const remaining = row.send_qty - row.received_qty;
    const res = await wx.showModal({
      title: '外发收回',
      editable: true,
      placeholderText: `收回数量（≤${remaining}）`,
      content: String(remaining)
    });
    if (!res.confirm) return;
    const qty = Number(res.content);
    if (!Number.isInteger(qty) || qty <= 0 || qty > remaining) {
      wx.showToast({ title: `收回数量须为 1~${remaining}`, icon: 'none' }); return;
    }
    try {
      await outsourceAction(id, { action: 'receive', qty });
      wx.showToast({ title: '已收回', icon: 'success' });
      this.reload();
    } catch (err) { /* 请求层已提示 */ }
  },

  async onSettle(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const row = this.data.list.find((r) => r.id === id);
    const res = await wx.showModal({
      title: '外发结算',
      content: `确认结算该外发单？加工费 ${row ? row.amount : ''} 元`
    });
    if (!res.confirm) return;
    try {
      await outsourceAction(id, { action: 'settle' });
      wx.showToast({ title: '已结算', icon: 'success' });
      this.reload();
    } catch (err) { /* 请求层已提示 */ }
  },

  async onDelete(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const res = await wx.showModal({ title: '删除外发单', content: '删除后不可恢复，确认？' });
    if (!res.confirm) return;
    try {
      await deleteOutsourceOrder(id);
      wx.showToast({ title: '已删除', icon: 'success' });
      this.reload();
    } catch (err) { /* 请求层已提示 */ }
  },

  // ================= 导出 =================
  async onExport() {
    if (this.data.exporting) return;
    this.setData({ exporting: true });
    try {
      const status = (STATUS_TABS[this.data.statusIndex] || {}).key || undefined;
      await downloadOutsourceExport(outsourceExportUrl({ status: status as OutsourceStatus | undefined }));
    } catch (e) {
      wx.showToast({ title: '导出失败', icon: 'none' });
    } finally {
      this.setData({ exporting: false });
    }
  }
});
