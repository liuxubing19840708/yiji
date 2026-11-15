/**
 * 全局类型：与 app.ts 的 globalData 保持一致。
 * 业务模型类型统一放 services/types.ts，不要放这里。
 */

interface IAppOption {
  globalData: {
    token: string;
    refresh: string;
    user: import('./utils/auth').StoredUser | null;
    /** 我的公司列表（GET /companies/mine，CompanyItem[]） */
    companies: import('./services/types').CompanyItem[];
    /** 当前公司 id（X-Company-Id 的来源），0 = 未选择 */
    companyId: number;
    /** 当前公司权限点集合 */
    permissions: string[];
    /** 当前公司是否管理员 */
    isAdmin: boolean;
  };
}

/** tabBar 页面索引（custom-tab-bar setSelected 用；AI问报=中央 FAB，无常规索引） */
type TabKey = 'index' | 'scan' | 'notifications' | 'profile';
