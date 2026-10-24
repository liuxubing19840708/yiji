/**
 * 打印服务（规格 §7）：打印队列 + 蓝牙下发 + 服务端打印上报。
 *
 * - 绑定打印机（deviceId/name）存 storage：yisao_printer
 * - 待打印队列存 storage：yisao_print_queue（打印失败自动入队，重进页面提示补打）
 * - 打印上报失败列表：yisao_print_report_pending（已出票但服务端计数未更新，
 *   下次进入打印管理页自动补报，避免重打出重复菲票）
 * - markPrinted → POST /tickets/print（print_count+1、is_printed=True，后端写审计日志；
 *   每次打印任务生成 print_key，后端按 (print_key, ticket) 唯一约束幂等去重）
 *
 * 页面一律走本服务，不直接调 utils/bluetooth。
 */
import { http, ApiError } from '../utils/request';
import * as bt from '../utils/bluetooth/adapter';
import {
  PrintTicketData, PrintCompanyInfo, buildLabelCommand, encodeGbk,
  LabelStyle, DEFAULT_LABEL_STYLE
} from '../utils/bluetooth/templates';

const KEY_PRINTER = 'yisao_printer';
const KEY_QUEUE = 'yisao_print_queue';
const KEY_STYLE = 'yisao_print_style';
const KEY_OFFSETS = 'yisao_print_offsets';
const KEY_REPORT_PENDING = 'yisao_print_report_pending';

/** 绑定的打印机 */
export interface BoundPrinter {
  deviceId: string;
  name: string;
}

/** 打印失败重试队列项（菲票字段 + 失败信息） */
export interface PrintQueueItem extends PrintTicketData {
  failedAt: number;
  reason: string;
}

export interface PrintResult {
  /** 成功出票的菲票 id */
  printed: number[];
  /** 失败入队的项 */
  failed: PrintQueueItem[];
}

// ============ 绑定打印机 ============

export function getPrinter(): BoundPrinter | null {
  try {
    const p = wx.getStorageSync(KEY_PRINTER) as BoundPrinter | '';
    if (p && p.deviceId) return p;
  } catch (e) { /* 读取失败视为未绑定 */ }
  return null;
}

export function setPrinter(printer: BoundPrinter): void {
  wx.setStorageSync(KEY_PRINTER, printer);
}

export function clearPrinter(): void {
  wx.removeStorageSync(KEY_PRINTER);
}

/** 绑定并立即连接（进入打印管理页选设备时调用） */
export async function bindPrinter(printer: BoundPrinter): Promise<void> {
  await bt.connectPrinter(printer.deviceId);
  setPrinter(printer);
}

/** 断开当前打印机并解除绑定 */
export async function unbindPrinter(): Promise<void> {
  const p = getPrinter();
  if (p) {
    await bt.disconnect(p.deviceId).catch(() => { /* 断开失败仍解除绑定 */ });
  }
  clearPrinter();
}

// ============ 菲票标签模板（LabelStyle，storage 缓存；公司设置 print_label_style 为准） ============

/** 深拷贝（LabelStyle 为纯 JSON 结构，JSON 法即可） */
function cloneStyle(s: LabelStyle): LabelStyle {
  return JSON.parse(JSON.stringify(s)) as LabelStyle;
}

/** 读取本地缓存的标签模板，缺项用默认经典款补齐 */
export function getLabelStyle(): LabelStyle {
  try {
    const raw = wx.getStorageSync(KEY_STYLE) as string | '';
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<LabelStyle>;
      return { ...cloneStyle(DEFAULT_LABEL_STYLE), ...parsed, fields: parsed.fields || DEFAULT_LABEL_STYLE.fields };
    }
  } catch (e) { /* 缓存损坏走默认 */ }
  return cloneStyle(DEFAULT_LABEL_STYLE);
}

/** 写本地缓存（进入打印管理页会从公司设置同步覆盖） */
export function setLabelStyle(style: LabelStyle): void {
  wx.setStorageSync(KEY_STYLE, JSON.stringify(style));
}

// ============ 每台打印机的对位偏移（机器级参数，2026-10-17） ============
// 「上下微调」补偿的是间隙传感器→打印头的机械距离，随机器个体而异：
// 公司默认存 style.offsetMm；个别机器（或不同型号）用 print_label_offsets
// 覆盖（打印名→mm 映射，公司设置全局同步，storage 做本机缓存）。

