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
import { CompletionSummary } from '../../../services/types';

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
    Promise.all([this.load(), this.loadFilters()])
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
