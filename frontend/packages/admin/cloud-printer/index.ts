/**
 * 打印管理（规格 §7 / §21.4，蓝牙打印方案，页面路径沿用 cloud-printer）。
 *
 * - 已绑定打印机卡：名称 / 连接状态 / 断开
 * - 扫描绑定：BLE 扫描 → 列表选择 → 连接绑定
 * - 菲票版式：仅 TSPL 标签机；纸张尺寸/码大小/字段点选/上下微调
 * - 测试打印：不涉及服务端（不上报、不入队列）
 * - 打印队列：失败自动入队，重进页面提示补打；逐条重打 / 移除 / 一键补打 / 清空
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import {
  getPrinter, unbindPrinter, bindPrinter,
  getQueue, removeQueueItem, clearQueue, printTickets, printQueueItems, removeQueueItems,
  queueCount, flushReportPending,
  getLabelStyle, setLabelStyle, sendRaw,
  getPrinterOffsets, setPrinterOffset, mergePrinterOffsets, printerOffset,
  getPrinters, removePrinterFromList,
  getSkipPreview, setSkipPreview,
  BoundPrinter, PrintQueueItem
} from '../../../services/printer';
import { startPrintFlow, cancelPrintFlow } from '../../../utils/print-flow';
import * as bt from '../../../utils/bluetooth/adapter';
import {
  PrintTicketData, LabelStyle, PRINT_FIELDS, FieldKey, buildDiagCommand, resolveFontMult
} from '../../../utils/bluetooth/templates';
import { fetchSettings, saveSettings } from '../../../services/ops';
import { fetchTickets } from '../../../services/catalog';
import { Paginated, Ticket } from '../../../services/types';
import { BleDevice } from '../../../utils/bluetooth/adapter';

interface DeviceRow extends BleDevice {
  matched: boolean;
  rssiText: string;
}

/** 打印历史行（GET /tickets?is_printed=1&ordering=print_at） */
interface HistoryRow {
  id: number;
  title: string;
  sub: string;
  printAtText: string;
  countText: string;
  /** print_count > 1：打过不止一次（M3 补打过徽标） */
  reprinted: boolean;
  /** 补打所需原始字段（GET /tickets 行；lot_no/来源单号列表不返回，
   *  传空串与床次详情页补打行为一致） */
  qr_code: string;
  bed_no: string;
  bundle_no: string;
  color: string;
  size: string;
  qty: number;
  style_no: string;
}

function currentCompany(): { name?: string } {
  const app = getApp<IAppOption>();
  const cur = app.globalData.companies.find((c) => c.id === app.globalData.companyId);
  return { name: cur ? cur.name : '' };
}

function errText(err: unknown): string {
  return err instanceof Error ? err.message : '操作失败，请重试';
}

// ============ 菲票版式（LabelStyle，公司设置 print_label_style，全公司共用） ============

const PAPERS = [
  { label: '40×30mm', widthMm: 40, heightMm: 30 },
  { label: '40×90mm', widthMm: 40, heightMm: 90 },
  { label: '60×40mm', widthMm: 60, heightMm: 40 },
  { label: '50×80mm', widthMm: 50, heightMm: 80 }
];
const QR_UNITS = [
  { label: '小', unit: 3 },
  { label: '中', unit: 4 },
  { label: '大', unit: 6 }
];

/** 字段点选 chips（PRINT_FIELDS 全集 + 是否勾上） */
function fieldChips(fields: FieldKey[]): Array<{ key: FieldKey; label: string; on: boolean }> {
  return PRINT_FIELDS.map((f) => ({ ...f, on: fields.indexOf(f.key) >= 0 }));
}

function clone(style: LabelStyle): LabelStyle {
  return JSON.parse(JSON.stringify(style)) as LabelStyle;
}

