/**
 * 系统设置（管理员，规格 §15）：13 项开关/选项逐条列出。
 * 数据结构 CompanySettings KV（键名以后端 CompanySetting 存量为准）：
 * 一次 fetchSettings 拉全量 → 逐项 saveSettings 保存（PUT 支持部分键）。
 * 开关在服务端校验时生效，前端只控制显示；非管理员仅可查看（服务端 require_admin）。
 */
import { requireLogin } from '../../../utils/auth';
import { fetchSettings, saveSettings } from '../../../services/ops';
import { CompanySettings } from '../../../services/types';

interface SettingRow {
  key: string;
  label: string;
  desc: string;
  /** switch 开关 / choice 二选一（默认打印方式）/ link 入口（打印管理、云打印） */
  kind: 'switch' | 'choice' | 'link';
  checked: boolean;
  /** choice/link 行的右侧文案 */
  valueText: string;
  /** 灰置不可用（云打印未接入，§7 蓝牙优先） */
  disabled?: boolean;
}

/** 11 个开关项（键与语义以后端 CompanySetting.DEFAULTS 为准） */
const SWITCH_ROWS: Array<{ key: string; label: string; desc: string }> = [
  { key: 'auto_cut_done', label: '默认裁完', desc: '编菲保存后自动置「裁完」' },
  { key: 'revoke_current_month_only', label: '只能撤销当月登记', desc: '撤销登记仅限当月记录' },
  { key: 'hide_scan_price', label: '隐藏扫菲单价', desc: '扫菲确认页不显示单价，防止员工比价' },
  { key: 'filter_zero_tickets', label: '过滤数量为0的菲票', desc: '列表与统计剔除数量为 0 的菲票' },
  { key: 'show_cut_total', label: '显示裁货总数', desc: '生产卡片显示总件数' },
  { key: 'hide_style_total_price', label: '隐藏款式总单价', desc: '款式列表不显示总单价' },
  { key: 'hide_employee_salary', label: '隐藏员工工资', desc: '非本人/非管理员不可见工资' },
  { key: 'forbid_unapproved_register', label: '未审核禁止登记', desc: '入职未审核的员工不能扫菲' },
  { key: 'default_counter_on', label: '默认开启计数', desc: '登记时默认打开计数器模式' },
  { key: 'show_salary_style_image', label: '显示薪酬款式图片', desc: '薪酬明细带款式缩略图' },
  { key: 'personalized_notify', label: '个性化通知', desc: '接收站内/订阅消息通知' }
];

/** 默认打印方式二选一 */
const PRINT_MODES = [
  { value: 'bluetooth', label: '蓝牙打印' },
  { value: 'cloud', label: '云打印' }
];

Page({
  data: {
    rows: [] as SettingRow[],
    isAdmin: false,
    loading: true,
    error: false,
    saving: false
  },

  onShow() {
    if (!requireLogin()) return;
    const app = getApp<IAppOption>();
    this.setData({ isAdmin: !!app.globalData.isAdmin });
    this.load();
  },

  load() {
    this.setData({ loading: true, error: false });
    fetchSettings()
      .then((map: CompanySettings) => {
        this.setData({ rows: this.buildRows(map), loading: false });
      })
      .catch(() => {
        this.setData({ loading: false, error: true });
      });
  },

  buildRows(map: CompanySettings): SettingRow[] {
    const rows: SettingRow[] = SWITCH_ROWS.map((r) => ({
      ...r,
      kind: 'switch' as const,
      checked: map[r.key] === '1',
      valueText: ''
    }));
    // 默认打印方式（二选一，非开关）
    const mode = map['default_print_mode'] === 'bluetooth' ? 'bluetooth' : 'cloud';
    rows.push({
      key: 'default_print_mode',
      label: '默认打印方式',
      desc: '菲票打印走蓝牙打印机或云打印平台',
      kind: 'choice',
      checked: false,
      valueText: mode === 'cloud' ? '云打印' : '蓝牙打印'
    });
    // 蓝牙打印管理：入口行 → 打印管理页（扫描绑定 / 测试打印 / 补打队列）
    rows.push({
      key: 'bluetooth_printer',
      label: '蓝牙打印管理',
      desc: '连接蓝牙打印机、测试打印、补打队列',
      kind: 'link',
      checked: false,
      valueText: ''
    });
    // 云打印：灰置，平台接入后开放
    rows.push({
      key: 'cloud_printer',
      label: '云打印',
      desc: '云打印机管理（即将支持，当前请使用蓝牙打印）',
      kind: 'link',
      checked: false,
      valueText: '',
      disabled: true
    });
    return rows;
  },

  /** 开关切换：乐观更新，失败回滚 */
  onToggle(e: WechatMiniprogram.SwitchChange) {
    const key = e.currentTarget.dataset.key as string;
    const checked = e.detail.value;
    const index = this.data.rows.findIndex((r) => r.key === key);
    if (index < 0) return;
    if (!this.data.isAdmin) {
      wx.showToast({ title: '仅管理员可修改', icon: 'none' });
      return;
    }
    this.setData({ [`rows[${index}].checked`]: checked });
    saveSettings({ [key]: checked ? '1' : '0' })
      .then(() => {
        wx.showToast({ title: '已保存', icon: 'none' });
      })
      .catch(() => {
        this.setData({ [`rows[${index}].checked`]: !checked });
      });
  },

  /** 行点击分发：choice 行选打印方式，link 行进打印管理 / 云打印（灰置） */
  onRowTap(e: WechatMiniprogram.TouchEvent) {
    const kind = e.currentTarget.dataset.kind as SettingRow['kind'];
    const key = e.currentTarget.dataset.key as string;
    if (kind === 'choice') {
      this.onChoosePrintMode();
    } else if (kind === 'link') {
      if (e.currentTarget.dataset.disabled) {
        wx.showToast({ title: '云打印即将支持，当前请使用蓝牙打印', icon: 'none' });
      } else if (key === 'bluetooth_printer') {
        wx.navigateTo({ url: '/packages/admin/cloud-printer/index' });
      } else {
        this.onGoCloudPrinter();
      }
    }
  },

  /** 默认打印方式：蓝牙 / 云打印 二选一 */
  onChoosePrintMode() {
    if (!this.data.isAdmin) {
      wx.showToast({ title: '仅管理员可修改', icon: 'none' });
      return;
    }
    const index = this.data.rows.findIndex((r) => r.key === 'default_print_mode');
    wx.showActionSheet({
      itemList: PRINT_MODES.map((m) => m.label),
      success: (res) => {
        const mode = PRINT_MODES[res.tapIndex];
        if (!mode) return;
        this.setData({ [`rows[${index}].valueText`]: mode.label });
        saveSettings({ default_print_mode: mode.value })
          .then(() => {
            wx.showToast({ title: '已保存', icon: 'none' });
          })
          .catch(() => {
            this.setData({
              [`rows[${index}].valueText`]: mode.value === 'cloud' ? '蓝牙打印' : '云打印'
            });
          });
      }
    });
  },

  /** 云打印行 → 云打印机管理页 */
  onGoCloudPrinter() {
    wx.navigateTo({ url: '/packages/admin/cloud-printer/index' });
  },

  onRetry() {
    this.load();
  }
});
