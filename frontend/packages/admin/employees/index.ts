/**
 * 员工管理列表：置顶、状态筛选（全部/待审核/在职/离职）、待审核通过/拒绝、
 * 在职 ⋯ 菜单（设置岗位/设置组/设置工资/办理离职）、离职复职；离职遇未结算记录二次确认。
 * 「邀请员工」（规格 §11.1）：手机号 + 姓名/岗位弹层 → 创建待审核成员。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import {
  fetchEmployees, approveEmployee, rejectEmployee, resignEmployee,
  rehireEmployee, setEmployeePost, setEmployeeGroup, pinEmployee, fetchPosts
} from '../../../services/auth';
import { inviteEmployee } from '../../../services/org';
import { updateEmployeeConfig } from '../../../services/salary';
import { Employee, MemberStatus, Post } from '../../../services/types';
import { currentCompanyId } from '../../../utils/format';

type TabKey = 'all' | 'pending' | 'active' | 'resigned';

interface EmpVM extends Employee {
  initial: string;
  /** 姓氏头像色块（按姓名散列到 6 色之一） */
  avColor: number;
  /** 性别角标：♂ / ♀ / ''（未知不显示） */
  genderMark: string;
}

/** 头像色板（与衣扫对齐：姓氏色块头像） */
const AVATAR_COLORS = 6;

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'pending', label: '待审核' },
  { key: 'active', label: '在职' },
  { key: 'resigned', label: '离职' }
];

/** 筛选 Tab → MemberStatus（null = 不筛选） */
const STATUS_BY_TAB: Record<TabKey, MemberStatus | null> = {
  all: null,
  pending: 0,
  active: 1,
  resigned: 2
};

/** Employee 判断：resign 接口可能返回 {message, need_confirm} 询问体 */
function isEmployee(v: Employee | { message: string; need_confirm: true }): v is Employee {
  return !!v && typeof (v as Employee).status !== 'undefined';
}

