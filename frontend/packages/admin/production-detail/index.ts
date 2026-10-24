/**
 * 床次订单详情（§八）：基本信息 + 完成矩阵（复用订单矩阵构造，只显示本床次）+ 订单工序 + 菲票列表。
 * - 完成矩阵 = fetchBundleProgress + utils/completion-matrix（2026-10-22 用户需求）；
 *   失败退回菲票列表兜底。
 * - 订单工序可覆盖单价：updateOrderProcesses。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { money } from '../../../utils/format';
import { mediaUrl } from '../../../utils/media';
import {
  fetchProduction, fetchProductionBundles,
  fetchOrderProcesses, updateOrderProcesses, lockProduction, cutDoneProduction,
  updateProduction
} from '../../../services/production';
import { fetchLots, fetchProductionLotLinks, updateProductionLotLinks } from '../../../services/warehouse';
import { Lot } from '../../../services/warehouse';
import { fetchProcesses, fetchBundleProgress } from '../../../services/catalog';
import { printTickets } from '../../../services/printer';
import { PrintTicketData } from '../../../utils/bluetooth/templates';
import { Production, ProductionProcessItem, Process, Paginated, BundleProgressItem } from '../../../services/types';
import { buildBedMatrix } from '../../../utils/completion-matrix';

/** 兼容分页/数组两种返回（services 契约与后端分页不一致，已上报） */
function unwrapList<T>(res: T[] | Paginated<T>): T[] {
  return Array.isArray(res) ? res : ((res as Paginated<T>).results || []);
}

interface OrderRowVM {
  process: number;
  process_name: string;
  price: string;
  priceText: string;
  isSaved: boolean;
}

/** 完成矩阵行直接复用 utils/completion-matrix 的渲染就绪结构（buildBedMatrix 产出） */

/** 菲票卡（2026-10-22 参考竞品「按扎号查看」：扎号标题 + 值/标签四宫格 + 工序进度条） */
interface BundleVM {
  id: number;
  bundle_no: string;
  color: string;
  size: string;
  qty: number;
  /** 菲票信息：二维码内容 + 打印状态（2026-10-23 用户需求：每扎保存对应菲票信息） */
  qr_code: string;
  is_printed: boolean;
  /** 工序数 / 已完成工序数 / 进度% */
  processCount: number;
  regCount: number;
  pct: number;
  /** 完成人（该扎有登记的员工姓名，/ 连接） */
  workers: string;
}

