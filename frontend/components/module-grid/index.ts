/**
 * 首页模块宫格。传入全量模块数组，组件内部按 visible 过滤（配置驱动，禁止硬编码，规格 §4.2）。
 * 管理员 15 项 / 员工仅「我的薪酬」「工作中」——由页面按 isAdmin 传不同数组。
 */
export interface ModuleItem {
  key: string;
  name: string;
  /** 模块图标（当前为语义 emoji；换 PNG/iconfont 时改页面模块数组的 icon 字段即可） */
  icon: string;
  path: string;
  /** 是否 tab 页（switchTab 与 navigateTo 之分） */
  isTab?: boolean;
  visible: boolean;
}

const TAB_PATHS = [
  '/pages/index/index', '/pages/scan/index',
  '/pages/notifications/index', '/pages/profile/index', '/pages/ai-report/index'
];

Component({
  properties: {
    modules: { type: Array, value: [] as ModuleItem[] }
  },

  data: {
    visible: [] as ModuleItem[]
  },

  observers: {
    modules(list: ModuleItem[]) {
      this.setData({ visible: (list || []).filter((m) => m.visible !== false) });
    }
  },

  methods: {
    onTap(e: WechatMiniprogram.TouchEvent) {
      const { path } = e.currentTarget.dataset as { path: string };
      if (!path) return;
      if (TAB_PATHS.includes(path)) {
        wx.switchTab({ url: path });
      } else {
        wx.navigateTo({ url: path });
      }
    }
  }
});
