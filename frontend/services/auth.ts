/**
 * 登录与公司接口（accounts）。
 * 端点以后端 apps/accounts/urls.py 为准。
 */
import { http } from '../utils/request';
import { setAuth, setCompanyId, getCompanyId, setUser, getUser, setCompanyContext, StoredUser } from '../utils/auth';
import { logger, flushLogs } from '../utils/logger';
import { TokenPair, CompanyItem, CompanyPostSummary, User, Employee, Post, Paginated, PermissionGroup } from './types';

/** 手机号 + 密码登录 */
export function passwordLogin(phone: string, password: string): Promise<TokenPair> {
  return http.post<TokenPair>('/auth/login', { phone, password }).then(saveSession);
}

/** 发送短信验证码 */
export function sendSmsCode(phone: string): Promise<{ message: string }> {
  return http.post('/auth/sms-code', { phone });
}

/** 验证码登录（无账号自动注册） */
export function smsLogin(phone: string, code: string): Promise<TokenPair> {
  return http.post<TokenPair>('/auth/login-sms', { phone, code }).then(saveSession);
}

/** 微信 code 登录 */
export function wechatLogin(code: string): Promise<TokenPair> {
  return http.post<TokenPair>('/auth/wechat-login', { code }).then(saveSession);
}

/** 登录成功统一处理：写 storage + globalData */
function saveSession(token: TokenPair): TokenPair {
  setAuth(token.access, token.refresh, token.user as StoredUser);
  const app = getApp<IAppOption>();
  app.globalData.token = token.access;
  app.globalData.user = token.user;
  // 关键动线埋点：登录成功（含此前未登录期的遗留日志，此刻起可按厂落款上报）
  logger.info('登录成功', (token.user as StoredUser)?.phone || '');
  flushLogs();
  return token;
}

/** 当前用户信息 */
export function fetchMe(): Promise<User> {
  return http.get<User>('/auth/me');
}

/** 更新个人资料（PUT /auth/me：real_name/gender/birthday；wx.request 无 PATCH） */
export function updateProfile(data: { real_name?: string; gender?: number; birthday?: string }): Promise<User> {
  return http.put<User>('/auth/me', data as Record<string, unknown>);
}

/** 创建公司（创建人自动成为在职管理员） */
export function createCompany(name: string, city?: string): Promise<{ message: string; company: CompanyItem }> {
  return http.post('/companies/create', { name, city: city || '' });
}

/** 修改密码 */
export function changePassword(oldPassword: string, newPassword: string): Promise<{ message: string }> {
  return http.put('/auth/password', { old_password: oldPassword, new_password: newPassword });
}

// ============ 公司 ============

/** 我的公司列表（含各公司内我的状态/岗位） */
export function fetchMyCompanies(): Promise<CompanyItem[]> {
  return http.get<CompanyItem[]>('/companies/mine');
}

/** 变更版本套餐（管理员）：容量随套餐同步（体验20/基础50/标准150/旗舰500） */
export function setCompanyPlan(plan: string): Promise<{ message: string; plan: string; plan_display: string; capacity: number }> {
  return http.put('/companies/plan', { plan });
}

/** 通过公司编号加入公司 */
export function joinCompany(companyNo: string): Promise<{ message: string }> {
  return http.post('/companies/join', { company_no: companyNo });
}

/** 把某公司的岗位上下文写入 globalData + storage（switchCompany / refreshCompanyContext 共用） */
function applyCompanyContext(companyId: number, company: CompanyItem): void {
  const app = getApp<IAppOption>();
  app.globalData.companyId = companyId;
  const post = (company && company.post) || null;
  const isAdmin = !!post && !!post.is_admin;
  app.globalData.isAdmin = isAdmin;
  // 权限点体系二期：优先 effective_perms（'*' 岗位=['*']，普通岗位=解析后的全部细粒度点）；
  // 后端该字段可能尚未上线 → 缺省时回退 permissions（可能是旧 coarse key，utils/perm.ts 兜底推导）
  const postCtx = (post as (CompanyPostSummary & { effective_perms?: string[]; permissions?: string[] }) | null) || null;
  const perms = postCtx ? (postCtx.effective_perms || postCtx.permissions || []) : [];
  app.globalData.permissions = perms;
  // 持久化：冷启动 hydrate 恢复（否则重开小程序 isAdmin 恒 false，只看到员工模块）
  setCompanyContext(isAdmin, perms);
}

