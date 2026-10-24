/**
 * 权限点工具（权限点体系二期，前端部分）：
 * 页面元素按当前用户权限点显隐。数据源为当前公司岗位的权限点集合
 * （登录/切公司时由 services/auth.ts applyCompanyContext 写入 globalData.permissions，
 * 并持久化到 storage 供冷启动恢复）。
 *
 * 口径与后端 apps/core/permissions.py 对齐：
 * - is_admin 岗位 / '*' → 恒有全部权限；
 * - 细粒度点 = '<module>.<action>'（如 records.revoke），直接命中；
 * - 旧 coarse key（styles/customers/production/working/my_salary/'*'）覆盖该模块全部
 *   legacy=True 的点（_LEGACY_COVERED），高危点（删除/撤销/结算等 legacy=False）不被覆盖。
 */
import { getIsAdmin, getPermissions } from './auth';

/** 旧 coarse key → 覆盖的细粒度点集合（与后端 _LEGACY_COVERED 逐一对应，勿单方面增删） */
const LEGACY_COVERED: Record<string, string[]> = {
  styles: ['styles.manage', 'styles.price'],
  customers: ['customers.manage'],
  // 旧 'production' 历史上治理 扫菲登记/编菲/追溯/仓库（records.create 等为跨组 legacy 点）
  production: ['records.create', 'production.manage', 'trace.view', 'warehouse.manage'],
  working: ['working.view'],
  my_salary: ['my_salary.view']
};

/** 当前用户权限点集合：globalData 优先（内存最新），storage 兜底（冷启动 hydrate 前） */
export function getMyPerms(): string[] {
  try {
    const app = getApp<IAppOption>();
    const perms = app && app.globalData ? app.globalData.permissions : null;
    if (perms && perms.length) return perms;
  } catch (e) { /* App 未就绪时走 storage */ }
  return getPermissions() || [];
}

/** 当前公司是否管理员（等价于旧 getIsAdmin，含 globalData 优先） */
export function isAdmin(): boolean {
  try {
    const app = getApp<IAppOption>();
    if (app && app.globalData && app.globalData.isAdmin) return true;
  } catch (e) { /* fallthrough */ }
  return getIsAdmin();
}

/**
 * 是否拥有某权限点。判定顺序：
 * 1. 管理员 → true；
 * 2. 点集为空 → true（未初始化/历史登录回退全量，与一期宫格口径一致，避免把人锁死；
 *    越权操作由后端 403 + http 层 toast 兜底）；
 * 3. 含 '*' 或直接含 key → true（effective_perms 下发后普通岗位即全部细粒度点）；
 * 4. 降级口径：effective_perms 缺失、岗位仍持有旧 coarse key 时，按 LEGACY_COVERED
 *    推导（与后端 _LEGACY_COVERED 一致）；其余 coarse key 无覆盖关系，直接同名单点判断已由第 3 步覆盖。
 * 5. 都不命中 → false。
 */
export function hasPerm(key: string): boolean {
  if (isAdmin()) return true;
  const perms = getMyPerms();
  if (!perms.length) return true;
  if (perms.indexOf('*') >= 0 || perms.indexOf(key) >= 0) return true;
  const legacyKeys = Object.keys(LEGACY_COVERED);
  for (let i = 0; i < legacyKeys.length; i++) {
    const covered = LEGACY_COVERED[legacyKeys[i]];
    if (perms.indexOf(legacyKeys[i]) >= 0 && covered.indexOf(key) >= 0) {
      return true;
    }
  }
  return false;
}

/** OR 语义：任一权限点通过即 true（首页宫格模块挂一组点用） */
export function hasAnyPerm(keys: string[]): boolean {
  for (let i = 0; i < keys.length; i++) {
    if (hasPerm(keys[i])) return true;
  }
  return false;
}