/** 机器偏移映射（打印机名 → 上下微调 mm） */
export type PrinterOffsetMap = Record<string, number>;

export function getPrinterOffsets(): PrinterOffsetMap {
  try {
    const raw = wx.getStorageSync(KEY_OFFSETS) as string | '';
    if (raw) {
      const parsed = JSON.parse(raw) as PrinterOffsetMap;
      if (parsed && typeof parsed === 'object') return parsed;
    }
  } catch (e) { /* 缓存损坏用空表 */ }
  return {};
}

/** 写入/更新某台机器的偏移（返回新映射，供同步公司设置） */
export function setPrinterOffset(printerName: string, mm: number): PrinterOffsetMap {
  const map = getPrinterOffsets();
  map[printerName] = mm;
  wx.setStorageSync(KEY_OFFSETS, JSON.stringify(map));
  return map;
}

/** 公司设置下发时整表覆盖本机缓存 */
export function mergePrinterOffsets(map: PrinterOffsetMap): void {
  if (map && typeof map === 'object') {
    wx.setStorageSync(KEY_OFFSETS, JSON.stringify(map));
  }
}

/** 某台机器的生效偏移：映射有该机器用该值，否则回落公司默认 */
export function printerOffset(printerName: string, fallbackMm: number): number {
  const v = getPrinterOffsets()[printerName];
  return typeof v === 'number' ? v : fallbackMm;
}

/** 实打生效的标签样式：公司默认 + 当前机器的对位偏移 */
export function getEffectiveLabelStyle(printerName?: string): LabelStyle {
  const style = getLabelStyle();
  const name = printerName !== undefined ? printerName : (getPrinter() ? getPrinter()!.name : '');
  return { ...style, offsetMm: printerOffset(name, style.offsetMm) };
}

/** 发送原始指令（走纸校准等非菲票场景，不计打印数、不入队列） */
export async function sendRaw(command: string): Promise<void> {
  const printer = getPrinter();
  if (!printer) {
    throw new Error('请先连接打印机');
  }
  await bt.ensureConnected(printer.deviceId);
  await bt.writeInChunks(printer.deviceId, encodeGbk(command));
}

// ============ 打印队列（storage 持久化，§7.2 失败重试） ============

export function getQueue(): PrintQueueItem[] {
  try {
    const q = wx.getStorageSync(KEY_QUEUE) as PrintQueueItem[] | '';
    return Array.isArray(q) ? q : [];
  } catch (e) {
    return [];
  }
}

function saveQueue(queue: PrintQueueItem[]): void {
  wx.setStorageSync(KEY_QUEUE, queue);
}

function enqueueFailed(items: PrintTicketData[], reason: string): PrintQueueItem[] {
  // 入队前按 ticket id 去重：同一张票不重复入队（重打失败场景防双份）
  const known = new Set(getQueue().map((it) => it.id));
  const added: PrintQueueItem[] = [];
  items.forEach((t) => {
    if (known.has(t.id)) return;
    known.add(t.id);
    added.push({
      ...t,
      failedAt: Date.now(),
      reason
    });
  });
  if (added.length) {
    saveQueue(getQueue().concat(added));
  }
  return added;
}

export function removeQueueItem(id: number): PrintQueueItem[] {
  const queue = getQueue().filter((item) => item.id !== id);
  saveQueue(queue);
  return queue;
}

export function clearQueue(): PrintQueueItem[] {
  saveQueue([]);
  return [];
}

/** 队列数量（重进页面提示补打用） */
export function queueCount(): number {
  return getQueue().length;
}

// ============ 服务端上报 ============

/** POST /tickets/print 响应（2026-10-24 契约）：
 *  reported=本次计数成功数；skipped=不存在/已删的票 id（不再 400）；
 *  duplicated=print_key 幂等命中（不重复计数） */
export interface MarkPrintedResult {
  reported: number;
  skipped: number[];
  duplicated: number[];
}

