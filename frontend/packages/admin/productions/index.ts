/**
 * 生产管理列表（§八）：床次订单卡片 + 状态筛选。
 * ⋮ 菜单：详情 / 锁定·解锁 / 置裁完·取消裁完 / 打印（蓝牙打印该床次菲票，§7）。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import {
  fetchProductions, lockProduction, cutDoneProduction, fetchProductionBundles
} from '../../../services/production';
import { fetchProductionLotLinks } from '../../../services/warehouse';
import { getSkipPreview } from '../../../services/printer';
import { startPrintFlow, cancelPrintFlow } from '../../../utils/print-flow';
import { PrintTicketData } from '../../../utils/bluetooth/templates';
import { mediaUrl } from '../../../utils/media';
import { Production, Paginated } from '../../../services/types';

const PAGE_SIZE = 20;

interface ProductionVM extends Production {
  ticketCount: number;
  registeredQty: number;
  doneTicketCount: number;
  imageUrl: string;
}

/** 补齐后端聚合统计字段（types.ts 暂缺，防御性读取） */
function toVM(p: Production): ProductionVM {
  const raw = p as unknown as Record<string, unknown>;
  return {
    ...p,
    ticketCount: Number(raw.ticket_count) || 0,
    registeredQty: Number(raw.registered_qty) || 0,
    doneTicketCount: Number(raw.done_ticket_count) || 0,
    imageUrl: mediaUrl(String(raw.style_image || ''))
  };
}

const FILTERS = [
  { key: 'all', label: '全部', isLocked: '', isCutDone: '' },
  { key: 'uncut', label: '未裁完', isLocked: '', isCutDone: '0' },
  { key: 'cut', label: '已裁完', isLocked: '', isCutDone: '1' },
  { key: 'locked', label: '已锁定', isLocked: '1', isCutDone: '' }
] as const;