/** 切换当前公司：写入 storage/globalData 的 companyId，并按岗位派生 isAdmin */
export function switchCompany(companyId: number): Promise<{ message: string; company: CompanyItem }> {
  return http.post<{ message: string; company: CompanyItem }>(`/companies/${companyId}/switch`, {}).then((res) => {
    setCompanyId(companyId);
    applyCompanyContext(companyId, res.company);
    const app = getApp<IAppOption>();
    // 密码登录历史响应无 user 时兜底补拉（后端已修复返回 user）
    if (!getUser()) {
      fetchMe().then((user) => {
        setUser(user as StoredUser);
        app.globalData.user = user;
      }).catch(() => { /* 个人资料缺失不阻断进入 */ });
    }
    return res;
  });
}

/**
 * 冷启动/岗位变更自愈：按当前 companyId 拉 /companies/mine 校正角色上下文。
 * 失败静默（离线时沿用 storage 里的持久化值）。
 */
export function refreshCompanyContext(): Promise<CompanyItem | null> {
  const cid = getCompanyId();
  if (!cid) return Promise.resolve(null);
  return fetchMyCompanies().then((list) => {
    const app = getApp<IAppOption>();
    app.globalData.companies = list;
    const cur = list.find((c) => c.id === cid && c.status === 1) || null;
    if (cur) applyCompanyContext(cid, cur);
    return cur;
  }).catch(() => null);
}

// ============ 员工管理（管理员） ============

/** 员工列表：tab=all（在职，默认）|pending（待审核）|resigned（离职），keyword 服务端搜索；
 *  postId 岗位筛选（0 = 未设置岗位） */
export function fetchEmployees(
  tab?: 'all' | 'pending' | 'resigned',
  keyword?: string,
  postId?: number
): Promise<Employee[]> {
  const params: Record<string, unknown> = {};
  if (tab && tab !== 'all') {
    params.tab = tab;
  }
  if (keyword) {
    params.keyword = keyword;
  }
  if (typeof postId === 'number') {
    params.post = postId;
  }
  return http.get<Employee[]>('/employees', Object.keys(params).length ? params : undefined);
}

export function approveEmployee(id: number): Promise<Employee> {
  return http.post<Employee>(`/employees/${id}/approve`);
}

export function rejectEmployee(id: number): Promise<Employee> {
  return http.post<Employee>(`/employees/${id}/reject`);
}

/** 办理离职：有未结算薪酬时后端返回 {need_confirm:true}，确认后传 force=1 */
export function resignEmployee(id: number, force = false): Promise<Employee | { message: string; need_confirm: true }> {
  return http.post<Employee>(`/employees/${id}/resign`, force ? { force: 1 } : undefined);
}

export function rehireEmployee(id: number): Promise<Employee> {
  return http.post<Employee>(`/employees/${id}/rehire`);
}

export function setEmployeePost(id: number, postId: number): Promise<Employee> {
  return http.post<Employee>(`/employees/${id}/set-post`, { post_id: postId });
}

/** 设置组编号（1组/2组…）：groupNo 必填正整数（非空字段） */
export function setEmployeeGroup(id: number, groupNo: number): Promise<Employee> {
  return http.post<Employee>(`/employees/${id}/set-group`, { group_no: groupNo });
}

export function pinEmployee(id: number, isPinned: boolean): Promise<{ id: number; is_pinned: boolean }> {
  return http.put<{ id: number; is_pinned: boolean }>(`/employees/${id}/pin`, { is_pinned: isPinned });
}

// ============ 岗位 ============

/** 岗位列表（后端分页，解包为数组） */
export function fetchPosts(): Promise<Post[]> {
  return http.get<Paginated<Post>>('/posts', { page_size: 100 })
    .then((data) => (Array.isArray(data) ? data : data.results || []));
}

export function createPost(name: string, permissions: string[]): Promise<Post> {
  return http.post<Post>('/posts', { name, permissions });
}

export function updatePost(id: number, name: string, permissions: string[]): Promise<Post> {
  return http.put<Post>(`/posts/${id}`, { name, permissions });
}

export function deletePost(id: number): Promise<unknown> {
  return http.del(`/posts/${id}`);
}

/** 复制岗位（名称自动加「副本」后缀） */
export function copyPost(id: number): Promise<Post> {
  return http.post<Post>(`/posts/${id}/copy`);
}

/** 权限点字典（按模块分组，岗位编辑弹窗渲染用） */
export function fetchPermissionGroups(): Promise<PermissionGroup[]> {
  return http.get<{ groups: PermissionGroup[] }>('/permissions')
    .then((d) => (d && d.groups) || []);
}

/** 便捷：更新本地用户资料（个人中心） */
export function refreshMe(): Promise<User> {
  return fetchMe().then((user) => {
    setUser(user as StoredUser);
    const app = getApp<IAppOption>();
    app.globalData.user = user;
    return user;
  });
}
