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
  { key: 'kanban', name: '生产看板', icon: '📊', path: '/packages/admin/kanban/index', visible: true },
  { key: 'salary', name: '薪酬管理', icon: '💰', path: '/packages/salary/company/index', visible: true },
  { key: 'my-salary', name: '我的薪酬', icon: '💴', path: '/packages/salary/mine/index', visible: true },
  { key: 'order-salary', name: '订单薪酬', icon: '🧾', path: '/packages/salary/order/index', visible: true },
  { key: 'completion', name: '完成汇总', icon: '📊', path: '/packages/salary/completion/index', visible: true },
  { key: 'styles', name: '款式管理', icon: '👗', path: '/packages/admin/styles/index', visible: true },
  { key: 'customers', name: '客户管理', icon: '🤝', path: '/packages/admin/customers/index', visible: true },
  { key: 'employees', name: '员工管理', icon: '👥', path: '/packages/admin/employees/index', visible: true },
  { key: 'in-progress', name: '工作中', icon: '⏳', path: '/packages/admin/in-progress/index', visible: true },
  { key: 'assist', name: '协助扫菲', icon: '📷', path: '/packages/admin/assist/index', visible: true },
  { key: 'warehouse', name: '轻量仓库', icon: '📦', path: '/packages/admin/warehouse/index', visible: true },
  { key: 'trace', name: '质量追溯', icon: '🔍', path: '/packages/admin/trace/index', visible: false }, // 2026-10-22 用户：质量追溯功能先去掉（改 visible 即可恢复）
  { key: 'print', name: '打印管理', icon: '🖨️', path: '/packages/admin/cloud-printer/index', visible: true },
  { key: 'assigned-process', name: '指定工序', icon: '🛠️', path: '/packages/admin/assigned/index', visible: true },
  { key: 'logs', name: '日志管理', icon: '📋', path: '/packages/admin/logs/index', visible: true },
  { key: 'client-logs', name: '客户端日志', icon: '📟', path: '/packages/admin/client-logs/index', visible: true },
  { key: 'company-qr', name: '公司二维码', icon: '🔳', path: '/packages/admin/company-qr/index', visible: true }
];

const STAFF_MODULES = [
  { key: 'my-salary', name: '我的薪酬', icon: '💴', path: '/packages/salary/mine/index', visible: true },
  { key: 'in-progress', name: '工作中', icon: '⏳', path: '/packages/admin/in-progress/index', visible: true }
];

/**
 * 宫格模块 → 权限点映射（权限点体系二期）：每个模块挂一组点，OR 语义（任一通过即显示）。
 * key 与后端 apps/core/permissions.py PERMISSION_GROUPS 对齐：
 *   订单=orders.manage、款式=styles.manage、生产=production.manage、看板=production.manage|kanban.view|kanban.manage、
 *   追溯=trace.view、仓库=warehouse.manage、薪酬/完成汇总/订单薪酬=salary.view、
 *   我的薪酬=my_salary.view、员工=employees.manage、工作中=working.view、客户=customers.manage、
 *   协助扫菲=records.create|scan.assist、日志=logs.view、客户端日志=logs.client、
 *   指定工序=production.manage、公司二维码=company.qr、打印=print.manage。
 * 岗位（posts 页）不单列（在员工管理页内）。
 */
const MODULE_PERMS: Record<string, string[]> = {
  orders: ['orders.manage'],
  styles: ['styles.manage'],
  production: ['production.manage'],
  kanban: ['production.manage', 'kanban.view', 'kanban.manage'],
  trace: ['trace.view'],
  warehouse: ['warehouse.manage'],
  salary: ['salary.view'],
  'order-salary': ['salary.view'],
  completion: ['salary.view'],
  'my-salary': ['my_salary.view'],
  employees: ['employees.manage'],
  'in-progress': ['working.view'],
  customers: ['customers.manage'],
  assist: ['records.create', 'scan.assist'],
  logs: ['logs.view'],
  'client-logs': ['logs.client'],
  'assigned-process': ['production.manage'],
  'company-qr': ['company.qr'],
  print: ['print.manage']
};

/** 全员基础模块：不参与权限过滤，不把任何人完全锁死（扫菲/通知/我的在 tabBar，不经宫格） */
const ALWAYS_VISIBLE = ['my-salary', 'in-progress'];

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

Page({
  data: {
    modules: [] as Array<{ key: string; name: string; icon: string; path: string; visible: boolean }>,
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
    // 管理员宫格按权限点过滤（hasPerm 内含未初始化回退全量口径）；员工 STAFF_MODULES 不变
    const modules = isAdmin
      ? filterAdminModules(ADMIN_MODULES)
      : STAFF_MODULES;
    this.setData({
      isAdmin,
      modules,
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
          : STAFF_MODULES;
        this.setData({ isAdmin: nowAdmin, modules: mods });
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