Page({
  data: {
    keyword: '',
    filterIndex: 0,
    filters: FILTERS,
    productions: [] as ProductionVM[],
    page: 1,
    hasMore: false,
    loading: false,
    error: false,
    // ---- 看板下钻预过滤（§2.9 P0）：styleId 主过滤 + styleNo 落搜索框 ----
    drillStyleId: 0,
    drillStyleNo: '',
    // ---- 打印预览弹层 ----
    previewShow: false,
    previewTickets: [] as PrintTicketData[],
    /** 打印门槛（H4）：⋮ 菜单「打印」按 print.manage ∪ production.manage 显隐 */
    canPrint: false,
    previewCompany: { name: '' } as { name?: string },
    /** 打印流程状态（print-progress 组件，utils/print-flow 驱动） */
    printFlow: { show: false, done: 0, total: 0 }
  },

  /**
   * 带参进入（看板下钻第一级）：?styleId=&styleNo=（encodeURIComponent，onLoad 自动解码）。
   * 只做加法：无参数时行为与原页面完全一致。
   */
  onLoad(options: Record<string, string>) {
    this.setData({ canPrint: hasPerm('print.manage') || hasPerm('production.manage') });
    const styleId = Number(options.styleId) || 0;
    const styleNo = options.styleNo ? decodeURIComponent(options.styleNo) : '';
    if (styleId || styleNo) {
      this.setData({ drillStyleId: styleId, drillStyleNo: styleNo, keyword: styleNo });
    }
  },

  /** 解除下钻筛选：清款过滤与搜索词，回到全量列表 */
  onClearDrill() {
    this.setData({ drillStyleId: 0, drillStyleNo: '', keyword: '' });
    this.reload();
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
    const f = FILTERS[this.data.filterIndex];
    const params: Record<string, unknown> = { page: 1, page_size: PAGE_SIZE };
    const kw = this.data.keyword.trim();
    if (kw) params.keyword = kw; // 后端支持 keyword，services 参数类型暂缺该字段（已上报）
    if (this.data.drillStyleId) params.style_id = this.data.drillStyleId; // 看板下钻预过滤
    if (f.isLocked !== '') params.is_locked = f.isLocked;
    if (f.isCutDone !== '') params.is_cut_done = f.isCutDone;
    this.setData({ loading: true, error: false, page: 1, productions: [] });
    fetchProductions(params as Parameters<typeof fetchProductions>[0])
      .then((res: Paginated<Production>) => {
        this.setData({
          productions: (res.results || []).map(toVM),
          hasMore: !!res.next,
          loading: false
        });
      })
      .catch(() => this.setData({ loading: false, error: true }));
  },

  loadMore() {
    if (!this.data.hasMore || this.data.loading) return;
    const nextPage = this.data.page + 1;
    const f = FILTERS[this.data.filterIndex];
    const params: Record<string, unknown> = { page: nextPage, page_size: PAGE_SIZE };
    const kw = this.data.keyword.trim();
    if (kw) params.keyword = kw;
    if (this.data.drillStyleId) params.style_id = this.data.drillStyleId; // 看板下钻预过滤
    if (f.isLocked !== '') params.is_locked = f.isLocked;
    if (f.isCutDone !== '') params.is_cut_done = f.isCutDone;
    this.setData({ loading: true });
    fetchProductions(params as Parameters<typeof fetchProductions>[0])
      .then((res: Paginated<Production>) => {
        this.setData({
          productions: this.data.productions.concat((res.results || []).map(toVM)),
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

  onFilter(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    if (idx === this.data.filterIndex) return;
    this.setData({ filterIndex: idx });
    this.reload();
  },

  onOpenDetail(e: WechatMiniprogram.TouchEvent) {
    const p = this.data.productions[Number(e.currentTarget.dataset.index)];
    if (!p) return;
    wx.navigateTo({ url: `/packages/admin/production-detail/index?id=${p.id}` });
  },

  // ============ 卡片 ⋮ 菜单 ============

  /**
   * 卡片 ⋮ 菜单：操作项按权限点显隐（权限点体系二期）——
   * 锁定/解锁床次 → production.lock（高危点，legacy coarse 不覆盖）；
   * 详情/裁完/打印属 production.manage（进本页已过门）。
   */
  onCardMenu(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const p = this.data.productions[idx];
    if (!p) return;
    const actions: Array<{ label: string; run: () => void }> = [
      { label: '床次详情', run: () => wx.navigateTo({ url: `/packages/admin/production-detail/index?id=${p.id}` }) },
      // 按扎卡片（竞品对齐借鉴#1，2026-11-05）：扎卡+工序价目+行内改数/撤销
      { label: '按扎卡片', run: () => wx.navigateTo({
        url: `/packages/admin/bundle-view/index?production_id=${p.id}`
          + `&bed_no=${encodeURIComponent(p.bed_no)}`
          + `&style_no=${encodeURIComponent(p.style_no || '')}`
      }) }
    ];
    if (hasPerm('production.lock')) {
      actions.push({ label: p.is_locked ? '解锁床次' : '锁定床次', run: () => this.toggleLock(idx) });
    }
    actions.push({ label: p.is_cut_done ? '取消裁完' : '置为裁完', run: () => this.toggleCutDone(idx) });
    // 打印/补打门槛（H4）：无 print.manage / production.manage 不出菜单项
    if (hasPerm('print.manage') || hasPerm('production.manage')) {
      actions.push({ label: '打印', run: () => this.doPrint(idx) });
    }
    wx.showActionSheet({
      itemList: actions.map((a) => a.label),
      success: (res) => {
        const act = actions[res.tapIndex];
        if (act) act.run();
      }
    });
  },

  toggleLock(idx: number) {
    const p = this.data.productions[idx];
    if (!p) return;
    const target = !p.is_locked;
    lockProduction(p.id, target)
      .then(() => {
        wx.showToast({ title: target ? '已锁定' : '已解锁', icon: 'success' });
        this.reload();
      })
      .catch(() => { /* toast 已由请求层弹出 */ });
  },

  toggleCutDone(idx: number) {
    const p = this.data.productions[idx];
    if (!p) return;
    const target = !p.is_cut_done;
    cutDoneProduction(p.id, target)
      .then(() => {
        wx.showToast({ title: target ? '已标记裁完' : '已取消裁完', icon: 'success' });
        this.reload();
      })
      .catch(() => { /* toast 已由请求层弹出 */ });
  },

  // ============ 打印（§7：卡片 ⋮ 菜单「打印」重打该床次菲票） ============

  /** 公司名（菲票底部一行），取自 globalData 的当前公司 */
  printCompany(): { name?: string } {
    const app = getApp<IAppOption>();
    const cur = app.globalData.companies.find((c) => c.id === app.globalData.companyId);
    return { name: cur ? cur.name : '' };
  },

  /** 拉该床次全部菲票（后端单页上限 100，按 count 逐页取全） */
  fetchAllBundles(productionId: number): Promise<Array<Record<string, unknown>>> {
    const rows: Array<Record<string, unknown>> = [];
    const pull = (page: number): Promise<void> =>
      fetchProductionBundles(productionId, { page, page_size: 100 }).then((res) => {
        rows.push(...(res.results || []));
        // count 为该床次总扎数；取满或到达护栏（50 页=5000 扎）为止
        if (rows.length < (res.count || 0) && page < 50) return pull(page + 1);
      });
    return pull(1).then(() => rows);
  },

  /** 拉该床次菲票 → 选择全部打印 / 仅未打印 → 预览 → printTickets */
  doPrint(idx: number) {
    const p = this.data.productions[idx];
    if (!p) return;
    wx.showLoading({ title: '获取菲票…', mask: true });
    // 质量追溯（P0）：标签可带 来源单号/批次（字段在「打印管理-标签样式」里勾选才打）
    Promise.all([this.fetchAllBundles(p.id), fetchProductionLotLinks(p.id).catch(() => [])])
      .then(([rows, links]) => {
        wx.hideLoading();
        const lotNo = (links || []).map((l) => l.lot_no).filter(Boolean).join(',');
        const srcNo = (p as unknown as Record<string, unknown>).source_order_no;
        const all: PrintTicketData[] = rows.map((row) => ({
          id: Number(row.id),
          qr_code: String(row.qr_code || ''),
          bed_no: p.bed_no,
          bundle_no: String(row.bundle_no || ''),
          color: String(row.color || ''),
          size: String(row.size || ''),
          qty: Number(row.qty) || 0,
          style_no: p.style_no || '',
          source_order_no: String(srcNo || ''),
          lot_no: lotNo
        }));
        if (!all.length) {
          wx.showToast({ title: '该床次暂无菲票', icon: 'none' });
          return;
        }
        const unprinted = rows
          .map((row, i) => ({ row, t: all[i] }))
          .filter((x) => !x.row.is_printed)
          .map((x) => x.t);
        const pickAndPreview = (list: PrintTicketData[]) => {
          // 开关关掉（预览里勾过「不再预览」）：跳过预览直接进打印流程
          if (getSkipPreview()) {
            this.printBundles(list);
            return;
          }
          this.setData({
            previewShow: true,
            previewTickets: list,
            previewCompany: this.printCompany()
          });
        };
        // 全部已打印过：重打全床是费纸操作，先防呆确认一次
        if (!unprinted.length) {
          wx.showModal({
            title: '该床次均已打印',
            content: `共 ${all.length} 扎菲票都已打印过，确认全部重打？`,
            success: (m) => {
              if (m.confirm) pickAndPreview(all);
            }
          });
          return;
        }
        const options = unprinted.length < all.length
          ? [`全部打印（${all.length} 扎）`, `仅未打印（${unprinted.length} 扎）`]
          : [`全部打印（${all.length} 扎）`];
        wx.showActionSheet({
          itemList: options,
          success: (r) => {
            // 打印前先出预览：按当前模板渲染标签，确认后才发蓝牙
            pickAndPreview(r.tapIndex === 1 ? unprinted : all);
          }
        });
      })
      .catch(() => {
        wx.hideLoading();
        wx.showToast({ title: '获取菲票失败', icon: 'none' });
      });
  },

  /** 预览确认 → 实际打印 */
  onPreviewConfirm() {
    const tickets = this.data.previewTickets;
    this.setData({ previewShow: false, previewTickets: [] });
    if (tickets.length) this.printBundles(tickets);
  },

  /** 预览取消 */
  onPreviewCancel() {
    this.setData({ previewShow: false, previewTickets: [] });
  },

  /** 发起打印：统一走 print-flow（进度弹窗可取消 + 失败/未连接引导），成功刷新列表 */
  printBundles(tickets: PrintTicketData[]) {
    startPrintFlow(this, tickets, {
      company: this.printCompany(),
      onSuccess: () => {
        // 打印成功后刷新列表（L5：统计与列表状态回到最新）
        this.reload();
      }
    });
  },

  /** 打印进度弹层「取消打印」回调（print-flow 统一转发） */
  onFlowCancel() {
    cancelPrintFlow();
  }
});