Page({
  data: {
    id: 0,
    loading: false,
    error: false,
    prod: null as Production | null,
    styleImageUrl: '',
    /** 权限点体系二期：锁定/解锁按钮显隐（production.lock） */
    canLock: false,
    /** 打印门槛（H4）：查看菲票/补打按 print.manage ∪ production.manage 显隐 */
    canPrint: false,
    // 完成矩阵（buildBedMatrix 渲染就绪结构：多扎尺码占多列、每列一扎）
    cutLoaded: false,
    cutFailed: false,
    cutCols: [] as Array<{ uk: string; size: string }>,
    cutRows: [] as Array<{
      color: string;
      cells: Array<{ uk: string; qty: number | string; reg: number | string; left: number | string }>;
      bundleLabels: string[];
      total: { qty: number; reg: number; left: number };
    }>,
    cutTotalQty: 0,
    // 订单工序
    orderRows: [] as OrderRowVM[],
    orderLoading: false,
    savingOrder: false,
    processNames: [] as string[],
    processOptions: [] as Array<{ id: number; name: string }>,
    // 菲票列表
    bundles: [] as BundleVM[],
    bundlesLoading: false,
    /** 菲票预览/补打弹层（每扎保存的菲票信息，点「查看菲票」打开） */
    previewShow: false,
    previewTickets: [] as PrintTicketData[],
    previewCompany: { name: '' } as { name: string },
    // 质量追溯（P0 2026-10-18）：来源单号 + 床次×批次血缘（可补填改）
    sourceOrderNo: '',
    lotLinks: [] as Array<{ lot: number; lot_no: string; color: string; qty: string }>,
    lotOptions: [] as Array<{ id: number; label: string }>,
    lotPickIndex: -1,
    linkQty: '',
    savingTrace: false
  },

  onLoad(options: Record<string, string>) {
    // 权限点体系二期：锁定/解锁按钮按 production.lock 点显隐（列表页 ⋮ 菜单同口径）
    this.setData({
      id: Number(options.id) || 0,
      canLock: hasPerm('production.lock'),
      canPrint: hasPerm('print.manage') || hasPerm('production.manage')
    });
    this.loadProcessOptions();
  },

  onShow() {
    if (!requireLogin()) return;
    this.reload();
  },

  onPullDownRefresh() {
    this.reload();
    wx.stopPullDownRefresh();
  },

  reload() {
    if (!this.data.id) return;
    this.setData({ loading: true, error: false });
    fetchProduction(this.data.id)
      .then((p: Production) => {
        const raw = p as unknown as Record<string, unknown>;
        this.setData({
          prod: p,
          styleImageUrl: mediaUrl(String(raw.style_image || '')),
          loading: false
        });
        wx.setNavigationBarTitle({ title: `床次 ${p.bed_no}` });
      })
      .catch(() => this.setData({ loading: false, error: true }));
    this.loadCutDetail();
    this.loadOrderProcesses();
    this.loadBundles();
    // 质量追溯卡已按用户要求暂时下架（2026-10-22），loadTraceInfo 保留便于恢复
  },

  // ============ 质量追溯（来源单号 + 床次×批次血缘，可补填改） ============

  loadTraceInfo() {
    if (!this.data.id) return;
    this.setData({ sourceOrderNo: String((this.data.prod as unknown as Record<string, unknown>)?.source_order_no || '') });
    fetchProductionLotLinks(this.data.id)
      .then((links) => {
        this.setData({
          lotLinks: (links || []).map((l) => ({
            lot: l.lot, lot_no: l.lot_no, color: l.color || '', qty: String(l.qty || '0')
          }))
        });
      })
      .catch(() => { /* 血缘加载失败不阻塞详情 */ });
    if (!this.data.lotOptions.length) {
      fetchLots({ page_size: 100 }).then((res) => {
        const list = Array.isArray(res) ? res : (res.results || []);
        this.setData({
          lotOptions: (list as Lot[]).map((l) => ({ id: l.id, label: `${l.lot_no}${l.color ? '（' + l.color + '）' : ''}` }))
        });
      }).catch(() => { /* 批次选择器失败不阻塞详情 */ });
    }
  },

  onSourceOrderNo(e: WechatMiniprogram.Input) {
    this.setData({ sourceOrderNo: e.detail.value });
  },

  onSaveSourceOrderNo() {
    if (this.data.savingTrace || !this.data.prod) return;
    const p = this.data.prod;
    this.setData({ savingTrace: true });
    updateProduction(p.id, { source_order_no: this.data.sourceOrderNo.trim() })
      .then(() => {
        this.setData({ savingTrace: false });
        wx.showToast({ title: '来源单号已保存', icon: 'success' });
      })
      .catch(() => this.setData({ savingTrace: false }));
  },

  onPickLinkLot(e: WechatMiniprogram.PickerChange) {
    this.setData({ lotPickIndex: Number(e.detail.value) });
  },

  onLinkQty(e: WechatMiniprogram.Input) {
    this.setData({ linkQty: e.detail.value });
  },

  /** 追加一条血缘（合并已有 → updateProductionLotLinks 整表重建） */
  onAddLotLink() {
    const opt = this.data.lotOptions[this.data.lotPickIndex];
    if (!opt) {
      wx.showToast({ title: '请选批次', icon: 'none' });
      return;
    }
    if (this.data.lotLinks.some((l) => l.lot === opt.id)) {
      wx.showToast({ title: '该批次已挂', icon: 'none' });
      return;
    }
    const links = this.data.lotLinks.concat([{
      lot: opt.id, lot_no: opt.label, color: '',
      qty: this.data.linkQty.trim() || '0'
    }]);
    this.setData({ savingTrace: true });
    updateProductionLotLinks(this.data.id, links.map((l) => ({ lot_id: l.lot, qty: l.qty })))
      .then(() => {
        this.setData({ savingTrace: false, lotPickIndex: -1, linkQty: '' });
        wx.showToast({ title: '批次已挂接', icon: 'success' });
        this.loadTraceInfo();
      })
      .catch(() => this.setData({ savingTrace: false }));
  },

  onRemoveLotLink(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const links = this.data.lotLinks.slice();
    links.splice(idx, 1);
    this.setData({ savingTrace: true });
    updateProductionLotLinks(this.data.id, links.map((l) => ({ lot_id: l.lot, qty: l.qty })))
      .then(() => {
        this.setData({ savingTrace: false });
        wx.showToast({ title: '已移除', icon: 'success' });
        this.loadTraceInfo();
      })
      .catch(() => this.setData({ savingTrace: false }));
  },

  onOpenTrace() {
    wx.navigateTo({ url: `/packages/admin/trace-detail/index?mode=production&id=${this.data.id}` });
  },

  // ============ 完成矩阵（2026-10-22 用户需求：复用订单完成度矩阵代码，只显示本床次） ============

  loadCutDetail() {
    this.setData({ cutLoaded: false, cutFailed: false });
    fetchBundleProgress([this.data.id])
      .then((res) => {
        const items = (res.results || []).filter((t) => t.production_id === this.data.id);
        if (!items.length) {
          this.setData({ cutLoaded: true, cutCols: [], cutRows: [], cutTotalQty: 0 });
          return;
        }
        const m = buildBedMatrix(items);
        this.setData({
          cutLoaded: true,
          cutCols: m.cols,
          cutRows: m.rows,
          cutTotalQty: m.total.qty
        });
      })
      .catch(() => {
        // 完成矩阵接口失败：退回菲票列表兜底展示
        this.setData({ cutFailed: true });
        this.loadBundles();
      });
  },

  // ============ 订单工序 ============

  loadProcessOptions() {
    fetchProcesses()
      .then((res) => {
        const list = unwrapList<Process>(res);
        this.setData({
          processOptions: list.map((p) => ({ id: p.id, name: p.name })),
          processNames: list.map((p) => p.name)
        });
      })
      .catch(() => { /* 选择器失败不阻塞详情 */ });
  },

  loadOrderProcesses() {
    this.setData({ orderLoading: true });
    fetchOrderProcesses(this.data.id)
      .then((items) => {
        this.setData({
          orderLoading: false,
          orderRows: (items || []).map((it: ProductionProcessItem) => ({
            process: it.process,
            process_name: it.process_name,
            price: String(it.price || ''),
            priceText: money(it.price),
            isSaved: true
          }))
        });
      })
      .catch(() => this.setData({ orderLoading: false }));
  },

  onOrderPrice(e: WechatMiniprogram.Input) {
    const idx = Number(e.currentTarget.dataset.index);
    this.setData({ [`orderRows[${idx}].price`]: e.detail.value });
  },

  onPickProcess(e: WechatMiniprogram.PickerChange) {
    const idx = Number(e.detail.value);
    const p = this.data.processOptions[idx];
    if (!p) return;
    if (this.data.orderRows.some((r) => r.process === p.id)) {
      wx.showToast({ title: '该工序已在列表中', icon: 'none' });
      return;
    }
    this.setData({
      orderRows: this.data.orderRows.concat([{
        process: p.id,
        process_name: p.name,
        price: '',
        priceText: '',
        isSaved: false
      }])
    });
  },

  onRemoveOrderRow(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const rows = this.data.orderRows.slice();
    rows.splice(idx, 1);
    this.setData({ orderRows: rows });
  },

  onSaveOrderProcesses() {
    if (this.data.savingOrder) return;
    for (let i = 0; i < this.data.orderRows.length; i++) {
      const p = this.data.orderRows[i].price;
      if (p === '' || Number.isNaN(Number(p)) || Number(p) < 0) {
        wx.showToast({ title: `第 ${i + 1} 行单价不合法`, icon: 'none' });
        return;
      }
    }
    // 源头预检（调研 §8.3 痛点 #2）：0 价合法存在（质检等不计件），但要确认是知情保存，
    // 否则工人扫到该工序会被拦截、无法计件
    const zeroNames = this.data.orderRows
      .filter((r) => Number(r.price) === 0)
      .map((r) => r.process_name);
    if (zeroNames.length) {
      wx.showModal({
        title: '存在未上价工序',
        content: `【${zeroNames.join('、')}】单价为 0，登记时将无法计件，确定继续保存吗？`,
        confirmColor: '#FA5151',
        success: (res) => {
          if (res.confirm) this.doSaveOrderProcesses();
        }
      });
      return;
    }
    this.doSaveOrderProcesses();
  },

  doSaveOrderProcesses() {
    this.setData({ savingOrder: true });
    const items = this.data.orderRows.map((r) => ({ process_id: r.process, price: r.price }));
    updateOrderProcesses(this.data.id, items)
      .then(() => {
        this.setData({ savingOrder: false });
        wx.showToast({ title: '订单工序已保存', icon: 'success' });
        this.loadOrderProcesses();
      })
      .catch(() => this.setData({ savingOrder: false }));
  },

  // ============ 菲票列表 ============

  /**
   * 菲票 tap → 该扎登记明细（§2.9 P0 下钻第三级，带参预过滤 ticketId；
   * 登记页 onLoad 解析并按 ticket_id 过滤，返回时本页状态不变）
   */
  onOpenBundleRecords(e: WechatMiniprogram.TouchEvent) {
    const b = this.data.bundles[Number(e.currentTarget.dataset.index)];
    if (!b) return;
    wx.navigateTo({
      url: `/packages/admin/records/index?ticketId=${b.id}`,
      fail: (r) => {
        console.warn('records navigate fail:', r && r.errMsg);
        wx.showToast({ title: '页面未就绪', icon: 'none' });
      }
    });
  },

  /** 查看该扎菲票（2026-10-23：菲票列表每扎保存对应菲票信息，可回看/补打） */
  onShowBundleTicket(e: WechatMiniprogram.TouchEvent) {
    const b = this.data.bundles[Number(e.currentTarget.dataset.index)];
    if (!b || !b.qr_code) {
      wx.showToast({ title: '该扎暂无菲票信息', icon: 'none' });
      return;
    }
    const app = getApp<IAppOption>();
    const cur = app.globalData.companies.find((c) => c.id === app.globalData.companyId);
    const p = this.data.prod;
    const ticket: PrintTicketData = {
      id: b.id,
      qr_code: b.qr_code,
      bed_no: p ? String(p.bed_no || '') : '',
      bundle_no: b.bundle_no,
      color: b.color,
      size: b.size,
      qty: b.qty,
      style_no: p ? String(p.style_no || '') : '',
      source_order_no: p ? String((p as unknown as Record<string, unknown>).source_order_no || '') : '',
      // 页面未持有该床次批次号（追溯卡下架后 lotLinks 为空）：保持空串，不为它新增加载
      lot_no: ''
    };
    this.setData({
      previewShow: true,
      previewTickets: [ticket],
      previewCompany: { name: cur ? cur.name : '' }
    });
  },

  onPreviewCancel() {
    this.setData({ previewShow: false });
  },

  onPreviewConfirm() {
    const tickets = this.data.previewTickets;
    this.setData({ previewShow: false });
    if (!tickets.length) return;
    wx.showLoading({ title: `打印中 0/${tickets.length}…`, mask: true });
    printTickets(tickets, {
      company: this.data.previewCompany,
      onProgress: (done, total) => wx.showLoading({ title: `打印中 ${done}/${total}…`, mask: true })
    })
      .then((result) => {
        wx.hideLoading();
        const failed = result.failed.length;
        if (!failed) {
          wx.showToast({ title: `已打印 ${result.printed.length} 张`, icon: 'success' });
          // 打印成功即刷新打印状态徽标（后端 printTickets 已上报 is_printed）
          this.loadBundles();
        } else {
          wx.showModal({
            title: '部分未打印',
            content: `成功 ${result.printed.length} 张，${failed} 张失败已入重试队列，可到「打印管理」补打`,
            showCancel: false
          });
        }
      })
      .catch((err: Error) => {
        wx.hideLoading();
        wx.showModal({
          title: '未能打印',
          content: (err && err.message === '请先连接打印机')
            ? '先到「打印管理」页扫描绑定蓝牙打印机，再回来打印；也可跳过稍后补打'
            : (err && err.message) || '打印失败，可稍后到「打印管理」补打',
          showCancel: false
        });
      });
  },

  loadBundles() {
    this.setData({ bundlesLoading: true });
    // 并行拉扎级完成数据补「完成人」（失败不阻塞列表，仅缺人名列）
    Promise.all([
      fetchProductionBundles(this.data.id),
      fetchBundleProgress([this.data.id]).catch(() => null)
    ])
      .then(([raw, prog]) => {
        // 后端返回 {count, process_count, results}，services 声明为数组（已上报），防御性取 results
        const list = Array.isArray(raw)
          ? raw
          : ((raw as unknown as { results?: Array<Record<string, unknown>> }).results || []);
        const workersByTicket = new Map<number, BundleProgressItem>();
        ((prog && prog.results) || []).forEach((t) => {
          workersByTicket.set(t.ticket_id, t);
        });
        this.setData({
          bundlesLoading: false,
          bundles: list.map((t) => {
            const p = workersByTicket.get(Number(t.id));
            const pc = p ? Number(p.process_count) || 0 : 0;
            const reg = p ? Number(p.registered_process_count) || 0 : 0;
            return {
              id: Number(t.id),
              bundle_no: String(t.bundle_no || ''),
              color: String(t.color || '-'),
              size: String(t.size || '-'),
              qty: Number(t.qty) || 0,
              qr_code: String(t.qr_code || ''),
              is_printed: !!t.is_printed,
              processCount: pc,
              regCount: reg,
              pct: pc > 0 ? Math.min(100, Math.round((reg / pc) * 100)) : 0,
              workers: p ? (p.users || []).map((u) => u.name).filter(Boolean).join('/') : ''
            };
          })
        });
      })
      .catch(() => this.setData({ bundlesLoading: false }));
  },

  // ============ 锁定 / 裁完 ============

  onToggleLock() {
    const p = this.data.prod;
    if (!p) return;
    const target = !p.is_locked;
    lockProduction(p.id, target)
      .then(() => {
        wx.showToast({ title: target ? '已锁定' : '已解锁', icon: 'success' });
        this.reload();
      })
      .catch(() => { /* toast 已由请求层弹出 */ });
  },

  onToggleCutDone() {
    const p = this.data.prod;
    if (!p) return;
    const target = !p.is_cut_done;
    cutDoneProduction(p.id, target)
      .then(() => {
        wx.showToast({ title: target ? '已标记裁完' : '已取消裁完', icon: 'success' });
        this.reload();
      })
      .catch(() => { /* toast 已由请求层弹出 */ });
  }
});
