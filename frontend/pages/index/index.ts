/**
 * 首页工作台：
 * - 模块宫格按 isAdmin 切换模块集（配置驱动，规格 §4.2）
 * - 统计卡板块已按需求移除（2026-10-17）；日期条保留展示，暂无联动数据
 */
import { requireLogin } from '../../utils/auth';
import { refreshCompanyContext } from '../../services/auth';
import { hasAnyPerm, hasPerm } from '../../utils/perm';
import { monthRange } from '../../utils/format';
import { fetchWorkbench } from '../../services/production';

// 图标设计：语义 emoji + 按模块专属底色（module-grid/index.wxss 的 cell-icon-<key>），
// 零图片资源、各机型渲染一致；后续换 PNG/iconfont 只需替换 icon 字段。
// 宫格顺序 = 菜单动线（重设计 §1.3）：订单 → 生产 → 看板 → 薪酬 → 档案 → 仓库/追溯 → 日志/设置。
const ADMIN_MODULES = [
  { key: 'orders', name: '订单管理', icon: '📋', path: '/packages/admin/orders/index', visible: true },
  { key: 'production', name: '生产管理', icon: '🧵', path: '/packages/admin/productions/index', visible: true },
  { key: 'full-production', name: '全流程生产', icon: '🔁', path: '/packages/admin/full-production/index', visible: false },
  { key: 'kanban', name: '生产看板', icon: '📊', path: '/packages/admin/kanban/index', visible: true },
  { key: 'salary', name: '薪酬管理', icon: '💰', path: '/packages/salary/company/index', visible: true },
  { key: 'my-salary', name: '我的薪酬', icon: '💴', path: '/packages/salary/mine/index', visible: true },
  { key: 'order-salary', name: '订单薪酬', icon: '🧾', path: '/packages/salary/order/index', visible: false },
  { key: 'completion', name: '完成汇总', icon: '📊', path: '/packages/salary/completion/index', visible: false },
  { key: 'styles', name: '款式管理', icon: '🗂️', path: '/packages/admin/master/index?tab=styles', visible: true },
  { key: 'tech-materials', name: '物料管理', icon: '🧶', path: '/packages/admin/tech-materials/index', visible: false },
  { key: 'tech-boms', name: 'BOM管理', icon: '📐', path: '/packages/admin/tech-boms/index', visible: false },
  { key: 'customers', name: '客户管理', icon: '🤝', path: '/packages/admin/customers/index', visible: true },
  { key: 'employees', name: '员工管理', icon: '👥', path: '/packages/admin/employees/index', visible: true },
  { key: 'in-progress', name: '我的登记', icon: '🧾', path: '/packages/admin/in-progress/index', visible: false },
  { key: 'assist', name: '协助扫菲', icon: '📷', path: '/packages/admin/assist/index', visible: true },
  { key: 'batch-register', name: '批量代登', icon: '✍️', path: '/packages/admin/batch-register/index', visible: true }, // 竞品对齐 GAP-2（2026-11-04）
  { key: 'quality', name: '质检管理', icon: '🔬', path: '/packages/admin/quality/index', visible: false },
  { key: 'outsource', name: '外发管理', icon: '📤', path: '/packages/admin/outsource/index', visible: false },
  { key: 'handovers', name: '交接中心', icon: '🤝', path: '/packages/admin/handovers/index/index', visible: true },
  { key: 'process', name: '工序', icon: '⚙️', path: '/packages/admin/master/index?tab=processes', visible: true },
  { key: 'warehouse', name: '轻量仓库', icon: '📦', path: '/packages/admin/warehouse/index', visible: false },
  { key: 'material-issue', name: '按床领料', icon: '🧶', path: '/packages/admin/material-issue/index', visible: false },
  { key: 'suppliers', name: '供应商', icon: '🏭', path: '/packages/admin/suppliers/index', visible: false },
  { key: 'receipts', name: '暂收IQC', icon: '📥', path: '/packages/admin/receipts/index', visible: false },
  { key: 'shipment', name: '出货管理', icon: '🚚', path: '/packages/admin/shipments/index', visible: false },
  { key: 'cost', name: '成本管理', icon: '🧮', path: '/packages/admin/cost/index', visible: false },
  { key: 'quotation', name: '报价管理', icon: '📄', path: '/packages/admin/quote-list/index', visible: false },
  { key: 'receivable', name: '应收管理', icon: '💳', path: '/packages/admin/receivables/index', visible: false },
  { key: 'funds', name: '资金管理', icon: '🏦', path: '/packages/admin/funds/index', visible: false },
  { key: 'trace', name: '质量追溯', icon: '🔍', path: '/packages/admin/trace/index', visible: false },
  { key: 'print', name: '打印管理', icon: '🖨️', path: '/packages/admin/cloud-printer/index', visible: true },
  { key: 'logs', name: '日志管理', icon: '📋', path: '/packages/admin/logs/index', visible: true },
  { key: 'client-logs', name: '客户端日志', icon: '📟', path: '/packages/admin/client-logs/index', visible: true },
  { key: 'company-qr', name: '公司二维码', icon: '🔳', path: '/packages/admin/company-qr/index', visible: true }
];