Page({
  data: {
    printer: null as BoundPrinter | null,
    connected: false,
    scanning: false,
    devices: [] as DeviceRow[],
    /** 历史绑定档案（多机快速切换：裁床一台/后道一台） */
    printers: [] as BoundPrinter[],
    queue: [] as PrintQueueItem[],
    printing: false,
    /** 批量打印前是否先出预览确认（本机开关，熟手可关） */
    previewConfirm: !getSkipPreview(),
    /** 打印/补打按钮门槛（H4）：print.manage 或 production.manage */
    canPrint: false,
    /** 打印流程状态（print-progress 组件，utils/print-flow 驱动） */
    printFlow: { show: false, done: 0, total: 0 },
    // ---- 打印历史（已出票菲票，按最后打印时间倒序；按需加载） ----
    historyShow: false,
    historyLoading: false,
    history: [] as HistoryRow[],
    historyPage: 1,
    historyHasMore: false,
    historyKeyword: '',
    // ---- 试打预览弹层 ----
    previewShow: false,
    previewTickets: [] as PrintTicketData[],
    /** 试打预览确认后是否上报服务端（历史补打=true；测试打印=false 不计数） */
    previewReport: false,
    previewCompany: { name: '' } as { name?: string },
    style: getLabelStyle(),
    offsetMm: 0,
    paperLabel: '',
    qrUnitLabel: '',
    qrPosLabel: '',
    fontMm: '4.5',
    fieldChips: fieldChips(getLabelStyle().fields)
  },

  /** 历史搜索防抖计时器 */
  _historyTimer: null as number | null,

  onShow() {
    if (!requireLogin()) return;
    this.setData({
      canPrint: hasPerm('print.manage') || hasPerm('production.manage'),
      printers: getPrinters(),
      previewConfirm: !getSkipPreview()
    });
    this.refresh();
    this.applyStyle(getLabelStyle());
    this.loadLabelStyle();
    // 重进页面提示补打（§7.2 打印失败重试队列）
    const n = queueCount();
    if (n > 0) {
      wx.showToast({ title: `有 ${n} 张菲票待补打`, icon: 'none' });
    }
    // 补报上次已出票但上报失败的打印计数（不重打出票）
    flushReportPending();
  },

  /** 拉公司设置里的菲票版式（print_label_style）+ 各机器对位偏移
   *  （print_label_offsets），覆盖本机缓存 —— 全公司一份配置 */
  loadLabelStyle() {
    fetchSettings()
      .then((s) => {
        const map = s as Record<string, string>;
        const rawOff = String(map.print_label_offsets || '');
        if (rawOff && rawOff !== '{}') {
          try { mergePrinterOffsets(JSON.parse(rawOff) as Record<string, number>); } catch (e) { /* 忽略坏数据 */ }
        }
        const raw = String(map.print_label_style || '');
        if (!raw || raw === '{}') { this.applyStyle(getLabelStyle()); return; }
        const parsed = JSON.parse(raw) as Partial<LabelStyle>;
        const style: LabelStyle = {
          ...clone(getLabelStyle()),
          ...parsed,
          fields: parsed.fields || getLabelStyle().fields
        };
        setLabelStyle(style);
        this.applyStyle(style);
      })
      .catch(() => { /* 拉不到就用本机缓存 */ });
  },

  /** 样式落三处：storage 缓存 + data 展示 + 公司设置（失败仅本机生效） */
  persistStyle(style: LabelStyle) {
    setLabelStyle(style);
    this.applyStyle(style);
    saveSettings({ print_label_style: JSON.stringify(style) } as Record<string, string>)
      .catch(() => wx.showToast({ title: '已本机生效，保存到公司设置失败', icon: 'none' }));
  },

  applyStyle(style: LabelStyle) {
    const unit = QR_UNITS.find((u) => u.unit === style.qrUnit);
    // 微调显示值=当前绑定机器的生效偏移（无映射回落公司默认）
    const printer = this.data.printer;
    this.setData({
      style,
      offsetMm: printer ? printerOffset(printer.name, style.offsetMm) : style.offsetMm,
      paperLabel: `${style.widthMm}×${style.heightMm}mm`,
      qrUnitLabel: unit ? unit.label : '中',
      qrPosLabel: style.qrPosition === 'bottom' ? '码在下·两列' : '码在上',
      // 字号显示=字高 mm（24 点基础字高 3mm × 倍率），四舍五入去尾零
      fontMm: String(parseFloat((resolveFontMult(style) * 3).toFixed(2))),
      fieldChips: fieldChips(style.fields)
    });
  },

  /** 字号步进（±0.25mm 字高 → 倍率 ±1/12，合法域 3~7.5mm）；公司级同步 */
  onFontStep(e: WechatMiniprogram.TouchEvent) {
    const delta = Number((e.currentTarget.dataset as { delta: string }).delta) || 0;
    const next = Math.min(2.5, Math.max(1, Math.round((resolveFontMult(this.data.style) + delta / 3) * 12) / 12));
    this.persistStyle({ ...clone(this.data.style), preset: 'custom', fontMult: next });
  },

  /** 二维码位置：孔先出纸（孔区在纸头）选「码在下」，其余信息两列排上方；
   *  仅竖版纸张生效，横版固定码左文右 */
  onPickQrPos() {
    wx.showActionSheet({
      itemList: ['码在上（文字在下方）', '码在下（文字两列在上方）'],
      success: (res) => {
        const qrPosition: LabelStyle['qrPosition'] = res.tapIndex === 1 ? 'bottom' : 'top';
        this.persistStyle({ ...clone(this.data.style), preset: 'custom', qrPosition });
      }
    });
  },

  refresh() {
    const printer = getPrinter();
    this.setData({
      printer,
      connected: printer ? bt.isConnected(printer.deviceId) : false,
      queue: getQueue(),
      printers: getPrinters()
    });
  },

  /** 切换到历史绑定的某台机器（立即连接并设为当前） */
  onPickSavedPrinter(e: WechatMiniprogram.TouchEvent) {
    const target = this.data.printers[Number(e.currentTarget.dataset.index)];
    if (!target) return;
    if (this.data.printer && this.data.printer.deviceId === target.deviceId) return;
    wx.showLoading({ title: '连接中…', mask: true });
    bindPrinter({ deviceId: target.deviceId, name: target.name })
      .then(() => {
        wx.hideLoading();
        wx.showToast({ title: '已切换', icon: 'success' });
        this.refresh();
        this.applyStyle(getLabelStyle());
      })
      .catch((err) => {
        wx.hideLoading();
        wx.showToast({ title: errText(err), icon: 'none' });
      });
  },

  /** 删除档案（当前机器会一并断开解绑；对位偏移记录保留，重绑自动生效） */
  onRemoveSavedPrinter(e: WechatMiniprogram.TouchEvent) {
    const target = this.data.printers[Number(e.currentTarget.dataset.index)];
    if (!target) return;
    const isActive = !!(this.data.printer && this.data.printer.deviceId === target.deviceId);
    wx.showModal({
      title: '删除打印机档案',
      content: `删除「${target.name}」？${isActive ? '当前连接将一并断开。' : ''}`,
      success: (r) => {
        if (!r.confirm) return;
        const done = () => {
          removePrinterFromList(target.deviceId);
          this.refresh();
        };
        if (isActive) {
          unbindPrinter().then(done).catch(done);
        } else {
          done();
        }
      }
    });
  },

  /** 批量打印前预览确认开关（关=熟手直接打，勾过「不再预览」时在此恢复） */
  onTogglePreviewConfirm(e: WechatMiniprogram.SwitchChange) {
    setSkipPreview(!e.detail.value);
    this.setData({ previewConfirm: e.detail.value });
  },

  /** 打印进度弹层「取消打印」回调（print-flow 统一转发） */
  onFlowCancel() {
    cancelPrintFlow();
  },

  // ============ 扫描绑定 ============

  onScan() {
    if (this.data.scanning) return;
    this.setData({ scanning: true, devices: [] });
    bt.scanPrinters(3000)
      .then(({ matched, others }) => {
        const toRow = (d: BleDevice, tag: boolean): DeviceRow => ({
          ...d,
          matched: tag,
          rssiText: d.rssi ? `${d.rssi} dBm` : ''
        });
        const devices = matched.map((d) => toRow(d, true)).concat(others.map((d) => toRow(d, false)));
        if (!devices.length) {
          wx.showToast({ title: '未发现打印机，请确认已开机并在附近', icon: 'none' });
        }
        this.setData({ devices });
      })
      .catch((err) => wx.showToast({ title: errText(err), icon: 'none' }))
      .finally(() => this.setData({ scanning: false }));
  },

  onPickDevice(e: WechatMiniprogram.TouchEvent) {
    const device = this.data.devices[Number(e.currentTarget.dataset.index)];
    if (!device || this.data.scanning) return;
    wx.showLoading({ title: '连接中…', mask: true });
    bindPrinter({ deviceId: device.deviceId, name: device.name })
      .then(() => {
        wx.hideLoading();
        wx.showToast({ title: '连接成功', icon: 'success' });
        this.setData({ devices: [] });
        this.refresh();
        // 换了绑定机器：按新机器刷新对位偏移显示
        this.applyStyle(getLabelStyle());
      })
      .catch((err) => {
        wx.hideLoading();
        wx.showToast({ title: errText(err), icon: 'none' });
      });
  },

  onDisconnect() {
    wx.showLoading({ title: '断开中…', mask: true });
    unbindPrinter()
      .then(() => {
        wx.hideLoading();
        wx.showToast({ title: '已断开', icon: 'none' });
        this.refresh();
      })
      .catch((err) => {
        wx.hideLoading();
        wx.showToast({ title: errText(err), icon: 'none' });
      });
  },

  // ============ 菲票版式编辑（无预设：纸张/码大小/字段直接设，二维码位置固定居中） ============

  onPickPaper() {
    wx.showActionSheet({
      itemList: PAPERS.map((p) => p.label),
      success: (res) => {
        const p = PAPERS[res.tapIndex];
        if (p) this.persistStyle({ ...clone(this.data.style), preset: 'custom', widthMm: p.widthMm, heightMm: p.heightMm });
      }
    });
  },

  onPickQrUnit() {
    wx.showActionSheet({
      itemList: QR_UNITS.map((u) => u.label),
      success: (res) => {
        const u = QR_UNITS[res.tapIndex];
        if (u) this.persistStyle({ ...clone(this.data.style), preset: 'custom', qrUnit: u.unit });
      }
    });
  },

  onToggleBigQty(e: WechatMiniprogram.SwitchChange) {
    this.persistStyle({ ...clone(this.data.style), preset: 'custom', bigQty: e.detail.value });
  },

  /** 上下微调 ±1mm（打印机标签起点固定偏移的人工对冲，-40~40mm）。
   *  跟打印机走：已绑定机器→写该机器的偏移并同步公司设置；
   *  未绑定→写公司默认（style.offsetMm） */
  onOffsetStep(e: WechatMiniprogram.TouchEvent) {
    const delta = Number(e.currentTarget.dataset.delta) || 0;
    const cur = Number(this.data.offsetMm) || 0;
    const next = Math.max(-40, Math.min(40, cur + delta));
    if (next === cur) return;
    const printer = this.data.printer;
    if (printer) {
      const map = setPrinterOffset(printer.name, next);
      this.setData({ offsetMm: next });
      saveSettings({ print_label_offsets: JSON.stringify(map) } as Record<string, string>)
        .catch(() => wx.showToast({ title: '已本机生效，保存到公司设置失败', icon: 'none' }));
    } else {
      this.persistStyle({ ...clone(this.data.style), offsetMm: next });
    }
  },

  /** 点选/取消要打印的字段（顺序即渲染顺序，取消追加到已选末尾） */
  onToggleField(e: WechatMiniprogram.TouchEvent) {
    const key = String(e.currentTarget.dataset.key) as FieldKey;
    if (!key) return;
    const cur = clone(this.data.style);
    const idx = cur.fields.indexOf(key);
    if (idx >= 0) {
      if (cur.fields.length <= 1) {
        wx.showToast({ title: '至少保留一个字段', icon: 'none' });
        return;
      }
      cur.fields.splice(idx, 1);
    } else {
      cur.fields.push(key);
    }
    this.persistStyle({ ...cur, preset: 'custom' });
  },

  // ============ 测试打印 ============

  onTestPrint() {
    if (!this.data.printer) {
      wx.showToast({ title: '请先连接打印机', icon: 'none' });
      return;
    }
    wx.showLoading({ title: '取菲票数据…', mask: true });
    // 优先取一张真实菲票：测试票扫码可直接走登记流程（写死的演示码会解析失败）
    fetchTickets({ page: 1, page_size: 1 })
      .then((res) => {
        wx.hideLoading();
        const t = (res.results || [])[0];
        let demo: PrintTicketData;
        if (t && t.qr_code) {
          const parts = String(t.qr_code).split('|');
          demo = {
            id: 0,
            qr_code: t.qr_code,
            bed_no: t.bed_no,
            bundle_no: t.bundle_no,
            color: t.color,
            size: t.size,
            qty: t.qty,
            style_no: parts[2] || ''
          };
        } else {
          // 库里没票时兜底演示数据（该码扫不出，仅看版式）
          demo = {
            id: 0,
            qr_code: 'Y0001|40|DEMO01|8|红色|M|25',
            bed_no: '40',
            bundle_no: '8',
            color: '红色',
            size: 'M',
            qty: 25,
            style_no: 'DEMO01'
          };
        }
        // 试打前先预览：按当前模板渲染标签实打效果，确认后发送
        this.setData({
          previewShow: true,
          previewTickets: [demo],
          previewCompany: currentCompany()
        });
      })
      .catch(() => {
        wx.hideLoading();
        wx.showToast({ title: '获取菲票数据失败', icon: 'none' });
      });
  },

  /** 试打预览确认 → 发送打印指令 */
  onPreviewConfirm() {
    const tickets = this.data.previewTickets;
    const report = this.data.previewReport;
    this.setData({ previewShow: false, previewTickets: [], previewReport: false });
    if (!tickets.length) return;
    wx.showLoading({ title: '发送指令…', mask: true });
    // 测试打印不上报（report:false）；历史补打正常上报计数
    printTickets(tickets, { company: currentCompany(), report, queue: false })
      .then(() => {
        wx.hideLoading();
        this.setData({ connected: true });
        wx.showToast({ title: report ? '补打指令已发送' : '测试指令已发送', icon: 'success' });
        if (report) this.loadHistory(1);
      })
      .catch((err) => {
        wx.hideLoading();
        wx.showToast({ title: errText(err), icon: 'none' });
      });
  },

  /** 试打预览取消 */
  onPreviewCancel() {
    this.setData({ previewShow: false, previewTickets: [], previewReport: false });
  },

  /** 对位诊断：打 2 张带刻度尺的诊断签，用于精确测出打印起点偏移量/方向 */
  onCalibrate() {
    if (!this.data.printer) {
      wx.showToast({ title: '请先连接打印机', icon: 'none' });
      return;
    }
    wx.showLoading({ title: '诊断打印中…', mask: true });
    sendRaw(buildDiagCommand(this.data.style))
      .then(() => {
        wx.hideLoading();
        wx.showModal({
          title: '诊断签已发出',
          content: '会打出 2 张带刻度的诊断签。请拍下照片：注意每张四周边框是否完整、0mm 黑条离标签边缘多远、两张之间刻度位置是否一致',
          showCancel: false
        });
      })
      .catch((err) => {
        wx.hideLoading();
        wx.showToast({ title: errText(err), icon: 'none' });
      });
  },

  // ============ 打印队列 ============

  onRetryAll() {
    if (!this.data.canPrint) {
      wx.showToast({ title: '暂无打印权限', icon: 'none' });
      return;
    }
    const queue = this.data.queue;
    if (!queue.length || this.data.printing) return;
    wx.showModal({
      title: '一键补打',
      content: `将补打队列中的 ${queue.length} 张菲票，已贴标的请先移除，确认继续？`,
      success: (res) => {
        if (!res.confirm) return;
        this.setData({ printing: true });
        // 统一打印流程：进度弹窗可取消；queue:false 防再次失败重复入队，
        // 成功项补打完成后从队列移除
        startPrintFlow(this, queue, {
          company: currentCompany(),
          queue: false,
          onFinished: (result) => {
            if (result.printed.length) removeQueueItems(result.printed);
            this.setData({ printing: false });
            this.refresh();
          },
          onError: () => {
            this.setData({ printing: false });
            this.refresh();
          }
        });
      }
    });
  },

  onRetryItem(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canPrint) {
      wx.showToast({ title: '暂无打印权限', icon: 'none' });
      return;
    }
    const item = this.data.queue[Number(e.currentTarget.dataset.index)];
    if (!item || this.data.printing) return;
    wx.showLoading({ title: '重打中…', mask: true });
    printQueueItems([item], currentCompany())
      .then((result) => {
        wx.hideLoading();
        wx.showToast({
          title: result.printed.length ? '已重打' : '重打失败，已留在队列',
          icon: 'none'
        });
      })
      .catch((err) => wx.showToast({ title: errText(err), icon: 'none' }))
      .finally(() => this.refresh());
  },

  onRemoveItem(e: WechatMiniprogram.TouchEvent) {
    const item = this.data.queue[Number(e.currentTarget.dataset.index)];
    if (!item) return;
    removeQueueItem(item.id);
    this.refresh();
  },

  onClearQueue() {
    if (!this.data.queue.length) return;
    wx.showModal({
      title: '清空队列',
      content: `确认清空 ${this.data.queue.length} 张待补打菲票？`,
      success: (res) => {
        if (!res.confirm) return;
        clearQueue();
        this.refresh();
      }
    });
  },

  // ============ 打印历史（已出票菲票存档，方便回看） ============

  onToggleHistory() {
    const show = !this.data.historyShow;
    this.setData({ historyShow: show });
    // 展开即刷新第一页（不再仅空列表时才拉取，保证刚打的票马上可见）
    if (show) this.loadHistory(1);
  },

  /** 历史搜索输入（防抖 300ms，M3）：床次号/扎号关键字 */
  onHistorySearchInput(e: WechatMiniprogram.Input) {
    const value = e.detail.value;
    this.setData({ historyKeyword: value });
    if (this._historyTimer !== null) {
      clearTimeout(this._historyTimer);
      this._historyTimer = null;
    }
    if (!value.trim()) {
      // 清空即恢复全量第一页
      this.loadHistory(1);
      return;
    }
    this._historyTimer = setTimeout(() => {
      this._historyTimer = null;
      this.loadHistory(1);
    }, 300) as unknown as number;
  },

  /** 键盘确认搜索（立即执行，不等防抖） */
  onHistorySearchConfirm() {
    if (this._historyTimer !== null) {
      clearTimeout(this._historyTimer);
      this._historyTimer = null;
    }
    this.loadHistory(1);
  },

  loadHistory(page: number): Promise<void> {
    this.setData({ historyLoading: true });
    const base = { is_printed: true as boolean, page, page_size: 20 };
    const kw = (this.data.historyKeyword || '').trim();
    // 后端 GET /tickets 仅支持 bed_no/bundle_no 精确过滤（无关键字模糊）：
    // 先按床次号查，首页为空再按扎号查
    const req: Promise<Paginated<Ticket>> = kw
      ? fetchTickets({ ...base, bed_no: kw }).then((res) =>
          ((res.results || []).length || res.next)
            ? res
            : fetchTickets({ ...base, bundle_no: kw })
        )
      : fetchTickets(base);
    return req
      .then((res) => {
        const rows: HistoryRow[] = (res.results || []).map((t) => ({
          id: t.id,
          title: `床次 ${t.bed_no} 第 ${t.bundle_no} 扎 ×${t.qty}`,
          sub: `${t.style_no || ''} ${t.style_name || ''} ${t.color || ''} ${t.size || ''}`.trim(),
          printAtText: String(t.print_at || '').slice(0, 16).replace('T', ' '),
          countText: `打印 ${t.print_count} 次`,
          reprinted: Number(t.print_count || 0) > 1,
          qr_code: String(t.qr_code || ''),
          bed_no: String(t.bed_no || ''),
          bundle_no: String(t.bundle_no || ''),
          color: String(t.color || ''),
          size: String(t.size || ''),
          qty: Number(t.qty) || 0,
          style_no: String(t.style_no || '')
        }));
        this.setData({
          history: page === 1 ? rows : this.data.history.concat(rows),
          historyPage: page,
          historyHasMore: !!res.next,
          historyLoading: false
        });
      })
      .catch(() => this.setData({ historyLoading: false }));
  },

  /** 历史行「补打」（M3）：用行数据构造与床次详情补打一致的 payload，
   *  走既有 label-preview 弹层路径（确认后 report:true 正常计数） */
  onHistoryReprint(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canPrint) {
      wx.showToast({ title: '暂无打印权限', icon: 'none' });
      return;
    }
    const row = this.data.history[Number(e.currentTarget.dataset.index)];
    if (!row) return;
    if (!row.qr_code) {
      wx.showToast({ title: '该票缺少二维码信息，无法补打', icon: 'none' });
      return;
    }
    const ticket: PrintTicketData = {
      id: row.id,
      qr_code: row.qr_code,
      bed_no: row.bed_no,
      bundle_no: row.bundle_no,
      color: row.color,
      size: row.size,
      qty: row.qty,
      style_no: row.style_no,
      source_order_no: '',
      lot_no: ''
    };
    this.setData({
      previewShow: true,
      previewTickets: [ticket],
      previewCompany: currentCompany(),
      previewReport: true
    });
  },

  onHistoryMore() {
    if (this.data.historyLoading || !this.data.historyHasMore) return;
    this.loadHistory(this.data.historyPage + 1);
  }
});
