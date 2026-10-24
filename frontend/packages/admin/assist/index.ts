/**
 * 协助扫菲入口（管理员代员工登记，规格 §9.2）：
 * 「选择员工」走 employee-picker 组件（规格 §11.3：搜索 + 角色 Tab + 3 列网格 + 橙色确定）。
 * 选中员工后暂存目标并切到扫菲页（switchTab 不带参，走 storage）。
 * 扫菲页登记时 user_id=被代员工（User 主键，Employee.user_id），后端记录 operator=操作者并写「协助登记」日志。
 */
import { requireLogin } from '../../../utils/auth';
import { Employee } from '../../../services/types';

/** 与 pages/scan 约定一致的暂存键 */
const ASSIST_KEY = 'yisao_assist_target';

Page({
  data: {
    pickerVisible: false
  },

  onShow() {
    if (!requireLogin()) return;
  },

  onOpenPicker() {
    this.setData({ pickerVisible: true });
  },

  onClosePicker() {
    this.setData({ pickerVisible: false });
  },

  /** 选中员工（单选取第一个）：暂存目标并切到扫菲 tab */
  onPickerConfirm(e: WechatMiniprogram.CustomEvent<{ users: Employee[] }>) {
    const users = (e.detail && e.detail.users) || [];
    const u = users[0];
    if (!u || !u.user_id) return;
    wx.setStorageSync(ASSIST_KEY, { userId: u.user_id, name: u.name || '' });
    this.setData({ pickerVisible: false });
    wx.switchTab({ url: '/pages/scan/index' });
  }
});
