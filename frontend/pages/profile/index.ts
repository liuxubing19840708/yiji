/**
 * 我的（规格 §16.2）：头像/姓名/手机号/当前公司与岗位 + 系统版本号；
 * 切换公司（fetchMyCompanies → switchCompany → reLaunch 首页重拉数据）、
 * 加入公司（joinCompany 输公司编号）、资料编辑/修改密码（跳 Agent A 的 profile-edit）、
 * 隐私政策/操作手册（暂 showModal 文案占位，待替换 web-view 打开 H5）、
 * 注销账号（POST /auth/deactivate 双重确认，规格 §16.2/规则⑧）、
 * 退出登录（clearAuth + reLaunch 登录页）、订阅消息引导（模板 id TODO）。
 */
import { requireLogin, getUser, clearAuth } from '../../utils/auth';
import { hasPerm } from '../../utils/perm';
import { refreshMe, fetchMyCompanies, joinCompany, switchCompany, fetchEmployees } from '../../services/auth';
import { fetchSettleApplies } from '../../services/salary';
import { http, ApiError } from '../../utils/request';
import { CompanyItem } from '../../services/types';

/** 待我处理行 */
interface TodoRow {
  key: string;
  label: string;
  count: number;
  url: string;
}

/** 系统版本号（§16.2：V: x.x.xx；发版时同步更新） */
const APP_VERSION = 'V 1.0.0';

interface MenuAction {
  key: string;
  label: string;
  desc: string;
  danger?: boolean;
}

const MENU_ACTIONS: MenuAction[] = [
  { key: 'switch', label: '切换公司', desc: '选择在职公司并切换上下文' },
  { key: 'join', label: '加入公司', desc: '输入公司编号申请加入' },
  { key: 'edit', label: '个人资料编辑', desc: '头像/姓名/性别/出生日期' },
  { key: 'password', label: '修改密码', desc: '' },
  { key: 'help', label: '使用帮助', desc: '老板/裁床/车位 三分钟上手指引' },
  { key: 'privacy', label: '隐私政策', desc: '' },
  { key: 'manual', label: '操作手册', desc: '' },
  { key: 'subscribe', label: '订阅消息设置', desc: '审核/结算等重要事件提醒' },
  { key: 'logout', label: '退出登录', desc: '', danger: true },
  { key: 'deactivate', label: '注销账号', desc: '删除账号，不可恢复', danger: true }
];

/** 员工专属菜单项（痛点 #3 变更透明，切片 §22.3）：插在「修改密码」之后 */
const STAFF_MENU: MenuAction = {
  key: 'changes',
  label: '与我相关的变更',
  desc: '改价/代登/撤销等涉及我的记录'
};

/** 管理员专属菜单项：设置（打印方式/自动裁完等全局开关，员工改不了） */
const ADMIN_MENU: MenuAction = {
  key: 'settings',
  label: '设置',
  desc: '打印方式、自动裁完等全局开关'
};

/** 员工菜单 = 通用菜单 + 与我相关的变更；管理员菜单 = 通用菜单 + 设置。
 *  权限点体系二期：管理员专属项按点显隐（设置 → settings.manage），员工项（变更记录）保留 */
function buildMenu(isAdmin: boolean): MenuAction[] {
  const list = MENU_ACTIONS.slice();
  const at = list.findIndex((m) => m.key === 'password');
  const insertAt = at >= 0 ? at + 1 : list.length;
  if (isAdmin) {
    // 设置仅持 settings.manage 点（或 '*'）可见；hasPerm 对未初始化权限回退全量
    if (hasPerm('settings.manage')) {
      list.splice(insertAt, 0, ADMIN_MENU);
    }
  } else {
    list.splice(insertAt, 0, STAFF_MENU);
  }
  return list;
}

// TODO(Wave 2): 替换为微信公众平台申请的订阅消息模板 id（审核结果/结算通知等）
const SUBSCRIBE_TEMPLATE_IDS: string[] = [];

/** tabBar 实例方法窄化 */
interface TabBarLike {
  setSelected?: (n: number) => void;
}

