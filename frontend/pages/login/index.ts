/**
 * 登录页：验证码 / 密码 / 微信三通道 + 协议勾选（规格 §2.1）。
 * 登录成功 → 拉公司列表：
 *   - 无任何公司 → 引导凭公司编号加入（joinCompany）/ 创建公司（createCompany）
 *   - 仅有待审核成员关系 → 「等待审核」态页
 *   - 单个在职公司 → switchCompany → reLaunch 首页
 *   - 多个在职公司 → 弹选择器 → switchCompany → reLaunch 首页
 * 支持 companyNo 落地参数（公司二维码分享卡片带入，自动预填编号申请加入）。
 */
import {
  sendSmsCode, smsLogin, passwordLogin, wechatLogin,
  fetchMyCompanies, joinCompany, switchCompany, createCompany
} from '../../services/auth';
import { CompanyItem } from '../../services/types';
import { clearAuth } from '../../utils/auth';

type Mode = 'sms' | 'password';
/** 页面阶段：form 登录表单 / noCompany 无公司引导 / pending 等待审核 / pick 多公司选择 */
type Stage = 'form' | 'noCompany' | 'pending' | 'pick';

Page({
  data: {
    mode: 'sms' as Mode,
    stage: 'form' as Stage,
    phone: '',
    code: '',
    password: '',
    smsCountdown: 0,
    submitting: false,
    // 协议勾选（未勾选点登录时 toast + 高亮）
    agreed: false,
    agreedShake: false,
    // 公司引导
    joinNo: '',
    // 创建公司弹层
    createVisible: false,
    coName: '',
    coCity: '',
    creating: false,
    activeCompanies: [] as CompanyItem[],
    pendingCompanies: [] as CompanyItem[],
    switchingId: 0
  },

  timer: undefined as ReturnType<typeof setInterval> | undefined,
  joining: false,
  /** 分享卡片带入的公司编号（登录后预填进加入表单） */
  pendingJoinNo: '',

  onLoad(options: Record<string, string | undefined>) {
    // 公司二维码页分享的卡片带 companyNo 参数（约定见 packages/admin/company-qr）
    if (options && options.companyNo) {
      this.pendingJoinNo = options.companyNo;
    }
    // 小程序码（scene=c=<company_no>）：微信「扫一扫」扫码加入的落地参数
    if (options && options.scene) {
      const m = /^c=(\d{1,8})$/.exec(decodeURIComponent(options.scene));
      if (m) {
        this.pendingJoinNo = m[1];
      }
    }
  },

  onUnload() {
    if (this.timer) {
      clearInterval(this.timer);
    }
  },

  onMode(e: WechatMiniprogram.TouchEvent) {
    this.setData({ mode: e.currentTarget.dataset.mode as Mode });
  },

  onPhone(e: WechatMiniprogram.Input) {
    this.setData({ phone: e.detail.value });
  },

  onCode(e: WechatMiniprogram.Input) {
    this.setData({ code: e.detail.value });
  },

  onPassword(e: WechatMiniprogram.Input) {
    this.setData({ password: e.detail.value });
  },

  onJoinNo(e: WechatMiniprogram.Input) {
    this.setData({ joinNo: e.detail.value });
  },

  // ---- 协议勾选 ----

  toggleAgree() {
    this.setData({ agreed: !this.data.agreed, agreedShake: false });
  },

  ensureAgreed(): boolean {
    if (this.data.agreed) {
      return true;
    }
    wx.showToast({ title: '请先勾选同意《隐私政策》与《用户协议》', icon: 'none' });
    this.setData({ agreedShake: true });
    setTimeout(() => this.setData({ agreedShake: false }), 800);
    return false;
  },

  // ---- 验证码 ----

  onSendSms() {
    if (this.data.smsCountdown > 0) return;
    if (!this.ensureAgreed()) return;
    if (!/^1\d{10}$/.test(this.data.phone)) {
      wx.showToast({ title: '请输入正确的手机号', icon: 'none' });
      return;
    }
    sendSmsCode(this.data.phone).then(() => {
      wx.showToast({ title: '验证码已发送', icon: 'none' });
      this.setData({ smsCountdown: 60 });
      this.timer = setInterval(() => {
        const n = this.data.smsCountdown - 1;
        this.setData({ smsCountdown: n });
        if (n <= 0 && this.timer) {
          clearInterval(this.timer);
        }
      }, 1000);
    });
  },

  // ---- 三通道登录 ----

  onSmsLogin() {
    if (this.data.submitting) return;
    if (!this.ensureAgreed()) return;
    if (!this.data.phone || !this.data.code) {
      wx.showToast({ title: '请填写手机号与验证码', icon: 'none' });
      return;
    }
    this.setData({ submitting: true });
    smsLogin(this.data.phone, this.data.code)
      .then(() => this.afterLogin())
      .catch(() => this.setData({ submitting: false }));
  },

  onPasswordLogin() {
    if (this.data.submitting) return;
    if (!this.ensureAgreed()) return;
    if (!this.data.phone || !this.data.password) {
      wx.showToast({ title: '请填写手机号与密码', icon: 'none' });
      return;
    }
    this.setData({ submitting: true });
    passwordLogin(this.data.phone, this.data.password)
      .then(() => this.afterLogin())
      .catch(() => this.setData({ submitting: false }));
  },

  onWechatLogin() {
    if (this.data.submitting) return;
    if (!this.ensureAgreed()) return;
    wx.login({
      success: (res) => {
        if (!res.code) {
          wx.showToast({ title: '微信登录失败，请重试', icon: 'none' });
          return;
        }
        this.setData({ submitting: true });
        wechatLogin(res.code)
          .then(() => this.afterLogin())
          .catch((err: Error) => {
            // 微信登录失败（该微信未绑定手机号 / 后端参数未配置等）：明确提示并回退验证码登录
            wx.showModal({
              title: '微信登录失败',
              content: (err && err.message) || '该微信尚未绑定手机号，请使用验证码登录',
              showCancel: false
            });
            this.setData({ mode: 'sms', submitting: false });
          });
      },
      fail: () => {
        wx.showToast({ title: '微信登录失败，请重试', icon: 'none' });
      }
    });
  },

  // ---- 登录后：公司上下文引导 ----

  afterLogin() {
    fetchMyCompanies().then((companies) => {
      const app = getApp<IAppOption>();
      app.globalData.companies = companies;
      const active = companies.filter((c) => c.status === 1);
      const pending = companies.filter((c) => c.status === 0);
      if (active.length === 0) {
        this.setData({
          stage: pending.length > 0 ? 'pending' : 'noCompany',
          pendingCompanies: pending,
          activeCompanies: [],
          joinNo: pending.length === 0 ? (this.pendingJoinNo || '') : '',
          submitting: false
        });
        return;
      }
      if (active.length === 1) {
        this.pickCompany(active[0].id);
        return;
      }
      this.setData({ stage: 'pick', activeCompanies: active, pendingCompanies: pending, submitting: false });
    }).catch(() => this.setData({ submitting: false }));
  },

  pickCompany(id: number) {
    if (this.data.switchingId) return;
    this.setData({ switchingId: id });
    switchCompany(id)
      .then(() => this.enterApp())
      .catch(() => this.setData({ switchingId: 0 }));
  },

  onPickCompany(e: WechatMiniprogram.TouchEvent) {
    this.pickCompany(Number(e.currentTarget.dataset.id));
  },

  enterApp() {
    wx.reLaunch({ url: '/pages/index/index' });
  },

  // ---- 无公司：凭编号加入 / 创建公司 ----

  onJoin() {
    const no = this.data.joinNo.trim();
    if (!no) {
      wx.showToast({ title: '请输入公司编号', icon: 'none' });
      return;
    }
    if (this.joining) return;
    this.joining = true;
    joinCompany(no)
      .then(() => {
        wx.showToast({ title: '申请已提交，等待管理员审核', icon: 'none' });
        this.afterLogin();
      })
      .catch(() => {})
      .then(() => { this.joining = false; });
  },

  // ---- 创建公司（弹层表单：名称必填 / 城市选填） ----

  onCreateCompany() {
    this.setData({ createVisible: true, coName: '', coCity: '' });
  },

  onCloseCreate() {
    if (this.data.creating) return;
    this.setData({ createVisible: false });
  },

  onCoName(e: WechatMiniprogram.Input) {
    this.setData({ coName: e.detail.value });
  },

  onCoCity(e: WechatMiniprogram.Input) {
    this.setData({ coCity: e.detail.value });
  },

  onCreateSubmit() {
    if (this.data.creating) return;
    const name = this.data.coName.trim();
    if (!name) {
      wx.showToast({ title: '请输入公司名称', icon: 'none' });
      return;
    }
    this.setData({ creating: true });
    // 创建人自动成为该公司在职管理员，成功后刷新公司列表走正常进入分支
    createCompany(name, this.data.coCity.trim())
      .then(() => {
        wx.showToast({ title: '公司创建成功', icon: 'success' });
        this.setData({ createVisible: false, creating: false });
        this.afterLogin();
      })
      .catch(() => this.setData({ creating: false }));
  },

  noop() { /* 阻止弹层冒泡关闭 */ },

  // ---- 待审核态 ----

  onRefreshStatus() {
    this.afterLogin();
  },

  onLogout() {
    wx.showModal({
      title: '退出登录',
      content: '确定退出当前账号吗？',
      success: (r) => {
        if (r.confirm) {
          clearAuth();
          this.setData({ stage: 'form', phone: '', code: '', password: '', joinNo: '', agreed: false });
        }
      }
    });
  }
});
