/** 公司信息页：编号展示（可复制供员工加入）、名称/城市/容量、成员数；管理员可切换版本套餐。 */
import { requireLogin, getIsAdmin } from '../../../utils/auth';
import { fetchMyCompanies, setCompanyPlan } from '../../../services/auth';
import { CompanyItem } from '../../../services/types';
import { currentCompanyId } from '../../../utils/format';

/** 与后端 Company.PLAN_CAPACITY 保持一致 */
const PLANS = [
  { key: 'trial', label: '体验版', capacity: 20 },
  { key: 'basic', label: '基础版', capacity: 50 },
  { key: 'standard', label: '标准版', capacity: 150 },
  { key: 'flagship', label: '旗舰版', capacity: 500 }
];

Page({
  data: {
    loading: true,
    isAdmin: false,
    company: null as CompanyItem | null,
    createdDate: '',
    capacityPercent: 0
  },

  onShow() {
    if (!requireLogin()) return;
    this.setData({ isAdmin: getIsAdmin() });
    this.load();
  },

  onPullDownRefresh() {
    this.load().then(() => wx.stopPullDownRefresh());
  },

  load(): Promise<void> {
    this.setData({ loading: true });
    return fetchMyCompanies()
      .then((list) => {
        const app = getApp<IAppOption>();
        app.globalData.companies = list;
        const cid = currentCompanyId();
        const current =
          list.find((c) => c.id === cid && c.status === 1) ||
          list.find((c) => c.status === 1) ||
          null;
        const capacity = current ? current.capacity || 0 : 0;
        const memberCount = current ? current.member_count || 0 : 0;
        this.setData({
          loading: false,
          company: current,
          createdDate: current ? (current.created_at || '').slice(0, 10) : '',
          capacityPercent: capacity > 0 ? Math.min(100, Math.round((memberCount / capacity) * 100)) : 0
        });
      })
      .catch(() => this.setData({ loading: false, company: null }));
  },

  onCopyNo() {
    const c = this.data.company;
    if (!c) return;
    wx.setClipboardData({
      data: c.company_no,
      success: () => wx.showToast({ title: '公司编号已复制', icon: 'success' })
    });
  },

  // ============ 版本套餐（管理员；容量随套餐变更） ============

  onChoosePlan() {
    const c = this.data.company;
    if (!c || !this.data.isAdmin) return;
    wx.showActionSheet({
      itemList: PLANS.map((p) => `${p.label}（容量 ${p.capacity} 人）`),
      success: (res) => {
        const plan = PLANS[res.tapIndex];
        if (!plan || plan.key === c.plan) return;
        wx.showLoading({ title: '变更中…', mask: true });
        setCompanyPlan(plan.key)
          .then((r) => {
            wx.hideLoading();
            wx.showToast({ title: `已切换${r.plan_display}`, icon: 'success' });
            this.load();
          })
          .catch(() => wx.hideLoading()); // 错误 toast 已由请求层弹出
      }
    });
  }
});