Page({
  data: {
    userName: '',
    avatar: '',
    avatarText: '',
    phone: '',
    companyName: '',
    postName: '',
    isAdmin: false,
    appVersion: APP_VERSION,
    menu: MENU_ACTIONS,
    /** 待我处理（2026-10-21 用户需求）：按角色拉待办计数，空则整卡隐藏 */
    todos: [] as TodoRow[]
  },

  onShow() {
    if (!requireLogin()) return;
    const tab = (this.getTabBar() as unknown) as TabBarLike | undefined;
    if (tab && tab.setSelected) {
      tab.setSelected(4);
    }
    this.renderLocal();
    // 拉最新资料与公司列表（失败静默，展示本地缓存）
    refreshMe().then(() => this.renderLocal()).catch(() => undefined);
    fetchMyCompanies()
      .then((list: CompanyItem[]) => {
        const app = getApp<IAppOption>();
        app.globalData.companies = list;
        this.renderCompany(list);
      })
      .catch(() => undefined);
    this.loadTodos();
  },

  /** 待我处理：员工加入审核（employees.manage）/ 结算审核（salary.review）/
   *  结算发放（salary.settle）——只显示计数 > 0 的项；员工角色无待办则不显示卡片 */
  loadTodos() {
    const jobs: Array<Promise<TodoRow | null>> = [];
    if (hasPerm('employees.manage')) {
      jobs.push(
        fetchEmployees('pending')
          .then((list) => ({ key: 'join', label: '员工加入审核', count: (list || []).length, url: '/packages/admin/employees/index?tab=pending' }))
          .catch(() => null)
      );
    }
    if (hasPerm('salary.review')) {
      jobs.push(
        fetchSettleApplies({ status: 0, page: 1, page_size: 1 })
          .then((res) => ({ key: 'review', label: '工资结算审核', count: res.count || 0, url: '/packages/salary/company/index' }))
          .catch(() => null)
      );
    }
    if (hasPerm('salary.settle')) {
      jobs.push(
        fetchSettleApplies({ status: 1, page: 1, page_size: 1 })
          .then((res) => ({ key: 'pay', label: '工资结算发放', count: res.count || 0, url: '/packages/salary/company/index' }))
          .catch(() => null)
      );
    }
    if (!jobs.length) {
      this.setData({ todos: [] });
      return;
    }
    Promise.all(jobs).then((rows) => {
      this.setData({ todos: rows.filter((r): r is TodoRow => !!r && r.count > 0) });
    });
  },

  onTodoTap(e: WechatMiniprogram.TouchEvent) {
    const url = (e.currentTarget.dataset as { url?: string }).url || '';
    if (url) wx.navigateTo({ url });
  },

  renderLocal() {
    const user = getUser();
    const name = user?.real_name || user?.username || '';
    const app = getApp<IAppOption>();
    const isAdmin = !!app.globalData.isAdmin;
    this.setData({
      userName: name,
      avatar: user?.avatar || '',
      avatarText: name ? name.slice(0, 1) : '?',
      phone: user?.phone || '未绑定手机号',
      isAdmin,
      menu: buildMenu(isAdmin)
    });
    if (app.globalData.companies.length) {
      this.renderCompany(app.globalData.companies);
    }
  },

  renderCompany(companies: CompanyItem[]) {
    const companyId = getApp<IAppOption>().globalData.companyId;
    const current = companies.find((c) => c.id === companyId);
    this.setData({
      companyName: current ? current.name : '',
      postName: current && current.post ? current.post.name : ''
    });
  },

  onAction(e: WechatMiniprogram.TouchEvent) {
    const key = e.currentTarget.dataset.key as string;
    if (key === 'switch') {
      this.onSwitchCompany();
    } else if (key === 'join') {
      this.onJoinCompany();
    } else if (key === 'edit' || key === 'password') {
      // 资料编辑与修改密码同页（Agent A：packages/admin/profile-edit）
      wx.navigateTo({ url: '/packages/admin/profile-edit/index' });
    } else if (key === 'changes') {
      wx.navigateTo({ url: '/packages/salary/changes/index' });
    } else if (key === 'help') {
      // 使用帮助（§2.11 P3）：按角色三篇静态指引，默认展示当前岗位对应角色
      wx.navigateTo({ url: '/packages/admin/help/index' });
    } else if (key === 'settings') {
      wx.navigateTo({ url: '/packages/admin/settings/index' });
    } else if (key === 'privacy') {
      this.onPrivacy();
    } else if (key === 'manual') {
      this.onManual();
    } else if (key === 'subscribe') {
      this.onSubscribe();
    } else if (key === 'logout') {
      this.onLogout();
    } else if (key === 'deactivate') {
      this.onDeactivate();
    }
  },

  /** 隐私政策：暂无 H5，showModal 文案占位（待替换 web-view，规格 §16.2） */
  onPrivacy() {
    wx.showModal({
      title: '隐私政策',
      content: '我们仅收集为你提供计件生产管理服务所必需的信息（账号、姓名、手机号、计件与薪酬记录），不向第三方出售。完整政策文本上线后在此展示。',
      showCancel: false,
      confirmText: '我知道了'
    });
  },

  /** 操作手册：暂无 H5，showModal 文案占位（待替换 web-view，规格 §16.2） */
  onManual() {
    wx.showModal({
      title: '操作手册',
      content: '员工：扫菲 → 选工序 → 输件数，三步完成登记。管理员：款式 → 床次 → 编菲生成菲票，打印后下发车间。完整图文手册上线后在此展示。',
      showCancel: false,
      confirmText: '我知道了'
    });
  },

  /**
   * 注销账号（§16.2/规则⑧）：双重确认 → POST /auth/deactivate（无 body）。
   * 成功 {'message':'账号已注销'} → clearAuth + reLaunch 登录页；
   * 409/400 时 message 为拒绝原因（如最后一名管理员、密码校验失败）仅提示不注销；
   * 404（端点未部署）兜底 toast「功能开通中」。
   */
  onDeactivate() {
    wx.showModal({
      title: '注销账号',
      content: '注销后账号将无法登录，生产与工资记录匿名保留。确定要注销吗？',
      confirmColor: '#F53F3F', // = --color-danger（showModal 不支持 CSS 变量）
      success: (res1) => {
        if (!res1.confirm) return;
        wx.showModal({
          title: '再次确认',
          content: '这是不可恢复的操作，本账号下所有公司将无法再使用此手机号登录。确认注销？',
          confirmColor: '#F53F3F',
          success: (res2) => {
            if (!res2.confirm) return;
            this.doDeactivate('');
          }
        });
      }
    });
  },

  doDeactivate(password: string) {
    wx.showLoading({ title: '提交中', mask: true });
    http.post<{ message: string }>('/auth/deactivate', password ? { password } : undefined, { silent: true })
      .then(() => {
        wx.hideLoading();
        wx.showToast({ title: '账号已注销', icon: 'none' });
        setTimeout(() => {
          clearAuth();
          const app = getApp<IAppOption>();
          app.globalData.token = '';
          app.globalData.refresh = '';
          app.globalData.user = null;
          app.globalData.companyId = 0;
          app.globalData.companies = [];
          app.globalData.isAdmin = false;
          app.globalData.permissions = [];
          wx.reLaunch({ url: '/pages/login/index' });
        }, 800);
      })
      .catch((err) => {
        wx.hideLoading();
        const status = err instanceof ApiError ? err.status : 0;
        const msg = (err instanceof ApiError && err.message) || '注销失败，请稍后再试';
        if (status === 404) {
          wx.showToast({ title: '功能开通中', icon: 'none' });
          return;
        }
        // 后端对设了密码的账号要求密码二次校验：弹框补录后重试一次
        if (msg.indexOf('密码') >= 0 && !password) {
          wx.showModal({
            title: '身份验证',
            content: '注销账号需验证登录密码',
            editable: true,
            placeholderText: '请输入登录密码',
            success: (res) => {
              if (res.confirm && res.content) {
                this.doDeactivate(res.content);
              }
            }
          });
          return;
        }
        wx.showToast({ title: msg.length > 20 ? msg.slice(0, 20) + '…' : msg, icon: 'none', duration: 2500 });
      });
  },

  /** 切换公司：列出在职（status=1）公司 → switchCompany → reLaunch 首页重拉数据 */
  onSwitchCompany() {
    const companies = (getApp<IAppOption>().globalData.companies || []).filter(
      (c: CompanyItem) => c.status === 1
    );
    if (!companies.length) {
      wx.showToast({ title: '暂无在职公司', icon: 'none' });
      return;
    }
    const names = companies.slice(0, 6).map((c) => c.name + (c.post ? `（${c.post.name}）` : ''));
    wx.showActionSheet({
      itemList: names,
      success: (res) => {
        const target = companies[res.tapIndex];
        if (!target) return;
        if (target.id === getApp<IAppOption>().globalData.companyId) {
          wx.showToast({ title: '已是当前公司', icon: 'none' });
          return;
        }
        switchCompany(target.id)
          .then(() => {
            wx.showToast({ title: '已切换，正在刷新数据', icon: 'none' });
            setTimeout(() => {
              wx.reLaunch({ url: '/pages/index/index' });
            }, 600);
          })
          .catch(() => undefined);
      }
    });
  },

  /** 加入公司：输入公司编号 */
  onJoinCompany() {
    wx.showModal({
      title: '加入公司',
      editable: true,
      placeholderText: '请输入公司编号',
      success: (res) => {
        if (!res.confirm) return;
        const companyNo = (res.content || '').trim();
        if (!companyNo) {
          wx.showToast({ title: '请输入公司编号', icon: 'none' });
          return;
        }
        joinCompany(companyNo)
          .then((r) => {
            wx.showToast({ title: r.message || '已提交申请', icon: 'none' });
            return fetchMyCompanies();
          })
          .then((list) => {
            getApp<IAppOption>().globalData.companies = list;
            this.renderCompany(list);
          })
          .catch(() => undefined);
      }
    });
  },

  /** 订阅消息引导（重要事件走订阅消息，规格 §16.1） */
  onSubscribe() {
    if (!SUBSCRIBE_TEMPLATE_IDS.length) {
      wx.showToast({ title: '订阅消息模板待配置', icon: 'none' });
      return;
    }
    wx.requestSubscribeMessage({
      tmplIds: SUBSCRIBE_TEMPLATE_IDS,
      complete: () => undefined
    });
  },

  /** 退出登录 */
  onLogout() {
    wx.showModal({
      title: '退出登录',
      content: '确定退出当前账号？',
      success: (res) => {
        if (!res.confirm) return;
        clearAuth();
        const app = getApp<IAppOption>();
        app.globalData.token = '';
        app.globalData.user = null;
        app.globalData.companyId = 0;
        app.globalData.companies = [];
        app.globalData.isAdmin = false;
        app.globalData.permissions = [];
        wx.reLaunch({ url: '/pages/login/index' });
      }
    });
  }
});