const STAFF_MODULES = [
  { key: 'process', name: '工序', icon: '⚙️', path: '/packages/admin/master/index?tab=processes', visible: true },
  { key: 'full-production', name: '全流程生产', icon: '🔁', path: '/packages/admin/full-production/index', visible: false },
  { key: 'handovers', name: '交接中心', icon: '🤝', path: '/packages/admin/handovers/index/index', visible: true },
  { key: 'my-salary', name: '我的薪酬', icon: '💴', path: '/packages/salary/mine/index', visible: true },
  { key: 'in-progress', name: '我的登记', icon: '🧾', path: '/packages/admin/in-progress/index', visible: true }
];

/**
 * 宫格分组（按衣协同信息架构重划，2026-11-04，docs/功能重划分_衣协同对齐.md）：
 * 订单生产 → 车间计件 → 薪资对账 → 供应链 → 经营报表 → 协同档案 → 系统。
 * 纯分组壳：组内显隐仍走 MODULE_PERMS；全组无权限的组整组隐藏。
 */
const MODULE_GROUPS: Array<{ title: string; keys: string[] }> = [
  { title: '订单生产', keys: ['orders', 'quotation', 'production', 'full-production', 'in-progress', 'styles', 'tech-boms', 'tech-materials'] },
  { title: '车间计件', keys: ['batch-register', 'assist', 'quality', 'outsource', 'handovers'] },
  { title: '薪资对账', keys: ['salary', 'my-salary', 'order-salary', 'completion', 'receivable', 'funds'] },
  { title: '供应链', keys: ['warehouse', 'material-issue', 'receipts', 'suppliers', 'shipment'] },
  { title: '经营报表', keys: ['kanban', 'cost', 'trace'] },
  { title: '协同档案', keys: ['customers', 'employees', 'company-qr'] },
  { title: '系统', keys: ['print', 'logs', 'client-logs'] }
];

/**
 * 宫格模块 → 权限点映射（权限点体系二期）：每个模块挂一组点，OR 语义（任一通过即显示）。
 * key 与后端 apps/core/permissions.py PERMISSION_GROUPS 对齐：
 *   订单=orders.manage、款式=styles.manage、生产=production.manage、看板=production.manage|kanban.view|kanban.manage、
 *   追溯=trace.view、仓库=warehouse.manage、薪酬/完成汇总/订单薪酬=salary.view、
 *   我的薪酬=my_salary.view、员工=employees.manage、工作中=working.view、客户=customers.manage、
 *   协助扫菲=records.create|scan.assist、日志=logs.view、客户端日志=logs.client、
 *   指定工序=production.manage、公司二维码=company.qr、打印=print.manage、
 *   物料=tech.material、BOM=tech.bom（P1 tech 域，legacy=False）、
 *   按床领料=warehouse.manage（P1 领料定额化，差异报表为其子页）、
 *   成本管理=cost.view（重算/制费按钮在页内挂 cost.manage，P1 cost 域，legacy=False）、
 *   报价管理=quotation.view（建/改/删/流转在页内挂 quotation.manage，P2 报价域，legacy=False）、
 *   应收管理=finance.view（核销/撤销/作废/新建在页内挂 finance.manage，P2 应收域，legacy=False）、
 *   供应商=warehouse.supplier（P2 采购补全主档，独立管控，legacy=False）、
 *   暂收IQC=warehouse.manage（暂收/IQC/确认入库/需求汇总入口；新建暂收在详情页，同点）、
 *   出货管理=shipment.view（建单/审核/作废/装箱/打印在页内挂 shipment.manage，P2 出货域，legacy=False）。
 * 岗位（posts 页）不单列（在员工管理页内）。
 */
