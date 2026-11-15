/**
 * 自定义 tabBar 逻辑。
 * 页面 onShow 中需调用 this.getTabBar().setSelected(n) 同步高亮：
 *   首页 0 / 扫菲 1 / 通知 2 / 我的 3 / AI问报=中央 FAB（无 tab 项，传 -1 清空高亮）。
 * 工序页已撤出 tabBar（2026-10-26），入口移至首页工作台宫格。
 */
Component({
  data: {
    selected: 0,
    unread: 0,
    leftItems: [
      { key: 'index', text: '首页', path: '/pages/index/index', index: 0 },
      { key: 'scan', text: '扫菲', path: '/pages/scan/index', index: 1 }
    ],
    rightItems: [
      { key: 'notifications', text: '通知', path: '/pages/notifications/index', index: 2 },
      { key: 'profile', text: '我的', path: '/pages/profile/index', index: 3 }
    ]
  },

  methods: {
    /** 页面 onShow 调用：同步高亮索引 */
    setSelected(index: number) {
      this.setData({ selected: index });
    },

    /** 通知页拉到未读数后调用 */
    setUnread(count: number) {
      this.setData({ unread: count });
    },

    onTap(e: WechatMiniprogram.TouchEvent) {
      const { path, index } = e.currentTarget.dataset as { path: string; index: number };
      if (this.data.selected === index) {
        return;
      }
      wx.switchTab({ url: path });
    },

    /** 中央 FAB → AI问报页（tab 页，switchTab 进入对话流） */
    onFab() {
      wx.switchTab({ url: '/pages/ai-report/index' });
    }
  }
});
