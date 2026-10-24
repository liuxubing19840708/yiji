/**
 * 登录态存取：storage 的唯一读写入口。
 * 令牌形状见 services/auth.ts —— 后端返回 { access, refresh, user }。
 */
const KEY_TOKEN = 'yisao_token';
const KEY_REFRESH = 'yisao_refresh';
const KEY_USER = 'yisao_user';
const KEY_COMPANY_ID = 'yisao_company_id';
const KEY_IS_ADMIN = 'yisao_is_admin';
const KEY_PERMISSIONS = 'yisao_permissions';

export interface StoredUser {
  id: number;
  phone?: string | null;
  username?: string;
  real_name?: string;
  avatar?: string;
  gender?: number;
  birthday?: string | null;
}

export function getToken(): string {
  return wx.getStorageSync(KEY_TOKEN) || '';
}

export function getRefresh(): string {
  return wx.getStorageSync(KEY_REFRESH) || '';
}

export function getUser(): StoredUser | null {
  return wx.getStorageSync(KEY_USER) || null;
}

export function getCompanyId(): number {
  return wx.getStorageSync(KEY_COMPANY_ID) || 0;
}

export function setAuth(access: string, refresh: string, user: StoredUser): void {
  wx.setStorageSync(KEY_TOKEN, access);
  wx.setStorageSync(KEY_REFRESH, refresh);
  wx.setStorageSync(KEY_USER, user);
}

export function setUser(user: StoredUser): void {
  wx.setStorageSync(KEY_USER, user);
}

export function setCompanyId(id: number): void {
  wx.setStorageSync(KEY_COMPANY_ID, id);
}

/** 当前公司内我的角色上下文（管理员标志 + 岗位权限点），冷启动恢复用 */
export function setCompanyContext(isAdmin: boolean, permissions: string[]): void {
  wx.setStorageSync(KEY_IS_ADMIN, isAdmin ? 1 : 0);
  wx.setStorageSync(KEY_PERMISSIONS, permissions || []);
}

export function getIsAdmin(): boolean {
  return !!wx.getStorageSync(KEY_IS_ADMIN);
}

export function getPermissions(): string[] {
  return wx.getStorageSync(KEY_PERMISSIONS) || [];
}

export function isLoggedIn(): boolean {
  return !!getToken();
}

export function clearAuth(): void {
  wx.removeStorageSync(KEY_TOKEN);
  wx.removeStorageSync(KEY_REFRESH);
  wx.removeStorageSync(KEY_USER);
  wx.removeStorageSync(KEY_COMPANY_ID);
  wx.removeStorageSync(KEY_IS_ADMIN);
  wx.removeStorageSync(KEY_PERMISSIONS);
}

/**
 * 登录守卫：未登录跳登录页并返回 false。
 * 页面 onShow 里调用：if (!requireLogin()) return;
 */
export function requireLogin(): boolean {
  if (isLoggedIn()) {
    return true;
  }
  wx.navigateTo({ url: '/pages/login/index' });
  return false;
}

/** 同步 globalData 与 storage（供 app.ts / 页面共用）。 */
export function syncGlobalData(): void {
  const app = getApp<IAppOption & { globalData: Record<string, unknown> }>();
  if (!app) {
    return;
  }
  app.globalData.token = getToken();
  app.globalData.refresh = getRefresh();
  app.globalData.user = getUser();
  app.globalData.companyId = getCompanyId();
  // 冷启动（token 仍在）时也要恢复角色上下文，否则首页恒按员工分支渲染
  app.globalData.isAdmin = getIsAdmin();
  app.globalData.permissions = getPermissions();
}
