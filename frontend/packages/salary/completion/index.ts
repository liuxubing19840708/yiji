/**
 * 完成汇总（规格 §13，三层钻取）：
 * 完成总数 + 快捷日期（date-range-bar）+ 筛选（员工/客户）
 * → 款式卡（款号/件数/单价/总额）→ 工序行 → 颜色×尺码矩阵 + 逐员工工资表。
 */
import { requireLogin } from '../../../utils/auth';
import { money, monthRange } from '../../../utils/format';
import {
  fetchCompletion, fetchCompanyEmployees,
  CompletionEmployeeRow, SalaryEmployeeCard
} from '../../../services/salary';
import { fetchCustomers } from '../../../services/catalog';
import {
  fetchDashboardDim, DashboardDim, DashboardDimColumn, DashboardDimRow
} from '../../../services/dashboard';
import { buildCsv, shareCsvFile } from '../../../utils/csv';
import { CompletionSummary } from '../../../services/types';

/** 维度汇总 tab（与看板 dim 同源 /dashboard/dim；订单/裁床行可下钻） */
const DIM_TABS = [
  { key: 'order', label: '订单' },
  { key: 'bed', label: '裁床' },
  { key: 'post', label: '部门' },
  { key: 'group', label: '组' },
  { key: 'user', label: '人' }
] as Array<{ key: Exclude<DashboardDim, 'style'>; label: string }>;

interface StyleVM {
  styleId: number;
  styleNo: string;
  styleName: string;
  qty: number;
  priceText: string;
  amountText: string;
  processCount: number;
}

interface ProcessVM {
  processId: number;
  name: string;
  qty: number;
  priceText: string;
  amountText: string;
  users: number;
}

interface MatrixRowVM {
  color: string;
  sizesText: string; // 「M 108  L 51」拼接，避免页面内复杂模板
}

interface EmpVM {
  userId: number;
  userName: string;
  qty: number;
  priceText: string;
  amountText: string;
}

interface FilterOption {
  id: number;
  name: string;
}

