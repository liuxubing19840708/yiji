/**
 * 小程序入口：登录态、当前公司、权限的内存缓存。
 *
 * 约定：页面里不要直接读 storage，统一走 app.globalData / utils/auth.ts。
 */
import { flushOfflineQueue } from './utils/offline-queue';
import { getToken, getUser, getCompanyId, getIsAdmin, getPermissions, isLoggedIn } from './utils/auth';
import { flushLogs, logger } from './utils/logger';

App({
  globalData: {
    token: '',
    refresh: '',
    user: null,
    companies: [],
    companyId: 0,
    permissions: [],
    isAdmin: false
  } as IAppOption['globalData'],

  onLaunch() {
    this.hydrate();
    // 启动时补发离线队列中失败的登记（车间弱网容错，见 docs/CONVENTIONS.md §离线容错）
    flushOfflineQueue();
    // 补传上次会话遗留的待上报日志（此时已带登录态，厂→用户隔离落款正确）
    flushLogs();
  },

  onShow() {
    this.hydrate();
    flushOfflineQueue();
    flushLogs();
  },

  /** 全局 JS 异常 → 实时日志 + 服务端（厂→用户隔离） */
  onError(msg: string) {
    logger.error('JS异常', msg);
    flushLogs();
  },

  /** 未处理的 Promise 拒绝 */
  onUnhandledRejection(res: { reason?: unknown }) {
    const reason = res && res.reason;
    logger.error('未处理Promise拒绝', reason instanceof Error ? (reason.stack || reason.message) : String(reason ?? ''));
    flushLogs();
  },

  /** 页面路由不存在 */
  onPageNotFound(res: { path: string }) {
    logger.warn('页面不存在', res && res.path);
  },

  /** storage → globalData 同步（登录/切公司后也会调用） */
  hydrate() {
    const g = this.globalData;
    g.token = getToken();
    g.refresh = '';
    g.user = getUser();
    g.companyId = getCompanyId();
    // 冷启动（token 仍在）也要恢复角色上下文，否则首页恒按员工分支渲染
    g.isAdmin = getIsAdmin();
    g.permissions = getPermissions();
  },

  /** 是否已登录（供页面守卫快速判断） */
  isLogin(): boolean {
    return isLoggedIn();
  }
});
