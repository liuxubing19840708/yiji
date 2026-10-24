/**
 * 指定工序（规格 §9.1）：员工维度管理。
 * 选择员工 → 勾选该公司工序（多选，支持按名称搜索）→ 保存到 assigned_processes。
 * 读侧（GET /process-selector）已消费：固定工序 ∪ 本列表工序 = 员工扫菲可选工序。
 */
import { requireLogin } from '../../../utils/auth';
import { fetchEmployees } from '../../../services/auth';
import { fetchProcesses } from '../../../services/catalog';
import { setAssignedProcesses, EmployeeWithProcesses } from '../../../services/org';
import { Process } from '../../../services/types';

/** 员工选择项（横向 chip） */
interface EmpItem {
  id: number; // UserCompany 主键
  name: string;
  assigned: number[];
}

/** 工序展示项（勾选状态在 this.checkedIds 维护） */
interface ProcVM extends Process {
  checked: boolean;
}

Page({
  data: {
    loading: true,
    loadError: false,
    saving: false,
    /** 员工 chip 列表 */
    employees: [] as EmpItem[],
    /** 当前选中员工下标（-1 未选） */
    empIndex: -1,
    /** 工序列表（已按关键词过滤） */
    processes: [] as ProcVM[],
    keyword: '',
    selectedCount: 0
  },

  /** 全量工序（checked 状态以此为准，data.processes 是过滤视图） */
  allProcesses: [] as ProcVM[],
  /** 当前员工的已选工序 id（保序） */
  checkedIds: [] as number[],

  onShow() {
    if (!requireLogin()) return;
    this.load();
  },

  onPullDownRefresh() {
    this.load();
    wx.stopPullDownRefresh();
  },

  load() {
    this.setData({ loading: true, loadError: false });
    const empsP = fetchEmployees()
      .then((list): EmployeeWithProcesses[] => list)
      .catch(() => [] as EmployeeWithProcesses[]);
    Promise.all([empsP, fetchProcesses().catch(() => null as Process[] | null)])
      .then(([emps, procs]) => {
      if (procs === null) {
        this.setData({ loading: false, loadError: true, employees: [], processes: [] });
        return;
      }
      // 只允许给在职员工指定工序
      const employees: EmpItem[] = emps
        .filter((e) => e.status === 1)
        .map((e) => ({ id: e.id, name: e.name || `员工${e.phone || ''}`, assigned: e.assigned_processes || [] }));
      this.allProcesses = procs.map((p) => ({ ...p, checked: false }));
      const empIndex = employees.length ? 0 : -1;
      this.checkedIds = empIndex >= 0 ? [...employees[empIndex].assigned] : [];
      this.setData({ loading: false, employees, empIndex }, () => this.applyView());
    });
  },

  /** 按 checkedIds + 关键词重算视图 */
  applyView() {
    const kw = (this.data.keyword || '').trim();
    const processes = this.allProcesses
      .filter((p) => !kw || p.name.indexOf(kw) >= 0)
      .map((p) => ({ ...p, checked: this.checkedIds.indexOf(p.id) >= 0 }));
    this.setData({ processes, selectedCount: this.checkedIds.length });
  },

  // ---- 员工选择 ----

  onPickEmployee(e: WechatMiniprogram.TouchEvent) {
    const index = Number(e.currentTarget.dataset.index);
    if (index === this.data.empIndex) return;
    const emp = this.data.employees[index];
    if (!emp) return;
    // 切换员工前未保存的勾选作废，回到该员工已保存状态
    this.checkedIds = [...emp.assigned];
    this.setData({ empIndex: index }, () => this.applyView());
  },

  // ---- 搜索 / 勾选 ----

  onSearchInput(e: WechatMiniprogram.Input) {
    this.setData({ keyword: e.detail.value }, () => this.applyView());
  },

  onClearSearch() {
    this.setData({ keyword: '' }, () => this.applyView());
  },

  onToggle(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const idx = this.checkedIds.indexOf(id);
    if (idx >= 0) {
      this.checkedIds.splice(idx, 1);
    } else {
      this.checkedIds.push(id);
    }
    this.applyView();
  },

  // ---- 保存 ----

  onSave() {
    const emp = this.data.employees[this.data.empIndex];
    if (!emp || this.data.saving) return;
    const ids = [...this.checkedIds];
    const changed = JSON.stringify(ids) !== JSON.stringify([...emp.assigned].sort());
    if (!changed) {
      wx.showToast({ title: '未做修改', icon: 'none' });
      return;
    }
    this.setData({ saving: true });
    setAssignedProcesses(emp.id, ids).then((updated) => {
      this.data.employees[this.data.empIndex].assigned = updated.assigned_processes || ids;
      this.checkedIds = [...(updated.assigned_processes || ids)];
      this.setData({ saving: false });
      wx.showToast({ title: '已保存', icon: 'success' });
    }).catch(() => {
      this.setData({ saving: false });
    });
  }
});
