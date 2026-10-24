/**
 * 款式工序单价配置：从款式列表 ⋮「工序单价」进入。
 * 读取 fetchStyleProcesses（后端该 URL 仅有 POST 同步入口，读取失败时退回
 * fetchStyle().processes，见最终报告），保存走 syncStyleProcesses。
 * 单价一律字符串输入，不做本地金额运算。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { fetchStyleProcesses, updateStyleProcesses, fetchProcesses } from '../../../services/catalog';
import { StyleProcessItem, Process, Style, Paginated } from '../../../services/types';

/** 兼容分页/数组两种返回（services 契约与后端分页不一致，已上报） */
function unwrapList<T>(res: T[] | Paginated<T>): T[] {
  return Array.isArray(res) ? res : ((res as Paginated<T>).results || []);
}

interface RowVM {
  process: number;
  process_name: string;
  price: string;
  /** 跳序开关：false 时须先完成 sort 更小的工序才能登记本工序（2026-10-18 拍板） */
  allow_skip: boolean;
}

Page({
  data: {
    styleId: 0,
    styleNo: '',
    /** 工价编辑权限（styles.price）：入口已在款式页过门，此处兜底隐藏保存/删行按钮 */
    canEdit: false,
    rows: [] as RowVM[],
    loading: false,
    error: false,
    saving: false,
    // 工序选择器（native picker 数据源）
    processOptions: [] as Array<{ id: number; name: string }>,
    processNames: [] as string[]
  },

  onLoad(options: Record<string, string>) {
    this.setData({
      styleId: Number(options.styleId) || 0,
      styleNo: options.styleNo ? decodeURIComponent(options.styleNo) : '',
      canEdit: hasPerm('styles.price')
    });
    if (options.styleNo) {
      wx.setNavigationBarTitle({ title: `工序单价 - ${this.data.styleNo}` });
    }
    this.loadProcessOptions();
  },

  onShow() {
    if (!requireLogin()) return;
    this.reload();
  },

  loadProcessOptions() {
    fetchProcesses()
      .then((res) => {
        const list = unwrapList<Process>(res);
        this.setData({
          processOptions: list.map((p) => ({ id: p.id, name: p.name })),
          processNames: list.map((p) => p.name)
        });
      })
      .catch(() => { /* 选择器加载失败不阻塞已有配置展示 */ });
  },

  reload() {
    if (!this.data.styleId) return;
    this.setData({ loading: true, error: false });
    // fetchStyleProcesses 走款式详情（Style.processes）
    fetchStyleProcesses(this.data.styleId)
      .then((items) => {
        this.applyRows(items.map((it: StyleProcessItem) => ({
          process: it.process,
          process_name: it.process_name,
          price: String(it.price || ''),
          allow_skip: it.allow_skip !== false
        })));
      })
      .catch(() => this.setData({ loading: false, error: true }));
  },

  applyRows(rows: RowVM[]) {
    this.setData({ rows, loading: false });
    if (!this.data.styleNo) {
      wx.setNavigationBarTitle({ title: `工序单价（${rows.length} 道）` });
    }
  },

  // ============ 行编辑 ============

  onPrice(e: WechatMiniprogram.Input) {
    const idx = Number(e.currentTarget.dataset.index);
    this.setData({ [`rows[${idx}].price`]: e.detail.value });
  },

  onToggleAllowSkip(e: WechatMiniprogram.SwitchChange) {
    const idx = Number(e.currentTarget.dataset.index);
    this.setData({ [`rows[${idx}].allow_skip`]: e.detail.value });
  },

  onPickProcess(e: WechatMiniprogram.PickerChange) {
    const idx = Number(e.detail.value);
    const p = this.data.processOptions[idx];
    if (!p) return;
    if (this.data.rows.some((r) => r.process === p.id)) {
      wx.showToast({ title: '该工序已在列表中', icon: 'none' });
      return;
    }
    const rows = this.data.rows.concat([{ process: p.id, process_name: p.name, price: '', allow_skip: true }]);
    this.setData({ rows });
  },

  onRemoveRow(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const rows = this.data.rows.slice();
    rows.splice(idx, 1);
    this.setData({ rows });
  },

  // ============ 保存 ============

  onSave() {
    if (this.data.saving) return;
    for (let i = 0; i < this.data.rows.length; i++) {
      const p = this.data.rows[i].price;
      if (p === '' || Number.isNaN(Number(p)) || Number(p) < 0) {
        wx.showToast({ title: `第 ${i + 1} 行单价不合法`, icon: 'none' });
        return;
      }
    }
    this.setData({ saving: true });
    const items = this.data.rows.map((r) => ({ process_id: r.process, price: r.price, allow_skip: r.allow_skip }));
    updateStyleProcesses(this.data.styleId, items)
      .then(() => {
        this.setData({ saving: false });
        wx.showToast({ title: '已保存', icon: 'success' });
        this.reload();
      })
      .catch(() => this.setData({ saving: false }));
  }
});
