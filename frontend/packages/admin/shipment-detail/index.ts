/**
 * 出货单详情/编辑页（shipment 域 P2）：
 * - create 模式：选订单（明细行从订单色码行带出）+ 客户（必传）+ 明细行编辑；
 * - 草稿编辑：全量改（order/customer/items 全量替换 PUT）；未交矩阵参考
 *   （草稿量不计入已出，审核前不预占未交量）；
 * - 已审核：仅物流单号/发货日期/备注可改（PUT 传其他字段后端 400）；
 * - 审核：成功响应 {shipment, receivable} 双块 → toast「应收 xx 已生成」
 *   可跳应收详情；400 三类文案直显（超未交量/成品库存不足/未定价→跳订单补价）；
 * - 作废：必弹 warning（应收号/已收金额文案后端拼好直显）+ 挂应收清单；
 * - 装箱行：毛净重/箱规卡片，boxes/total_qty 后端自动汇总；
 * - 打印：出货单/装箱单两模板，print_key=`shipment:{id}:{tpl}` 幂等
 *   （duplicated → 已打过），is_reprint → 补打徽标 + 打印历史。
 * 权限：页面挂 shipment.view；动作按 shipment.manage 显隐（打印上报
 * shipment.manage 或 print.manage）。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { ApiError } from '../../../utils/request';
import {
  fetchShipment, createShipment, updateShipment, deleteShipment,
  auditShipment, voidShipment, fetchUndelivered, fetchBoxes, addBox, deleteBox,
  fetchPrintLogs, reportShipmentPrint, createShipmentPrintKey, openShipmentPrintFile,
  Shipment, PackingBox, ShipmentPrintLog, ShipmentTpl, UndeliveredMatrix
} from '../../../services/shipment';
import { fetchOrders, fetchOrder } from '../../../services/orders';
import { fetchCustomers } from '../../../services/catalog';
import type { Customer } from '../../../services/types';

interface OrderOption {
  id: number;
  label: string;
  customer: number | null;
}

interface ItemRowVM {
  color: string;
  size: string;
  qty: string;
  /** 订单量参考（带出行显示） */
  refQty: number;
  /** 未交量参考（草稿编辑态由未交矩阵带出；create 态=订单量） */
  refUndelivered: number | null;
}

interface UndeliveredVM {
  color: string;
  size: string;
  qty: number;
  shippedQty: number;
  undelivered: number;
}

function emptyRow(): ItemRowVM {
  return { color: '', size: '', qty: '', refQty: 0, refUndelivered: null };
}