Page({
  data: {
    start: '',
    end: '',
    quick: '',
    loading: true,
    error: '',
    // 视图切换（2026-10-29 与看板维度查看合并）：style=三层钻取 / dim=维度汇总
    view: 'style' as 'style' | 'dim',
    dimTabs: DIM_TABS,
    dim: 'order' as Exclude<DashboardDim, 'style'>,
    dimCols: [] as DashboardDimColumn[],
    dimRows: [] as DashboardDimRow[],
    dimLoading: false,
    dimError: false,
    // 筛选
    userOptions: [] as FilterOption[],
    userIndex: 0,
    customerOptions: [] as FilterOption[],
    customerIndex: 0,
    // 三层
    level: 1,
    totalQty: 0,
    styles: [] as StyleVM[],
    styleId: 0,
    styleNo: '',
    processes: [] as ProcessVM[],
    processId: 0,
    processName: '',
    matrix: [] as MatrixRowVM[],
    employees: [] as EmpVM[]
  },

  onLoad() {
    const r = monthRange();
    this.setData({ start: r.start, end: r.end, quick: 'month' });
  },

  onShow() {
    if (!requireLogin()) return;
    if (!this.data.userOptions.length) {
      this.loadFilters();
    }
    this.load();
  },

  onPullDownRefresh() {
    const jobs: Array<Promise<void>> = [this.load(), this.loadFilters()];
    if (this.data.view === 'dim') jobs.push(this.loadDimView());
    Promise.all(jobs)
      .then(() => wx.stopPullDownRefresh())
      .catch(() => wx.stopPullDownRefresh());
  },

  loadFilters(): Promise<void> {
    const users: Promise<void> = fetchCompanyEmployees()
      .then((list: SalaryEmployeeCard[]) => {
        this.setData({
          userOptions: [{ id: 0, name: '全部员工' }].concat(
            list.map((x) => ({ id: x.user_id, name: x.real_name })))
        });
      })
      .catch(() => undefined);
    const customers: Promise<void> = fetchCustomers({ page_size: 100 })
      .then((res) => {
        this.setData({
          customerOptions: [{ id: 0, name: '全部客户' }].concat(
            res.results.map((x) => ({ id: x.id, name: x.name })))
        });
      })
      .catch(() => undefined);
    return Promise.all([users, customers]).then(() => undefined);
  },

  baseParams(): Record<string, string | number> {
    const params: Record<string, string | number> = {};
    if (this.data.start) params.date_from = this.data.start;
    if (this.data.end) params.date_to = this.data.end;
    const u = this.data.userOptions[this.data.userIndex];
    if (u && u.id) params.user_id = u.id;
    const cst = this.data.customerOptions[this.data.customerIndex];
    if (cst && cst.id) params.customer_id = cst.id;
    return params;
  },

  load(): Promise<void> {
    this.setData({ loading: true, error: '' });
    return fetchCompletion(this.baseParams())
      .then((res: CompletionSummary) => {
        this.applyStyles(res);
        if (this.data.level >= 2 && this.data.styleId) {
          return this.loadLevel3();
        }
        return undefined;
      })
      .then(() => this.setData({ loading: false }))
      .catch(() => this.setData({ loading: false, error: '加载失败，请重试' }));
  },

  applyStyles(res: CompletionSummary) {
    const styles: StyleVM[] = res.styles.map((s) => ({
      styleId: s.style_id,
      styleNo: s.style_no,
      styleName: s.style_name,
      qty: s.qty,
      priceText: money(s.avg_price),
      amountText: money(s.amount),
      processCount: s.process_count
    }));
    const totalQty = res.styles.reduce((sum, s) => sum + s.qty, 0);
    this.setData({
      level: 1,
      styles,
      totalQty,
      styleId: 0,
      styleNo: '',
      processId: 0,
      processName: '',
      processes: [],
      matrix: [],
      employees: []
    });
  },

  loadLevel3(): Promise<void> {
    const params = this.baseParams();
    params.style_id = this.data.styleId;
    if (this.data.level >= 3 && this.data.processId) {
      params.process_id = this.data.processId;
    }
    return fetchCompletion(params).then((res) => {
      if (this.data.level === 2) {
        this.setData({
          processes: res.processes.map((p) => ({
            processId: p.process_id,
            name: p.name,
            qty: p.qty,
            priceText: money(p.avg_price),
            amountText: money(p.amount),
            users: p.users
          }))
        });
      } else {
        this.setData({
          matrix: res.matrix.map((m) => ({
            color: m.color,
            sizesText: m.sizes.map((s) => `${s.size} ${s.qty}`).join('　')
          })),
          employees: (res.employees as CompletionEmployeeRow[]).map((e) => ({
            userId: e.user_id,
            userName: e.user_name,
            qty: e.qty,
            priceText: money(e.avg_price || '0'),
            amountText: money(e.amount)
          }))
        });
      }
    });
  },

  onRangeChange(e: WechatMiniprogram.CustomEvent<{ start: string; end: string; quick: string }>) {
    this.setData({ start: e.detail.start, end: e.detail.end, quick: e.detail.quick });
    this.load();
    // 维度视图随日期联动刷新
    if (this.data.view === 'dim') this.loadDimView();
  },

  // ============================================================
  // 维度汇总视图（2026-10-29 合并看板「维度查看」：/dashboard/dim 同源）
  // ============================================================

  /** 视图切换：款式钻取=三层钻取；维度汇总=按订单/裁床/部门/组/人聚合 */
  onViewTap(e: WechatMiniprogram.TouchEvent) {
    const key = String(e.currentTarget.dataset.key || 'style');
    if (key === this.data.view) return;
    this.setData({ view: key === 'dim' ? 'dim' : 'style' });
    if (key === 'dim' && !this.data.dimCols.length) this.loadDimView();
  },

  /** 切维度：拉 /dashboard/dim 聚合表（range=custom 复用页顶日期区间） */
  onDimTab(e: WechatMiniprogram.TouchEvent) {
    const key = String(e.currentTarget.dataset.key || 'order') as Exclude<DashboardDim, 'style'>;
    if (!DIM_TABS.some((t) => t.key === key) || key === this.data.dim) return;
    this.setData({ dim: key });
    this.loadDimView();
  },

  loadDimView(): Promise<void> {
    this.setData({ dimLoading: true, dimError: false });
    return fetchDashboardDim(this.data.dim, 'custom',
      { start: this.data.start, end: this.data.end })
      .then((res) => this.setData({
        dimLoading: false,
        dimCols: res.columns || [],
        dimRows: res.rows || [],
        dimError: false
      }))
      .catch(() => this.setData({ dimLoading: false, dimError: true }));
  },

  onDimRetry() {
    if (this.data.dimLoading) return;
    this.loadDimView();
  },

  /** 维度行下钻：订单→订单详情、裁床→床次详情（后端 nav 白名单） */
  onDimRowTap(e: WechatMiniprogram.TouchEvent) {
    const row = this.data.dimRows[Number(e.currentTarget.dataset.index)];
    if (!row || !row.nav) return;
    const url = row.nav === 'order'
      ? `/packages/admin/order-detail/index?id=${row.key}`
      : `/packages/admin/production-detail/index?id=${row.key}`;
    wx.navigateTo({
      url,
      fail: () => wx.showToast({ title: '页面未就绪', icon: 'none' })
    });
  },

  /** 维度表导出 CSV（评审 Top10 #9）：当前 tab + 日期范围的整表 */
  onDimExport() {
    const { dimCols, dimRows, dim } = this.data;
    if (!dimRows.length) {
      wx.showToast({ title: '没有可导出的数据', icon: 'none' });
      return;
    }
    const tabLabel = DIM_TABS.find((t) => t.key === dim)?.label || dim;
    const headers = dimCols.map((c) => c.title);
    const rows = dimRows.map((r) => r.cells.map((c) => String(c ?? '')));
    shareCsvFile(`完成汇总_${tabLabel}_${this.data.start}_${this.data.end}.csv`,
      buildCsv(headers, rows))
      .catch(() => wx.showToast({ title: '导出失败', icon: 'none' }));
  },

  onUserFilter(e: WechatMiniprogram.PickerChange) {
    this.setData({ userIndex: Number(e.detail.value) });
    this.load();
  },

  onCustomerFilter(e: WechatMiniprogram.PickerChange) {
    this.setData({ customerIndex: Number(e.detail.value) });
    this.load();
  },

  onOpenStyle(e: WechatMiniprogram.TouchEvent) {
    const styleId = Number(e.currentTarget.dataset.id);
    const styleNo = String(e.currentTarget.dataset.no || '');
    this.setData({ level: 2, styleId, styleNo, processes: [] });
    this.loadLevel3().catch(() => wx.showToast({ title: '加载失败', icon: 'none' }));
  },

  onOpenProcess(e: WechatMiniprogram.TouchEvent) {
    const processId = Number(e.currentTarget.dataset.id);
    const processName = String(e.currentTarget.dataset.name || '');
    this.setData({ level: 3, processId, processName, matrix: [], employees: [] });
    this.loadLevel3().catch(() => wx.showToast({ title: '加载失败', icon: 'none' }));
  },

  onBackLevel1() {
    this.load();
  },

  onBackLevel2() {
    this.setData({ level: 2, processId: 0, processName: '', matrix: [], employees: [] });
    this.loadLevel3().catch(() => undefined);
  }
});