/** 本次打印任务的幂等键（≤64 字符）：后端按 (print_key, ticket) 唯一约束去重 */
function newPrintKey(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

/** 打印结果上报（silent：失败由调用方入补报列表/页面自行处理，不弹错误 toast）：
 *  POST /tickets/print（print_count+1 / is_printed / 审计日志） */
export function markPrinted(ticketIds: number[], printKey: string): Promise<MarkPrintedResult> {
  return http.post<MarkPrintedResult>(
    '/tickets/print',
    { ticket_ids: ticketIds, print_key: printKey },
    { silent: true }
  );
}

/** 补报列表项：菲票 id + 所属打印任务的 print_key（幂等键随票走，补报不重复计数） */
interface ReportPendingItem {
  id: number;
  key: string;
}

/** 旧版本（无 print_key）补报条目统一挂到固定 legacy 键：重试期间幂等键保持稳定 */
const LEGACY_PRINT_KEY = 'legacy';

function getReportPending(): ReportPendingItem[] {
  try {
    const v = wx.getStorageSync(KEY_REPORT_PENDING) as Array<ReportPendingItem | number> | '';
    if (!Array.isArray(v)) return [];
    return v
      .map((it): ReportPendingItem | null => {
        if (typeof it === 'number') return { id: it, key: LEGACY_PRINT_KEY };
        if (it && typeof it === 'object' && typeof (it as ReportPendingItem).id === 'number') {
          return { id: (it as ReportPendingItem).id, key: String((it as ReportPendingItem).key || LEGACY_PRINT_KEY) };
        }
        return null;
      })
      .filter((it): it is ReportPendingItem => it !== null);
  } catch (e) {
    return [];
  }
}

function saveReportPending(list: ReportPendingItem[]): void {
  if (list.length) {
    wx.setStorageSync(KEY_REPORT_PENDING, list);
  } else {
    wx.removeStorageSync(KEY_REPORT_PENDING);
  }
}

/** 补报：把上次已出票但上报失败的 id 按各自原 print_key 分组重新提交
 *  （幂等由后端 (print_key, ticket) 唯一约束保证，重复上报安全）。
 *  - 响应 skipped 的 id 从补报列表清除（已删菲票不再反复补报，M1）；
 *  - 403/400 不可恢复错误同样清除（无权限/参数不合法不无限重试，H4）；
 *  - 网络/5xx 保留，下次进入打印管理页再补报（silent 不打扰）。 */
export async function flushReportPending(): Promise<void> {
  let pending = getReportPending();
  if (!pending.length) return;
  const groups = new Map<string, number[]>();
  pending.forEach((p) => {
    const key = p.key || LEGACY_PRINT_KEY;
    const ids = groups.get(key);
    if (ids) ids.push(p.id);
    else groups.set(key, [p.id]);
  });
  for (const [key, ids] of groups) {
    let done = false;
    try {
      await markPrinted(ids, key);
      done = true;
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      if (status === 403 || status === 400) done = true; // 不可恢复：放弃该组
    }
    if (done) {
      const drop = new Set(ids);
      pending = pending.filter((it) => !drop.has(it.id));
    }
  }
  saveReportPending(pending);
}

// ============ 打印主流程 ============

/** 上报分段大小：打印循环内每累计 10 张成功即上报一段（大批次不一次性押上全部） */
const REPORT_SEGMENT = 10;

/**
 * 蓝牙打印一批菲票（§7.2）：
 *  1. 未绑定打印机/连接失败 → 整批入失败队列（可到打印管理补打）后抛错引导页面
 *  2. 逐张构建 TSPL 指令（坐标来源 = computeLabelLayout，与预览一致）→ 分包写入
 *  3. 单张失败不中断批次；失败项入 yisao_print_queue 供补打
 *  4. 成功项分段 markPrinted 上报（每 10 张一段，print_key 为本次任务幂等键）；
 *     上报失败进补报列表（不重打出票）
 *
 * opts.report = false 时不上报（测试打印）；opts.queue = false 时不入重试队列；
 * opts.onProgress(done, total) 每张出票后回调（批量打印进度展示用）。
 */
export async function printTickets(
  tickets: PrintTicketData[],
  opts?: {
    company?: PrintCompanyInfo; report?: boolean; queue?: boolean;
    onProgress?: (done: number, total: number) => void;
  }
): Promise<PrintResult> {
  if (!tickets.length) {
    return { printed: [], failed: [] };
  }
  const doReport = opts?.report !== false;
  const doQueue = opts?.queue !== false;
  const printer = getPrinter();
  if (!printer) {
    // 未连打印机也入失败队列（H3）：保证「可到打印管理补打」的文案成立
    if (doQueue) enqueueFailed(tickets, '未连接打印机');
    throw new Error('请先连接打印机');
  }

  try {
    await bt.ensureConnected(printer.deviceId);
  } catch (err) {
    // 连接失败（打印循环外抛错）：整批入失败队列后原样抛给页面
    if (doQueue) {
      enqueueFailed(tickets, err instanceof Error ? err.message : '连接打印机失败');
    }
    throw err;
  }

  // 对位偏移跟打印机走：同一公司多台机器各自调准，实打按绑定机器取值
  const style = getEffectiveLabelStyle(printer.name);

  // 本次打印任务的幂等键：分段上报共用，后端按 (print_key, ticket) 去重
  const printKey = newPrintKey();
  const printed: number[] = [];
  const failedRaw: PrintTicketData[] = [];
  /** 已成功上报（或确定放弃上报）的前缀长度；其后为待补报 */
  let reportOffset = 0;
  /** 某段上报失败后置位：本次任务不再逐段重试，循环结束统一入补报列表 */
  let reportBroken = false;

  /** 上报一段：网络/服务端错误返回 false（进补报列表）；403/400 不可恢复错误
   *  返回 true（放弃上报、也不进补报列表无限重试） */
  const reportSegment = async (ids: number[]): Promise<boolean> => {
    try {
      await markPrinted(ids, printKey);
      return true;
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      return status === 403 || status === 400;
    }
  };

  let failReason = '';
  let index = 0;
  for (; index < tickets.length; index++) {
    const ticket = tickets[index];
    try {
      const command = buildLabelCommand(style, ticket, opts?.company);
      await bt.writeInChunks(printer.deviceId, encodeGbk(command));
      printed.push(ticket.id);
      if (opts?.onProgress) opts.onProgress(index + 1, tickets.length);
      // 分段上报（H2）：每累计 REPORT_SEGMENT 张成功即提交一段
      if (doReport && !reportBroken && printed.length - reportOffset >= REPORT_SEGMENT) {
        const seg = printed.slice(reportOffset, reportOffset + REPORT_SEGMENT);
        if (await reportSegment(seg)) {
          reportOffset += seg.length;
        } else {
          reportBroken = true;
        }
      }
    } catch (err) {
      failReason = err instanceof Error ? err.message : '打印失败';
      failedRaw.push(ticket);
      if (failReason.indexOf('断开') >= 0 || failReason.indexOf('未找到该打印机') >= 0) {
        // 连接已断：跳过本张，剩余菲票不再逐张尝试，全部入队
        index++;
        break;
      }
    }
  }
  const rest = tickets.slice(index);
  const allFailed = failedRaw.concat(rest);

  if (doQueue && allFailed.length) {
    enqueueFailed(allFailed, failReason || '打印失败');
  }
  if (doReport && printed.length > reportOffset) {
    // 收尾：flush 剩余未上报的分段
    const unreported = printed.slice(reportOffset);
    if (!reportBroken && (await reportSegment(unreported))) {
      // 上报成功（或 403/400 放弃），无需补报
    } else {
      // 已出票但上报失败：记入补报列表（带本次 print_key），不重打（避免重复菲票）
      const merged = getReportPending().concat(unreported.map((id) => ({ id, key: printKey })));
      saveReportPending(merged);
    }
  }
  return { printed, failed: allFailed.map((t) => ({ ...t, failedAt: Date.now(), reason: failReason })) };
}

/**
 * 补打重试队列（打印管理页「一键补打」/ 单项重打共用）。
 * 队列项本身已在队列里，这里 queue:false 防止再次失败时重复入队；
 * 成功出票的项从队列移除，并正常上报服务端。
 */
export async function printQueueItems(items: PrintQueueItem[], company?: PrintCompanyInfo): Promise<PrintResult> {
  const result = await printTickets(items, { company, queue: false });
  if (result.printed.length) {
    const done = new Set(result.printed);
    saveQueue(getQueue().filter((item) => !done.has(item.id)));
  }
  return result;
}