Page({
  data: {
    denied: false,
    canManage: false,
    canPrint: false,
    id: 0,
    isCreate: false,
    mode: 'view' as 'view' | 'create' | 'edit',
    loading: true,
    error: false,
    gone: false,
    shp: null as Shipment | null,
    isDraft: false,
    saving: false,
    // ---- 明细行（view 展示 / create+edit 编辑同源） ----
    rows: [] as ItemRowVM[],
    undelivered: null as UndeliveredMatrix | null,
    undeliveredRows: [] as UndeliveredVM[],
    showMatrix: false,
    // ---- 表单（create / draft edit） ----
    orders: [] as OrderOption[],
    orderLabels: ['请选择订单'],
    orderIndex: 0,
    customers: [] as Customer[],
    customerNames: ['请选择客户（必填）'],
    customerIndex: 0,
    deliveryDate: '',
    shipDate: '',
    logisticsNo: '',
    remark: '',
    // ---- 装箱行 ----
    boxes: [] as PackingBox[],
    boxVisible: false,
    boxSaving: false,
    boxNo: '',
    boxColor: '',
    boxSize: '',
    boxQty: '',
    boxGross: '',
    boxNet: '',
    boxSpec: '',
    // ---- 打印 ----
    printLogs: [] as ShipmentPrintLog[],
    printing: false
  },

  onLoad(query: Record<string, string | undefined>) {
    const isCreate = query.mode === 'create';
    this.setData({
      id: Number(query.id || 0),
      isCreate,
      mode: isCreate ? 'create' : 'view',
      denied: !hasPerm('shipment.view'),
      canManage: hasPerm('shipment.manage'),
      canPrint: hasPerm('shipment.manage') || hasPerm('print.manage')
    });
  },

  onShow() {
    if (!requireLogin()) return;
    if (this.data.denied) return;
    if (this.data.isCreate) this.initCreate();
    else this.load();
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
      fetchOrders({ page: 1, page_size: 200 }),
      fetchCustomers({ page: 1, page_size: 200 })
    ])
      .then(([orderRes, custRes]) => {
        const orders: OrderOption[] = (orderRes.results || []).map((o) => ({
          id: o.id,
          label: `${o.order_no}${o.customer_name ? ' · ' + o.customer_name : ''}`
            + `${o.style_no ? ' · ' + o.style_no : ''}`,
          customer: o.customer
        }));
        const customers = custRes.results || [];
        this.setData({
          loading: false,
          orders,
          orderLabels: ['请选择订单'].concat(orders.map((o) => o.label)),
          customers,
          customerNames: ['请选择客户（必填）'].concat(customers.map((c) => c.name)),
          rows: [emptyRow()]
        });
      })
      .catch(() => this.setData({ loading: false, error: true }));
  },

  onOrderChange(e: WechatMiniprogram.PickerChange) {
    const idx = Number(e.detail.value);
    if (idx === this.data.orderIndex) return;
    this.setData({ orderIndex: idx });
    const opt = this.data.orders[idx - 1];
    if (!opt) return;
    fetchOrder(opt.id)
      .then((order) => {
        const rows: ItemRowVM[] = (order.items || []).map((it) => ({
          color: it.color,
          size: it.size,
          qty: String(it.qty),
          refQty: it.qty,
          refUndelivered: it.qty
        }));
        const custIdx = order.customer
          ? this.data.customers.findIndex((c) => c.id === order.customer) + 1 : 0;
        this.setData({
          rows: rows.length ? rows : [emptyRow()],
          customerIndex: custIdx > 0 ? custIdx : this.data.customerIndex
        });
      })
      .catch(() => undefined); // 带出失败不阻塞：行可手填
  },

  onCustomerChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ customerIndex: Number(e.detail.value) });
  },

  onDeliveryDate(e: WechatMiniprogram.PickerChange) {
    this.setData({ deliveryDate: String(e.detail.value) });
  },

  onShipDate(e: WechatMiniprogram.PickerChange) {
    this.setData({ shipDate: String(e.detail.value) });
  },

  onLogisticsNo(e: WechatMiniprogram.Input) {
    this.setData({ logisticsNo: e.detail.value });
  },

  onFormRemark(e: WechatMiniprogram.Input) {
    this.setData({ remark: e.detail.value });
  },

  onAddRow() {
    this.setData({ rows: this.data.rows.concat([emptyRow()]) });
  },

  onRemoveRow(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const rows = this.data.rows.slice();
    rows.splice(idx, 1);
    this.setData({ rows: rows.length ? rows : [emptyRow()] });
  },

  onRowField(e: WechatMiniprogram.Input) {
    const idx = Number(e.currentTarget.dataset.index);
    const field = e.currentTarget.dataset.field as 'color' | 'size' | 'qty';
    const rows = this.data.rows.slice();
    const row = { ...rows[idx] };
    row[field] = e.detail.value;
    rows[idx] = row;
    this.setData({ rows });
  },

  noop() { /* 阻止冒泡 */ },

  // ============ 详情加载（view） ============

  load() {
    this.setData({ loading: true, error: false, gone: false });
    if (!this.data.id) {
      this.setData({ loading: false, error: true });
      return;
    }
    fetchShipment(this.data.id)
      .then((shp) => {
        this.setData({
          loading: false,
          shp,
          isDraft: shp.status === 'draft',
          logisticsNo: shp.logistics_no || '',
          shipDate: shp.ship_date || '',
          remark: shp.remark || '',
          rows: (shp.items || []).map((it) => ({
            color: it.color, size: it.size, qty: String(it.qty),
            refQty: 0, refUndelivered: null
          }))
        }, () => {
          this.loadExtras();
        });
      })
      .catch(() => this.setData({ loading: false, error: true }));
  },

  /** 详情附属：未交矩阵 + 装箱行 + 打印历史（任一失败静默不阻塞主数据） */
  loadExtras() {
    fetchUndelivered(this.data.id)
      .then((m) => {
        this.setData({
          undelivered: m,
          undeliveredRows: (m.items || []).map((it) => ({
            color: it.color, size: it.size, qty: it.qty,
            shippedQty: it.shipped_qty, undelivered: it.undelivered_qty
          }))
        });
      })
      .catch(() => undefined);
    fetchBoxes(this.data.id)
      .then((boxes) => this.setData({ boxes: boxes || [] }))
      .catch(() => undefined);
    fetchPrintLogs(this.data.id)
      .then((logs) => this.setData({ printLogs: logs || [] }))
      .catch(() => undefined);
  },

  onToggleMatrix() {
    this.setData({ showMatrix: !this.data.showMatrix });
  },

  // ============ 保存（create POST / draft 全量 PUT） ============

  buildRows(): { ok: boolean; msg: string; rows: Array<{ color: string; size: string; qty: number }> } {
    const out: Array<{ color: string; size: string; qty: number }> = [];
    const seen = new Set<string>();
    for (let i = 0; i < this.data.rows.length; i++) {
      const r = this.data.rows[i];
      const color = r.color.trim();
      const size = r.size.trim();
      const qty = Number(r.qty);
      if (!color) return { ok: false, msg: `第 ${i + 1} 行缺少颜色`, rows: [] };
      if (!size) return { ok: false, msg: `第 ${i + 1} 行缺少尺码`, rows: [] };
      if (!Number.isInteger(qty) || qty <= 0) {
        return { ok: false, msg: `第 ${i + 1} 行数量必须大于 0（整数）`, rows: [] };
      }
      const key = `${color}/${size}`;
      if (seen.has(key)) return { ok: false, msg: `同单同色码重复：${key}`, rows: [] };
      seen.add(key);
      out.push({ color, size, qty });
    }
    if (!out.length) return { ok: false, msg: '出货明细不能为空', rows: [] };
    return { ok: true, msg: '', rows: out };
  },

  onSave() {
    const built = this.buildRows();
    if (!built.ok) {
      wx.showToast({ title: built.msg, icon: 'none' });
      return;
    }
    const cust = this.data.customerIndex > 0
      ? this.data.customers[this.data.customerIndex - 1].id : null;
    if (!cust) {
      wx.showToast({ title: '请选择客户（必传）', icon: 'none' });
      return;
    }
    const body = {
      customer: cust,
      delivery_date: this.data.deliveryDate || null,
      ship_date: this.data.shipDate || null,
      logistics_no: this.data.logisticsNo.trim(),
      remark: this.data.remark.trim(),
      items: built.rows
    };
    this.setData({ saving: true });
    if (this.data.isCreate) {
      const opt = this.data.orderIndex > 0 ? this.data.orders[this.data.orderIndex - 1] : null;
      if (!opt) {
        this.setData({ saving: false });
        wx.showToast({ title: '请选择订单', icon: 'none' });
        return;
      }
      createShipment({ ...body, order: opt.id })
        .then((shp) => {
          this.setData({
            saving: false, isCreate: false, mode: 'view',
            id: shp.id, shp, isDraft: shp.status === 'draft'
          });
          wx.showToast({ title: '出货单已建立', icon: 'success' });
          this.load();
        })
        .catch(() => this.setData({ saving: false }));
    } else {
      // 草稿全量改（order/customer/items 全量替换）；已审核单走 onSaveAudited
      const opt = this.data.orderIndex > 0 ? this.data.orders[this.data.orderIndex - 1] : null;
      if (!opt) {
        this.setData({ saving: false });
        wx.showToast({ title: '请选择订单', icon: 'none' });
        return;
      }
      updateShipment(this.data.id, { ...body, order: opt.id })
        .then(() => {
          this.setData({ saving: false, mode: 'view' });
          wx.showToast({ title: '已保存', icon: 'success' });
          this.load();
        })
        .catch(() => this.setData({ saving: false }));
    }
  },

  /** 已审核单：仅物流单号/发货日期/备注（后端其余字段 400） */
  onSaveAudited() {
    this.setData({ saving: true });
    updateShipment(this.data.id, {
      logistics_no: this.data.logisticsNo.trim(),
      ship_date: this.data.shipDate || null,
      remark: this.data.remark.trim()
    })
      .then(() => {
        this.setData({ saving: false, mode: 'view' });
        wx.showToast({ title: '已保存', icon: 'success' });
        this.load();
      })
      .catch(() => this.setData({ saving: false }));
  },

  onEnterEdit() {
    const shp = this.data.shp;
    if (!shp) return;
    if (shp.status === 'draft') {
      Promise.all([
        fetchOrders({ page: 1, page_size: 200 }),
        fetchCustomers({ page: 1, page_size: 200 })
      ])
        .then(([orderRes, custRes]) => {
          const orders: OrderOption[] = (orderRes.results || []).map((o) => ({
            id: o.id,
            label: `${o.order_no}${o.customer_name ? ' · ' + o.customer_name : ''}`
              + `${o.style_no ? ' · ' + o.style_no : ''}`,
            customer: o.customer
          }));
          const customers = custRes.results || [];
          const orderIdx = shp.order
            ? orders.findIndex((o) => o.id === shp.order) + 1 : 0;
          const custIdx = shp.customer
            ? customers.findIndex((c) => c.id === shp.customer) + 1 : 0;
          this.setData({
            mode: 'edit',
            orders,
            orderLabels: ['请选择订单'].concat(orders.map((o) => o.label)),
            customers,
            customerNames: ['请选择客户（必填）'].concat(customers.map((c) => c.name)),
            orderIndex: Math.max(orderIdx, 0),
            customerIndex: Math.max(custIdx, 0),
            deliveryDate: shp.delivery_date || '',
            shipDate: shp.ship_date || '',
            logisticsNo: shp.logistics_no || '',
            remark: shp.remark || ''
          }, () => {
            // 未交量参考（草稿量不计入已出）：有矩阵则带出 refUndelivered
            this.hydrateRowRefs();
          });
        })
        .catch(() => {
          // 选择器拉取失败不阻塞：保持原值直接编辑行
          this.setData({ mode: 'edit' });
        });
    } else {
      this.setData({ mode: 'edit' });
    }
  },

  /** 编辑态行参考量：优先未交矩阵（真口径），无矩阵回落订单量占位 */
  hydrateRowRefs() {
    const rows = this.data.rows.map((r) => ({ ...r, refQty: 0, refUndelivered: null }));
    this.setData({ rows });
    fetchUndelivered(this.data.id)
      .then((m) => {
        const map = new Map<string, number>();
        (m.items || []).forEach((it) => map.set(`${it.color}/${it.size}`, it.undelivered_qty));
        this.setData({
          rows: this.data.rows.map((r) => ({
            ...r,
            refUndelivered: map.has(`${r.color}/${r.size}`)
              ? map.get(`${r.color}/${r.size}`) as number : null
          })),
          undelivered: m,
          undeliveredRows: (m.items || []).map((it) => ({
            color: it.color, size: it.size, qty: it.qty,
            shippedQty: it.shipped_qty, undelivered: it.undelivered_qty
          }))
        });
      })
      .catch(() => undefined);
  },

  onCancelEdit() {
    this.setData({ mode: 'view' }, () => this.load());
  },

  // ============ 删除草稿 / 审核 / 作废 ============

  onDelete() {
    const shp = this.data.shp;
    if (!shp || !this.data.canManage) return;
    wx.showModal({
      title: '删除出货单',
      content: `删除草稿 ${shp.shipment_no}？已审核单不可删只可作废。`,
      confirmColor: '#F53F3F',
      success: (m) => {
        if (!m.confirm) return;
        deleteShipment(this.data.id)
          .then(() => {
            wx.showToast({ title: '已删除', icon: 'success' });
            setTimeout(() => wx.navigateBack(), 500);
          })
          .catch(() => undefined); // 400 文案自动 toast（仅草稿可删）
      }
    });
  },

  onAudit() {
    if (!this.data.canManage) return;
    wx.showModal({
      title: '审核出货单',
      content: '审核校验：未交量 / 成品库存 / 色码定价；通过后自动生成应收。确定审核？',
      success: (m) => {
        if (!m.confirm) return;
        wx.showLoading({ title: '审核中…' });
        auditShipment(this.data.id)
          .then((res) => {
            wx.hideLoading();
            const rec = res.receivable;
            wx.showModal({
              title: '审核通过',
              content: `应收 ${rec.receivable_no} 已生成（${rec.amount} 元）。`,
              confirmText: '查看应收',
              cancelText: '留在本页',
              success: (mm) => {
                if (mm.confirm) {
                  wx.navigateTo({
                    url: `/packages/admin/receivable-detail/index?id=${rec.id}&no=${rec.receivable_no}`
                  });
                } else {
                  this.load();
                }
              }
            });
          })
          .catch((err: unknown) => {
            wx.hideLoading();
            // 400 三类文案直显：超未交量 / 成品库存不足 / 未定价（跳订单补价）
            const msg = err instanceof ApiError && err.message ? err.message : '审核失败';
            const needPrice = msg.indexOf('补价') >= 0;
            wx.showModal({
              title: '审核未通过',
              content: msg,
              confirmText: needPrice ? '去补价' : '知道了',
              showCancel: needPrice,
              success: (mm) => {
                if (needPrice && mm.confirm) {
                  const orderId = this.data.shp ? this.data.shp.order : null;
                  if (orderId) {
                    wx.navigateTo({ url: `/packages/admin/order-detail/index?id=${orderId}` });
                  }
                } else {
                  this.load();
                }
              }
            });
          });
      }
    });
  },

  onVoid() {
    const shp = this.data.shp;
    if (!shp || !this.data.canManage) return;
    wx.showModal({
      title: '作废出货单',
      content: '作废后单据下架（软删留痕），已出量回到未交池；撤单不自动撤应收。确定作废？',
      confirmColor: '#F53F3F',
      success: (m) => {
        if (!m.confirm) return;
        wx.showLoading({ title: '作废中…' });
        voidShipment(this.data.id)
          .then((res) => {
            wx.hideLoading();
            // warning 后端拼好（含应收号/已收金额），直接展示
            const lines = (res.receivables || [])
              .map((r) => `${r.receivable_no}（${r.amount} 元，已收 ${r.received_amount}）`);
            const content = res.warning
              + (lines.length ? `\n挂应收：\n${lines.join('\n')}` : '');
            wx.showModal({
              title: '已作废',
              content,
              confirmText: '去应收',
              cancelText: '返回',
              success: (mm) => {
                if (mm.confirm) {
                  wx.redirectTo({ url: '/packages/admin/receivables/index' });
                } else {
                  wx.navigateBack();
                }
              }
            });
          })
          .catch(() => wx.hideLoading());
      }
    });
  },

  // ============ 装箱行 ============

  onOpenBox() {
    this.setData({
      boxVisible: true,
      boxNo: '',
      boxColor: '',
      boxSize: '',
      boxQty: '',
      boxGross: '',
      boxNet: '',
      boxSpec: ''
    });
  },

  onCloseBox() { this.setData({ boxVisible: false }); },

  onBoxNo(e: WechatMiniprogram.Input) { this.setData({ boxNo: e.detail.value }); },
  onBoxColor(e: WechatMiniprogram.Input) { this.setData({ boxColor: e.detail.value }); },
  onBoxSize(e: WechatMiniprogram.Input) { this.setData({ boxSize: e.detail.value }); },
  onBoxQty(e: WechatMiniprogram.Input) { this.setData({ boxQty: e.detail.value }); },
  onBoxGross(e: WechatMiniprogram.Input) { this.setData({ boxGross: e.detail.value }); },
  onBoxNet(e: WechatMiniprogram.Input) { this.setData({ boxNet: e.detail.value }); },
  onBoxSpec(e: WechatMiniprogram.Input) { this.setData({ boxSpec: e.detail.value }); },

  onSaveBox() {
    const boxNo = this.data.boxNo.trim();
    if (!boxNo) {
      wx.showToast({ title: '请填写箱号', icon: 'none' });
      return;
    }
    this.setData({ boxSaving: true });
    addBox(this.data.id, {
      box_no: boxNo,
      color: this.data.boxColor.trim(),
      size: this.data.boxSize.trim(),
      qty: this.data.boxQty === '' ? 0 : this.data.boxQty,
      gross_wt: this.data.boxGross === '' ? undefined : this.data.boxGross,
      net_wt: this.data.boxNet === '' ? undefined : this.data.boxNet,
      box_spec: this.data.boxSpec.trim()
    })
      .then(() => {
        this.setData({ boxSaving: false, boxVisible: false });
        wx.showToast({ title: '已加装箱行', icon: 'success' });
        fetchBoxes(this.data.id)
          .then((boxes) => this.setData({ boxes: boxes || [] }))
          .catch(() => undefined);
        fetchShipment(this.data.id)
          .then((shp) => this.setData({ shp }))
          .catch(() => undefined);
      })
      .catch(() => this.setData({ boxSaving: false }));
  },

  onDeleteBox(e: WechatMiniprogram.TouchEvent) {
    const boxId = Number(e.currentTarget.dataset.id);
    const box = this.data.boxes.find((b) => b.id === boxId);
    if (!box) return;
    wx.showModal({
      title: '删除装箱行',
      content: `删除箱 ${box.box_no}？汇总箱数/件数自动重算。`,
      confirmColor: '#F53F3F',
      success: (m) => {
        if (!m.confirm) return;
        deleteBox(this.data.id, boxId)
          .then(() => {
            wx.showToast({ title: '已删除', icon: 'success' });
            fetchBoxes(this.data.id)
              .then((boxes) => this.setData({ boxes: boxes || [] }))
              .catch(() => undefined);
            fetchShipment(this.data.id)
              .then((shp) => this.setData({ shp }))
              .catch(() => undefined);
          })
          .catch(() => undefined);
      }
    });
  },

  // ============ 打印（出货单/装箱单；print_key 幂等） ============

  onPrint(e: WechatMiniprogram.TouchEvent) {
    const tpl = String(e.currentTarget.dataset.tpl || 'shipment') as ShipmentTpl;
    if (this.data.printing) return;
    const printKey = createShipmentPrintKey(this.data.id, tpl);
    this.setData({ printing: true });
    openShipmentPrintFile(this.data.id, tpl)
      .then(() => reportShipmentPrint(this.data.id, tpl, printKey))
      .then((res) => {
        if (res.duplicated && res.duplicated.length) {
          wx.showToast({ title: '该打印请求已记录', icon: 'none' });
        } else if (res.is_reprint) {
          wx.showToast({ title: '补打已记录', icon: 'none' });
        } else {
          wx.showToast({ title: '打印已记录', icon: 'success' });
        }
        return fetchPrintLogs(this.data.id);
      })
      .then((logs) => this.setData({ printLogs: logs || [] }))
      .catch((error: unknown) => {
        const message = error instanceof ApiError ? error.message : '出货 PDF 打开失败';
        wx.showToast({ title: message, icon: 'none' });
      })
      .finally(() => this.setData({ printing: false }));
  }
});
