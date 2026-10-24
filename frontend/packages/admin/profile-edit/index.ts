/**
 * 个人资料编辑（规格 §16.2）：头像上传 + real_name/gender/birthday 保存 + 修改密码。
 * 头像：chooseImage → utils/upload.uploadImage → URL 随保存一并 PUT /auth/me
 * （后端 UserSerializer 字段含 avatar，已核对 apps/accounts/serializers.py）。
 * 保存按钮随「脏数据」启用：与最近一次加载/保存的快照比对。
 */
import { requireLogin, getUser, setUser } from '../../../utils/auth';
import { fetchMe, updateProfile, refreshMe, changePassword } from '../../../services/auth';
import { User } from '../../../services/types';
import { chooseImage, uploadImage } from '../../../utils/upload';
import { mediaUrl } from '../../../utils/media';

const GENDERS = ['未知', '男', '女'];

interface ProfileSnapshot {
  realName: string;
  gender: number;
  birthday: string;
  avatar: string;
}

function snapOf(realName: string, gender: number, birthday: string, avatar: string): ProfileSnapshot {
  return { realName, gender, birthday, avatar };
}

Page({
  data: {
    genders: GENDERS,
    phone: '',
    realName: '',
    gender: 0,
    genderName: GENDERS[0],
    birthday: '',
    avatar: '',
    /** 头像展示地址（/uploads 相对路径补域名，真机才显示） */
    avatarUrl: '',
    today: '',
    profileDirty: false,
    savingProfile: false,
    uploadingAvatar: false,
    // 修改密码
    oldPassword: '',
    newPassword: '',
    confirmPassword: '',
    savingPwd: false
  },

  /** 最近一次加载/保存后的基线快照（脏数据比对基准） */
  baseline: snapOf('', 0, '', ''),

  onShow() {
    if (!requireLogin()) return;
    this.setData({ today: this.fmtToday() });
    fetchMe()
      .then((user: User) => this.applyUser(user))
      .catch(() => {
        // 接口失败时回退本地缓存
        const cached = getUser();
        if (cached) this.applyUser(cached as unknown as User);
      });
  },

  fmtToday(): string {
    const d = new Date();
    const m = `${d.getMonth() + 1}`.padStart(2, '0');
    const day = `${d.getDate()}`.padStart(2, '0');
    return `${d.getFullYear()}-${m}-${day}`;
  },

  applyUser(u: User) {
    const gender = u.gender || 0;
    const avatar = u.avatar || '';
    this.baseline = snapOf(u.real_name || '', gender, u.birthday || '', avatar);
    this.setData({
      phone: u.phone || '',
      realName: u.real_name || '',
      gender,
      genderName: GENDERS[gender] || GENDERS[0],
      birthday: u.birthday || '',
      avatar,
      avatarUrl: avatar ? mediaUrl(avatar) : '',
      profileDirty: false
    });
  },

  /** 当前表单与基线比对，标记保存按钮启用态 */
  markDirty() {
    const d = this.data;
    const dirty =
      d.realName !== this.baseline.realName ||
      d.gender !== this.baseline.gender ||
      d.birthday !== this.baseline.birthday ||
      d.avatar !== this.baseline.avatar;
    this.setData({ profileDirty: dirty });
  },

  onNameInput(e: WechatMiniprogram.Input) {
    this.setData({ realName: e.detail.value }, () => this.markDirty());
  },

  onGenderChange(e: WechatMiniprogram.PickerChange) {
    const gender = Number(e.detail.value) || 0;
    this.setData({ gender, genderName: GENDERS[gender] || GENDERS[0] }, () => this.markDirty());
  },

  onBirthdayChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ birthday: String(e.detail.value || '') }, () => this.markDirty());
  },

  onChooseAvatar() {
    if (this.data.uploadingAvatar) return;
    chooseImage()
      .then((tempPath: string) => {
        this.setData({ uploadingAvatar: true });
        return uploadImage(tempPath);
      })
      .then((url: string) => {
        this.setData({ avatar: url, avatarUrl: mediaUrl(url) }, () => this.markDirty());
      })
      .catch((err: Error) => {
        // 用户取消选图不提示；上传失败由提示区分
        if (err && err.message && err.message !== '未选择图片') {
          wx.showToast({ title: err.message, icon: 'none' });
        }
      })
      .then(() => this.setData({ uploadingAvatar: false }));
  },

  onSaveProfile() {
    if (this.data.savingProfile) return;
    if (!this.data.profileDirty) return;
    const d = this.data;
    this.setData({ savingProfile: true });
    // 头像字段后端已支持（UserSerializer.avatar），随资料一并提交
    updateProfile({
      real_name: d.realName.trim(),
      gender: d.gender,
      birthday: d.birthday,
      avatar: d.avatar
    } as Parameters<typeof updateProfile>[0])
      .then((user: User) => {
        // 同步本地缓存/globalData 的用户资料
        setUser(user);
        const app = getApp<IAppOption>();
        app.globalData.user = user;
        this.applyUser(user);
        wx.showToast({ title: '资料已保存', icon: 'success' });
      })
      .catch(() => { /* 失败 toast 已由请求层统一弹出 */ })
      .then(() => this.setData({ savingProfile: false }));
  },

  // ---- 修改密码 ----

  onOldPwd(e: WechatMiniprogram.Input) {
    this.setData({ oldPassword: e.detail.value });
  },

  onNewPwd(e: WechatMiniprogram.Input) {
    this.setData({ newPassword: e.detail.value });
  },

  onConfirmPwd(e: WechatMiniprogram.Input) {
    this.setData({ confirmPassword: e.detail.value });
  },

  onSavePassword() {
    if (this.data.savingPwd) return;
    const { oldPassword, newPassword, confirmPassword } = this.data;
    if (!oldPassword || !newPassword) {
      wx.showToast({ title: '请填写原密码与新密码', icon: 'none' });
      return;
    }
    if (newPassword.length < 6) {
      wx.showToast({ title: '新密码至少 6 位', icon: 'none' });
      return;
    }
    if (newPassword !== confirmPassword) {
      wx.showToast({ title: '两次输入的新密码不一致', icon: 'none' });
      return;
    }
    this.setData({ savingPwd: true });
    changePassword(oldPassword, newPassword)
      .then(() => {
        wx.showToast({ title: '密码修改成功', icon: 'success' });
        this.setData({ oldPassword: '', newPassword: '', confirmPassword: '' });
        // 后端改密可能使旧 token 失效，静默刷新个人资料不阻断
        refreshMe().catch(() => { /* 忽略 */ });
      })
      .catch(() => {})
      .then(() => this.setData({ savingPwd: false }));
  }
});