Page({
  data: {
    tabs: TABS,
    tab: 'all' as TabKey,
    /** 权限点体系二期：邀请/审核/设岗等管理动作按 employees.manage 点显隐 */
    canManage: false,
    /** 工资设置入口按 salary.config 点显隐（仅在职员工） */
    canSalary: false,
    loading: true,
    list: [] as EmpVM[],
    capacity: 0,
    memberCount: 0,
    capacityPercent: 0,
    // 岗位筛选 chips（0 = 全部）+ 岗位管理入口
    posts: [] as Post[],
    postFilter: 0,
    // 邀请员工弹层
    inviteVisible: false,
    invitePhone: '',
    inviteName: '',
    invitePosts: [] as string[],        // picker 选项：不指定岗位 + 岗位名
    invitePostIds: [] as number[],      // 与选项对应的岗位 id（0 = 不指定）
    invitePostIndex: 0,
    inviteSubmitting: false,
    // 工资设置弹层（固定工资 = 金额 + 按天/按月）
    salaryVisible: false,
    salaryEmpId: 0,                     // 后端按 user_id 定位配置（非 UserCompany 主键）
    salaryEmpName: '',
    salaryType: 'process' as 'fixed' | 'whole' | 'process' | 'commission',
    salaryAmount: '',                   // fixed：金额（空串或 0 = 取消固定工资）
    salaryFixedKind: 'day' as 'day' | 'month',
    salaryRate: '',                     // whole：整件单价 / commission：提成比例%
    salarySubmitting: false
  },

  /** 接口原始列表（筛选前的全量数据） */
  all: [] as Employee[],

  onLoad(options: Record<string, string | undefined>) {
    // 岗位管理「查看成员」跳入：?post=<postId> 直接按岗位过滤
    if (options && options.post) {
      const pid = Number(options.post);
      if (pid > 0) this.setData({ postFilter: pid });
    }
    // 我的-待我处理跳入：?tab=pending 直达待审核
    if (options && options.tab === 'pending') {
      this.setData({ tab: 'pending' });
    }
  },

  onShow() {
    if (!requireLogin()) return;
    // 邀请/审核/设岗/岗位管理入口统一按 employees.manage 点显隐（hasPerm 未初始化时回退全量）
    this.setData({
      canManage: hasPerm('employees.manage'),
      canSalary: hasPerm('salary.config')
    });
    this.loadCapacity();
    this.loadPosts();
    this.load();
  },

  /** 岗位列表（筛选 chips 用；从岗位管理页返回时 onShow 会重拉保持最新） */
  loadPosts() {
    fetchPosts().then((posts) => this.setData({ posts })).catch(() => {});
  },

  /** 岗位筛选 chips：全部 + 各岗位 */
  onPickPostFilter(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id) || 0;
    if (id === this.data.postFilter) return;
    this.setData({ postFilter: id }, () => this.load());
  },

  /** 右侧「岗位管理」入口 */
  onOpenPosts() {
    wx.navigateTo({ url: '/packages/admin/posts/index' });
  },

  loadCapacity() {
    const cid = currentCompanyId();
    const company = getApp<IAppOption>().globalData.companies.find((c) => c.id === cid);
    if (!company) return;
    const capacity = company.capacity || 0;
    const memberCount = company.member_count || 0;
    this.setData({
      capacity,
      memberCount,
      capacityPercent: capacity > 0 ? Math.min(100, Math.round((memberCount / capacity) * 100)) : 0
    });
  },

  load() {
    this.setData({ loading: true });
    // 后端 GET /employees 按 tab=all|pending|resigned 过滤（all 即在职）；
    // 页面 'all' 与 'active' 两个 tab 都对应后端 'all'，切 tab 时按 tab 重新拉取。
    const TAB_TO_API: Record<TabKey, 'all' | 'pending' | 'resigned'> = {
      all: 'all', active: 'all', pending: 'pending', resigned: 'resigned'
    };
    fetchEmployees(TAB_TO_API[this.data.tab], undefined, this.data.postFilter || undefined).then((list) => {
      this.all = list;
      this.applyFilter();
      this.setData({ loading: false });
    }).catch(() => {
      this.all = [];
      this.applyFilter();
      this.setData({ loading: false });
    });
  },

  applyFilter() {
    const status = STATUS_BY_TAB[this.data.tab];
    const filtered = status === null
      ? this.all
      : this.all.filter((e) => e.status === status);
    const list: EmpVM[] = filtered.map((e) => ({
      ...e,
      initial: e.name ? e.name.slice(0, 1) : '?',
      avColor: e.name ? (e.name.charCodeAt(0) % AVATAR_COLORS) : 0,
      genderMark: e.gender === 1 ? '♂' : e.gender === 2 ? '♀' : ''
    }));
    this.setData({ list });
  },

  onTab(e: WechatMiniprogram.TouchEvent) {
    const tab = e.currentTarget.dataset.key as TabKey;
    if (tab === this.data.tab) return;
    // 切 tab 重新按 tab 拉取（后端按 tab 过滤），拉回后 applyFilter 兜底过滤
    this.setData({ tab }, () => this.load());
  },

  onPullDownRefresh() {
    this.load();
    wx.stopPullDownRefresh();
  },

  // ---- 置顶 ----

  onTogglePin(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const emp = this.all.find((x) => x.id === id);
    if (!emp) return;
    pinEmployee(id, !emp.is_pinned).then(() => {
      this.all = this.all.map((x) => (x.id === id ? { ...x, is_pinned: !x.is_pinned } : x));
      this.applyFilter();
    }).catch(() => {});
  },

  // ---- 待审核：通过 / 拒绝 ----

  onApprove(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    approveEmployee(id)
      .then(() => {
        wx.showToast({ title: '已通过', icon: 'success' });
        this.load();
      })
      .catch(() => {});
  },

  onReject(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    wx.showModal({
      title: '拒绝申请',
      content: '确定拒绝该成员的入职申请吗？',
      success: (r) => {
        if (!r.confirm) return;
        rejectEmployee(id)
          .then(() => {
            wx.showToast({ title: '已拒绝', icon: 'success' });
            this.load();
          })
          .catch(() => {});
      }
    });
  },

  // ---- 在职/离职：⋯ 菜单 ----

  onMenu(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const emp = this.all.find((x) => x.id === id);
    if (!emp) return;
    if (emp.status === 1) {
      // 「设置工资」仅在职员工显示，且按 salary.config 点显隐
      const items = ['设置岗位', '设置组'];
      if (this.data.canSalary) items.push('设置工资');
      items.push('办理离职');
      wx.showActionSheet({
        itemList: items,
        success: (r) => {
          const label = items[r.tapIndex];
          if (label === '设置岗位') this.pickPost(emp);
          else if (label === '设置组') this.pickGroup(emp);
          else if (label === '设置工资') this.pickSalary(emp);
          else if (label === '办理离职') this.askResign(emp);
        }
      });
    } else if (emp.status === 2) {
      wx.showActionSheet({
        itemList: ['复职'],
        success: (r) => {
          if (r.tapIndex === 0) {
            rehireEmployee(emp.id)
              .then(() => {
                wx.showToast({ title: '已复职', icon: 'success' });
                this.load();
              })
              .catch(() => {});
          }
        }
      });
    }
  },

  // ---- 设置岗位 ----

  pickPost(emp: Employee) {
    fetchPosts().then((posts: Post[]) => {
      if (!posts.length) {
        wx.showToast({ title: '请先在岗位管理中创建岗位', icon: 'none' });
        return;
      }
      // ActionSheet 最多 6 项，超出部分提示到岗位管理调整
      const shown = posts.slice(0, 6);
      wx.showActionSheet({
        itemList: shown.map((p) => `${p.name}${p.is_admin ? '（管理员）' : ''}`),
        success: (r) => {
          const post = shown[r.tapIndex];
          if (!post) return;
          setEmployeePost(emp.id, post.id)
            .then(() => {
              wx.showToast({ title: '岗位已更新', icon: 'success' });
              this.load();
            })
            .catch(() => {});
        }
      });
    }).catch(() => {});
  },

  // ---- 设置组编号（1组/2组…，必填非空） ----

  pickGroup(emp: Employee) {
    wx.showModal({
      title: `设置组编号（${emp.name}）`,
      editable: true,
      placeholderText: '如：1（显示为 1组）',
      content: String(emp.group_no || ''),
      success: (r) => {
        if (!r.confirm) return;
        const text = ((r as { content?: string }).content || '').trim();
        const n = Number(text);
        if (!text || !Number.isInteger(n) || n < 1 || n > 999) {
          wx.showToast({ title: '请输入 1~999 的整数', icon: 'none' });
          return;
        }
        setEmployeeGroup(emp.id, n)
          .then(() => {
            wx.showToast({ title: `已设为 ${n}组`, icon: 'success' });
            this.load();
          })
          .catch(() => {});
      }
    });
  },

  // ---- 设置工资（四类工资制，四选一互斥：固定/整件计件/工序计件/计件提成） ----

  /** 打开工资设置弹层（按当前工资方式预填对应参数） */
  pickSalary(emp: Employee) {
    const st = emp.salary_type || 'process';
    this.setData({
      salaryVisible: true,
      salaryEmpId: emp.user_id,
      salaryEmpName: emp.name,
      salaryType: st,
      salaryAmount: emp.fixed_salary && Number(emp.fixed_salary) > 0 ? emp.fixed_salary : '',
      salaryFixedKind: emp.fixed_type === 'month' ? 'month' : 'day',
      salaryRate: st === 'whole'
        ? (emp.piece_rate && Number(emp.piece_rate) > 0 ? emp.piece_rate : '')
        : st === 'commission'
          ? (emp.commission_rate && Number(emp.commission_rate) > 0 ? emp.commission_rate : '')
          : ''
    });
  },

  closeSalary() {
    if (this.data.salarySubmitting) return;
    this.setData({ salaryVisible: false });
  },

  onSalaryAmount(e: WechatMiniprogram.Input) {
    this.setData({ salaryAmount: e.detail.value });
  },

  onSalaryRate(e: WechatMiniprogram.Input) {
    this.setData({ salaryRate: e.detail.value });
  },

  onPickSalaryType(e: WechatMiniprogram.TouchEvent) {
    const t = e.currentTarget.dataset.type as 'fixed' | 'whole' | 'process' | 'commission';
    if (t === this.data.salaryType) return;
    // 切换方式时清空另一口径的参数，避免误提交旧值
    this.setData({ salaryType: t, salaryRate: '' });
  },

  onPickFixedKind(e: WechatMiniprogram.TouchEvent) {
    this.setData({ salaryFixedKind: e.currentTarget.dataset.kind === 'month' ? 'month' : 'day' });
  },

  submitSalary() {
    const st = this.data.salaryType;
    const data: Parameters<typeof updateEmployeeConfig>[1] = { salary_type: st };
    if (st === 'fixed') {
      const raw = (this.data.salaryAmount || '').trim();
      // 清空或 0 = 取消固定工资（后端 fixed_salary=0 即不计固定工资）
      let amount = '0';
      if (raw) {
        if (!/^\d+(\.\d{1,2})?$/.test(raw)) {
          wx.showToast({ title: '请输入不小于 0 的金额（最多两位小数）', icon: 'none' });
          return;
        }
        amount = String(Number(raw));
      }
      data.fixed_salary = amount;
      data.fixed_type = this.data.salaryFixedKind;
    } else if (st === 'whole') {
      const raw = (this.data.salaryRate || '').trim();
      if (!raw || Number(raw) <= 0) {
        wx.showToast({ title: '请输入大于 0 的整件单价', icon: 'none' });
        return;
      }
      data.piece_rate = raw;
    } else if (st === 'commission') {
      const raw = (this.data.salaryRate || '').trim();
      if (!raw || Number(raw) <= 0 || Number(raw) > 100) {
        wx.showToast({ title: '提成比例须大于 0 且不超过 100', icon: 'none' });
        return;
      }
      data.commission_rate = raw;
    }
    if (this.data.salarySubmitting) return;
    this.setData({ salarySubmitting: true });
    updateEmployeeConfig(this.data.salaryEmpId, data).then(() => {
      this.setData({ salaryVisible: false, salarySubmitting: false });
      wx.showToast({ title: '工资方式已更新', icon: 'success' });
      this.load();
    }).catch(() => {
      this.setData({ salarySubmitting: false });
    });
  },

  // ---- 办理离职（含未结算二次确认） ----

  askResign(emp: Employee) {
    resignEmployee(emp.id).then((res) => {
      const extra = res as unknown as { need_confirm?: boolean; confirm?: boolean; message?: string };
      if (isEmployee(res)) {
        this.afterResign();
        return;
      }
      if (extra.need_confirm === true || extra.confirm === true) {
        // 存在未结算记录：二次确认
        wx.showModal({
          title: '存在未结算记录',
          content: extra.message || '该员工存在未结算的计件记录，确认仍要办理离职吗？',
          success: (r) => {
            if (r.confirm) this.confirmResign(emp.id);
          }
        });
      }
    }).catch(() => {});
  },

  confirmResign(id: number) {
    // services 已支持 force：确认后传 force=1 真正离职
    resignEmployee(id, true).then((res) => {
      if (isEmployee(res)) {
        this.afterResign();
        return;
      }
      wx.showModal({
        title: '无法办理离职',
        content: '该员工存在未结算的计件记录，请先完成薪酬结算后再办理离职',
        showCancel: false
      });
    }).catch(() => {});
  },

  afterResign() {
    wx.showToast({ title: '已办理离职', icon: 'success' });
    this.loadCapacity();
    this.load();
  },

  // ---- 邀请员工（规格 §11.1：手机号邀请 + 录入姓名/岗位） ----

  openInvite() {
    fetchPosts().then((posts: Post[]) => {
      const options = ['不指定岗位'].concat(posts.map((p) => p.name));
      const ids = [0].concat(posts.map((p) => p.id));
      this.setData({
        inviteVisible: true,
        invitePhone: '',
        inviteName: '',
        invitePosts: options,
        invitePostIds: ids,
        invitePostIndex: 0
      });
    }).catch(() => {
      // 岗位拉取失败也能邀请（岗位可空）
      this.setData({
        inviteVisible: true,
        invitePhone: '',
        inviteName: '',
        invitePosts: ['不指定岗位'],
        invitePostIds: [0],
        invitePostIndex: 0
      });
    });
  },

  closeInvite() {
    if (this.data.inviteSubmitting) return;
    this.setData({ inviteVisible: false });
  },

  /** 面板事件截流（空处理器名 catchtap="" 不拦截冒泡，必须绑真实方法） */
  noop() {},

  onInvitePhone(e: WechatMiniprogram.Input) {
    this.setData({ invitePhone: e.detail.value });
  },

  onInviteName(e: WechatMiniprogram.Input) {
    this.setData({ inviteName: e.detail.value });
  },

  onInvitePickPost(e: WechatMiniprogram.PickerChange) {
    this.setData({ invitePostIndex: Number(e.detail.value) });
  },

  submitInvite() {
    const phone = (this.data.invitePhone || '').trim();
    if (!/^\d{11}$/.test(phone)) {
      wx.showToast({ title: '请输入正确的手机号', icon: 'none' });
      return;
    }
    if (this.data.inviteSubmitting) return;
    const postId = this.data.invitePostIds[this.data.invitePostIndex] || 0;
    this.setData({ inviteSubmitting: true });
    inviteEmployee(phone, (this.data.inviteName || '').trim(), postId || null)
      .then(() => {
        this.setData({ inviteVisible: false, inviteSubmitting: false });
        wx.showToast({ title: '已邀请，等待对方登录后管理员审核', icon: 'none' });
        this.loadCapacity();
        this.load();
      })
      .catch(() => {
        this.setData({ inviteSubmitting: false });
      });
  }
});