const MODULE_PERMS: Record<string, string[]> = {
  orders: ['orders.manage'],
  styles: ['styles.manage'],
  'tech-materials': ['tech.material'],
  'tech-boms': ['tech.bom'],
  production: ['production.manage'],
  'full-production': ['manufacturing.view'],
  kanban: ['production.manage', 'kanban.view', 'kanban.manage'],
  trace: ['trace.view'],
  warehouse: ['warehouse.manage'],
  'material-issue': ['warehouse.manage'],
  // P2 采购补全+出货域（legacy=False，2 段式）：
  // 供应商=warehouse.supplier（独立管控）、暂收/需求汇总=warehouse.manage、出货=shipment.view
  suppliers: ['warehouse.supplier'],
  receipts: ['warehouse.manage'],
  shipment: ['shipment.view'],
  cost: ['cost.view'],
  quotation: ['quotation.view'],
  receivable: ['finance.view'],
  funds: ['finance.view'],
  salary: ['salary.view'],
  'order-salary': ['salary.view'],
  completion: ['salary.view'],
  'my-salary': ['my_salary.view'],
  employees: ['employees.manage'],
  'in-progress': ['working.view'],
  customers: ['customers.manage'],
  assist: ['records.create', 'scan.assist'],
  'batch-register': ['scan.assist'], // GAP-2：批量代登 = 协助登记的批量形态，同点门控
  quality: ['quality.view'], // GAP-1：质检（页内动作挂 quality.manage，2 段式）
  outsource: ['outsource.view'], // GAP-5：外发（页内动作挂 outsource.manage，2 段式）
  handovers: ['handover.view', 'handover.receive', 'handover.create', 'handover.manage'],
  logs: ['logs.view'],
  'client-logs': ['logs.client'],
  'assigned-process': ['production.manage'],
  'company-qr': ['company.qr'],
  print: ['print.manage']
};

/** 全员基础模块：不参与权限过滤，不把任何人完全锁死（扫菲/通知/我的/AI问报在 tabBar，不经宫格） */
const ALWAYS_VISIBLE = ['my-salary', 'in-progress', 'process'];

type ModuleDef = { key: string; name: string; icon: string; path: string; visible: boolean };

/**
 * 按权限点过滤管理员宫格（显隐判定走 utils/perm.hasPerm：'*'/管理员/旧 coarse key 兜底口径一致）：
 * - 全员基础模块恒显示；
 * - 命中映射的模块按点 OR 判定；
 * - 未映射模块（暂无）：权限点 '*' 或管理员显示。
 */
function filterAdminModules(modules: ModuleDef[]): ModuleDef[] {
  return modules.map((m) => {
    if (ALWAYS_VISIBLE.indexOf(m.key) >= 0) return m;
    const keys = MODULE_PERMS[m.key];
    const allowed = keys ? hasAnyPerm(keys) : hasPerm('*');
    return { ...m, visible: m.visible && allowed };
  });
}

