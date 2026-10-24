/**
 * 自定义 tabBar 逻辑。
 * 页面 onShow 中需调用 this.getTabBar().setSelected(n) 同步高亮：
 *   首页 0 / 工序 1 / 扫菲 2 / 通知 3 / 我的 4
 */
Component({
  data: {
    selected: 0,
    unread: 0,
    leftItems: [
      { key: 'index', text: '首页', path: '/pages/index/index', index: 0 },
      { key: 'process', text: '工序', path: '/pages/process/index', index: 1 }
    ],
    rightItems: [
      { key: 'notifications', text: '通知', path: '/pages/notifications/index', index: 3 },
      { key: 'profile', text: '我的', path: '/pages/profile/index', index: 4 }
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

    /** 中央 FAB → 扫菲页（tab 页，switchTab 进全屏扫码） */
    onScan() {
      wx.switchTab({ url: '/pages/scan/index' });
    }
  }
});