/** 组标题 + 该组过滤后的模块（全组无权限 → 丢弃该组） */
function buildGroups(filtered: ModuleDef[]): Array<{ title: string; modules: ModuleDef[] }> {
  const byKey: Record<string, ModuleDef> = {};
  filtered.forEach((m) => { byKey[m.key] = m; });
  const groups: Array<{ title: string; modules: ModuleDef[] }> = [];
  MODULE_GROUPS.forEach((g) => {
    const mods = g.keys.map((k) => byKey[k]).filter(
      (m): m is ModuleDef => !!m && m.visible !== false);
    if (mods.length) groups.push({ title: g.title, modules: mods });
  });
  return groups;
}

Page({
  data: {
    modules: [] as Array<{ key: string; name: string; icon: string; path: string; visible: boolean }>,
    groups: [] as Array<{ title: string; modules: ModuleDef[] }>,
    startDate: '',
    endDate: '',
    quick: 'month',
    isAdmin: false,
    // ---- 常驻两块数字（§2.8 P0：工作台「今日件数 / 今日预估工资」） ----
    todayQty: 0,          // 本人今日登记件数（my_today 求和；本人口径不受"隐藏员工工资"开关影响）
    todayAmount: '0.00'   // 本人今日预估工资（Σqty×price，后端字符串防浮点误差）
  },

  onShow() {
    if (!requireLogin()) return;
    const tabBar = (this as unknown as { getTabBar?: () => { setSelected: (n: number) => void } }).getTabBar?.();
    if (tabBar) tabBar.setSelected(0);

    // 骨架期 isAdmin 可能恒 false（由登录/切公司流程驱动），允许 fallback
    const app = getApp<IAppOption>();
    const isAdmin = !!app.globalData.isAdmin;
    const r = monthRange();
    // 管理员宫格按权限点过滤后按衣协同骨架分组；员工单组不落标题
    const modules = isAdmin
      ? filterAdminModules(ADMIN_MODULES)
      : filterAdminModules(STAFF_MODULES);
    this.setData({
      isAdmin,
      modules,
      groups: isAdmin ? buildGroups(modules) : [],
      startDate: this.data.startDate || r.start,
      endDate: this.data.endDate || r.end
    });
    // 常驻两块数字：失败静默（保持上次值/0），不阻塞宫格渲染
    this.loadToday();
    // 冷启动/岗位变更自愈：校正角色上下文，宫格渲染变化时重刷（不递归 onShow）
    refreshCompanyContext().then((cur) => {
      if (!cur) return;
      const g = getApp<IAppOption>().globalData;
      const nowAdmin = !!g.isAdmin;
      if (nowAdmin !== this.data.isAdmin) {
        const mods = nowAdmin
          ? filterAdminModules(ADMIN_MODULES)
          : filterAdminModules(STAFF_MODULES);
        this.setData({ isAdmin: nowAdmin, modules: mods, groups: nowAdmin ? buildGroups(mods) : [] });
      }
    });
  },

  /**
   * 常驻两块数字（§2.8 P0）：GET /records/workbench。
   * - 今日件数 = my_today（本人今日登记）qty 求和（后端 my_today 已按登录人过滤）；
   * - 今日预估工资 = 后端新字段 my_today_amount（SA3 开发中）；字段缺失时显示 0.00。
   * TODO(SA3 落地后)：直接消费 my_today_amount，不再依赖前端求和兜底口径。
   */
  loadToday() {
    fetchWorkbench({})
      .then((wb) => {
        const rows = (wb.my_today || []) as Array<{ qty?: number }>;
        const qty = rows.reduce((s, r) => s + (Number(r.qty) || 0), 0);
        const raw = wb as unknown as { my_today_amount?: string };
        this.setData({
          todayQty: qty,
          // SA3 的 my_today_amount 未上线时字段缺失 → 显示 0（不猜金额，金额必须后端口径）
          todayAmount: raw.my_today_amount || '0.00'
        });
      })
      .catch(() => undefined);
  },

  /** 日期条/日历变更：统计板块已移除，仅记录选择结果 */
  onRangeChange(e: WechatMiniprogram.CustomEvent<{ start: string; end: string; quick: string }>) {
    this.setData({
      startDate: e.detail.start,
      endDate: e.detail.end,
      quick: e.detail.quick || ''
    });
  }
});
